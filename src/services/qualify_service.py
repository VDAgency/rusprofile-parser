"""Stage E + Stage 0/1/2/3 — оркестратор ИИ-квалификации.

Главная функция: ``qualify_run(run_id, tenant_id, profile_id, ...)``.
Прогоняет все is_new=True компании из ParseRun через конвейер с
тариф-зависимым ветвлением:

* **Simple** (бесплатный): Stage 0a → Stage E → Stage 0b → сохранение.
* **AI** (платный): + Stage 1 → Stage 2 → (опционально) Stage 3.

Логирование каждого решения — JSONL в ``logs/qualify/qualify_{run_id}_{date}.jsonl``.
Агрегаты — в ``ParseRun.ai_qualify_stats``.

См. ТЗ v3, разделы 9 и 13.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ai import keyword_matcher, scoring, website_extractor
from src.ai.llm_client import AsyncLLMClient
from src.ai.qualifier import qualify_company_default
from src.db import get_session
from src.db.models import (
    AIProfile,
    AIStatus,
    Company,
    ParseRun,
    RunCompany,
    TariffPlan,
    Tenant,
)
from src.services import (
    cross_enrichment_service as ces,
    quota_service,
    tariff_helpers,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Структуры
# ---------------------------------------------------------------------------


@dataclass
class QualifyRunStats:
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    decisions_by_stage: dict[str, int] = field(default_factory=dict)
    cross_enrichment_matches: dict[str, int] = field(default_factory=dict)
    llm_calls: int = 0
    tokens_used_total: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost_rub_estimate: float = 0.0
    duration_seconds: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _bump(d: dict[str, int], key: str, n: int = 1) -> None:
    d[key] = d.get(key, 0) + n


# ---------------------------------------------------------------------------
# Логирование решений
# ---------------------------------------------------------------------------


class StageLogger:
    """Накопитель JSONL-лога по одному ParseRun."""

    def __init__(self, run_id: int) -> None:
        from src.config import LOG_DIR
        self.dir = Path(LOG_DIR) / "qualify"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / (
            f"qualify_{run_id}_{datetime.now().strftime('%Y%m%d')}.jsonl"
        )
        self._fh = None

    def _ensure_open(self):
        if self._fh is None:
            self._fh = self.path.open("a", encoding="utf-8")

    def write(self, record: dict) -> None:
        try:
            self._ensure_open()
            self._fh.write(json.dumps(record, ensure_ascii=False, default=_json_default))
            self._fh.write("\n")
            self._fh.flush()
        except Exception as e:  # noqa: BLE001
            logger.warning("StageLogger.write failed: %s", e)

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._fh = None


def _json_default(obj):
    if isinstance(obj, (datetime, _date)):
        return obj.isoformat()
    return str(obj)


# ---------------------------------------------------------------------------
# Получение списка компаний для квалификации
# ---------------------------------------------------------------------------


def _load_run_and_companies(
    session: Session,
    run_id: int,
    tenant_id: int,
    *,
    max_companies: int,
) -> tuple[ParseRun | None, Tenant | None, list[Company]]:
    run = session.scalar(
        select(ParseRun).where(
            ParseRun.id == run_id, ParseRun.tenant_id == tenant_id
        )
    )
    if run is None:
        return None, None, []
    tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        return run, None, []

    rows = session.execute(
        select(Company)
        .join(RunCompany, RunCompany.company_id == Company.id)
        .where(
            RunCompany.run_id == run_id,
            RunCompany.is_new == True,  # noqa: E712
        )
        .order_by(Company.id)
        .limit(max_companies)
    ).scalars().all()
    return run, tenant, list(rows)


# ---------------------------------------------------------------------------
# Один проход pipeline по одной компании
# ---------------------------------------------------------------------------


async def _qualify_one(
    *,
    company: Company,
    tenant: Tenant,
    profile: AIProfile | None,
    llm_client: AsyncLLMClient | None,
    yandex_finder: ces.YandexFinder | None,
    rusprofile_finder: ces.RusprofileFinder | None,
    stats: QualifyRunStats,
    log: StageLogger,
    enable_cross_enrichment: bool,
    today: _date,
) -> None:
    """Полный конвейер для одной компании. Изменяет company in-place,
    обновляет stats, пишет лог."""
    rec: dict = {
        "company_id": company.id,
        "company_name": company.name,
        "source_original": company.source,
        "tariff": tenant.tariff_plan,
        "stages": {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # ─── Stage 0a — быстрые хард-фильтры ─────────────────────────────
    early = scoring.score_company_early(company)
    rec["stages"]["stage_0a_early"] = {
        "hard_filter_passed": early.hard_filter_passed,
        "kill_reasons": early.kill_reasons,
    }
    if not early.hard_filter_passed:
        company.ai_status = AIStatus.SKIP.value
        company.ai_score = 0
        company.ai_comment = "; ".join(early.kill_reasons)[:200]
        company.ai_qualified_at = datetime.now(timezone.utc)
        if profile is not None:
            company.ai_profile_id = profile.id
        _bump(stats.by_status, AIStatus.SKIP.value)
        _bump(stats.decisions_by_stage, "stage_0a_skip")
        rec["final"] = {
            "ai_status": AIStatus.SKIP.value, "ai_score": 0,
            "tokens_used": 0, "decision_path": "stage_0a",
            "quota_check_passed": True,
        }
        log.write(rec)
        return

    # ─── Stage E — кросс-обогащение ──────────────────────────────────
    if enable_cross_enrichment:
        try:
            await ces.enrich_company(
                company,
                yandex_finder=yandex_finder,
                rusprofile_finder=rusprofile_finder,
            )
            rec["stages"]["stage_e_cross"] = {
                "source": company.cross_enrichment_source,
                "confidence": company.cross_match_confidence,
            }
            if company.cross_enrichment_source == "both":
                _bump(stats.cross_enrichment_matches, "both")
            elif company.cross_enrichment_source == "yandex":
                _bump(stats.cross_enrichment_matches, "yandex_found")
            elif company.cross_enrichment_source == "rusprofile":
                _bump(stats.cross_enrichment_matches, "rusprofile_found")
            else:
                _bump(stats.cross_enrichment_matches, "none")
        except Exception as e:  # noqa: BLE001
            logger.warning("Stage E failed for company_id=%s: %s", company.id, e)
            stats.errors.append(f"stage_e:{type(e).__name__}")
            rec["stages"]["stage_e_cross"] = {"error": str(e)[:200]}

    # ─── Stage 0b — полный сорт-скор ─────────────────────────────────
    full = scoring.score_company_full(company, today=today)
    rec["stages"]["stage_0b_full"] = {
        "score_raw": full.score_raw,
        "score_max": full.score_max_possible,
        "score_normalized": full.score_normalized,
        "hard_filter_passed": full.hard_filter_passed,
        "signals": full.signals,
    }
    company.ai_score = full.score_normalized
    if not full.hard_filter_passed:
        company.ai_status = AIStatus.SKIP.value
        company.ai_comment = "; ".join(full.kill_reasons)[:200]
        company.ai_qualified_at = datetime.now(timezone.utc)
        if profile is not None:
            company.ai_profile_id = profile.id
        _bump(stats.by_status, AIStatus.SKIP.value)
        _bump(stats.decisions_by_stage, "stage_0b_skip")
        rec["final"] = {
            "ai_status": AIStatus.SKIP.value,
            "ai_score": full.score_normalized,
            "tokens_used": 0, "decision_path": "stage_0b",
        }
        log.write(rec)
        return

    # Simple-тариф (или Trial/Basic) — не идём в ИИ-стадии. Status
    # остаётся None — это нормально: компания не «горячая/холодная»,
    # она просто скорошена. Sheets отсортирует по ai_score.
    # ИИ доступен для PRO, legacy AI и dev-whitelist.
    if not tariff_helpers.is_ai_available(tenant) or profile is None:
        company.ai_qualified_at = datetime.now(timezone.utc)
        _bump(stats.decisions_by_stage, "simple_done")
        rec["final"] = {
            "ai_status": None,
            "ai_score": full.score_normalized,
            "tokens_used": 0, "decision_path": "simple",
        }
        log.write(rec)
        return

    # ─── Проверка квоты перед платными стадиями ──────────────────────
    quota_service.reset_if_period_expired(
        # session берётся через тот же transaction context, см. qualify_run
        session=_unused_session_arg(), tenant=tenant,
    ) if False else None  # noqa — placeholder, делается в qualify_run периодически

    can_use, reason = quota_service.check_can_use_ai(tenant)
    if not can_use:
        company.ai_status = AIStatus.QUOTA_EXCEEDED.value
        company.ai_comment = f"Месячная квота ИИ исчерпана ({reason})"[:200]
        company.ai_qualified_at = datetime.now(timezone.utc)
        company.ai_profile_id = profile.id
        _bump(stats.by_status, AIStatus.QUOTA_EXCEEDED.value)
        _bump(stats.decisions_by_stage, "quota_exceeded")
        rec["final"] = {
            "ai_status": AIStatus.QUOTA_EXCEEDED.value,
            "ai_score": full.score_normalized,
            "tokens_used": 0, "decision_path": "quota",
            "quota_check_passed": False,
        }
        log.write(rec)
        return

    # ─── Stage 1 — извлечение текста сайта ──────────────────────────
    site_url = (company.site or "").strip()
    if not site_url:
        company.ai_status = AIStatus.COLD.value
        company.ai_comment = "Нет сайта — не можем оценить деятельность"
        company.ai_qualified_at = datetime.now(timezone.utc)
        company.ai_profile_id = profile.id
        _bump(stats.by_status, AIStatus.COLD.value)
        _bump(stats.decisions_by_stage, "stage_1_no_site")
        rec["stages"]["stage_1_website"] = {"fetched": False, "reason": "no_site"}
        rec["final"] = {
            "ai_status": AIStatus.COLD.value,
            "ai_score": full.score_normalized,
            "tokens_used": 0, "decision_path": "stage_1",
        }
        log.write(rec)
        return

    from src.config import (
        WEBSITE_MAX_CHARS,
        WEBSITE_MAX_PAGES,
        WEBSITE_TIMEOUT_S,
    )
    site_result = await website_extractor.extract_website_text(
        site_url,
        timeout_s=WEBSITE_TIMEOUT_S,
        max_pages=WEBSITE_MAX_PAGES,
        max_chars=WEBSITE_MAX_CHARS,
    )
    rec["stages"]["stage_1_website"] = {
        "fetched": site_result.text is not None,
        "pages": site_result.pages_fetched,
        "text_length": len(site_result.text or ""),
        "validation_issue": site_result.validation_issue,
        "duration_ms": site_result.fetch_duration_ms,
    }
    if site_result.validation_issue in ("fetch_failed", "stub"):
        company.ai_status = AIStatus.COLD.value
        company.ai_comment = (
            "Сайт недоступен" if site_result.validation_issue == "fetch_failed"
            else "Сайт-заглушка"
        )
        company.ai_qualified_at = datetime.now(timezone.utc)
        company.ai_profile_id = profile.id
        _bump(stats.by_status, AIStatus.COLD.value)
        _bump(stats.decisions_by_stage, f"stage_1_{site_result.validation_issue}")
        rec["final"] = {
            "ai_status": AIStatus.COLD.value,
            "ai_score": full.score_normalized,
            "tokens_used": 0, "decision_path": "stage_1",
        }
        log.write(rec)
        return

    if site_result.validation_issue == "too_short" or not site_result.text:
        company.ai_status = AIStatus.UNKNOWN.value
        company.ai_comment = "Мало контента на сайте — недостаточно для оценки"
        company.ai_qualified_at = datetime.now(timezone.utc)
        company.ai_profile_id = profile.id
        _bump(stats.by_status, AIStatus.UNKNOWN.value)
        _bump(stats.decisions_by_stage, "stage_1_too_short")
        rec["final"] = {
            "ai_status": AIStatus.UNKNOWN.value,
            "ai_score": full.score_normalized,
            "tokens_used": 0, "decision_path": "stage_1",
        }
        log.write(rec)
        return

    # ─── Stage 2 — keyword-скоринг ──────────────────────────────────
    from src.config import KW_COLD_MIN, KW_HOT_MIN
    kw_res = keyword_matcher.match_keywords(
        site_result.text,
        list(profile.keywords_positive or []),
        list(profile.keywords_negative or []),
        hot_threshold=KW_HOT_MIN,
        cold_threshold=KW_COLD_MIN,
    )
    rec["stages"]["stage_2_keywords"] = {
        "positive_matches": kw_res.positive_matches,
        "negative_matches": kw_res.negative_matches,
        "decision": kw_res.decision,
        "decision_reason": kw_res.decision_reason,
    }
    company.ai_keyword_matches_positive = kw_res.positive_matches
    company.ai_keyword_matches_negative = kw_res.negative_matches

    if kw_res.decision == "hot":
        company.ai_status = AIStatus.HOT.value
        company.ai_comment = f"Keywords: {', '.join(kw_res.positive_matches[:3])}"[:200]
        company.ai_qualified_at = datetime.now(timezone.utc)
        company.ai_profile_id = profile.id
        _bump(stats.by_status, AIStatus.HOT.value)
        _bump(stats.decisions_by_stage, "stage_2_hot")
        rec["final"] = {
            "ai_status": AIStatus.HOT.value,
            "ai_score": full.score_normalized,
            "tokens_used": 0, "decision_path": "stage_2",
        }
        log.write(rec)
        return

    if kw_res.decision == "cold":
        company.ai_status = AIStatus.COLD.value
        company.ai_comment = f"Negative keywords: {', '.join(kw_res.negative_matches[:3])}"[:200]
        company.ai_qualified_at = datetime.now(timezone.utc)
        company.ai_profile_id = profile.id
        _bump(stats.by_status, AIStatus.COLD.value)
        _bump(stats.decisions_by_stage, "stage_2_cold")
        rec["final"] = {
            "ai_status": AIStatus.COLD.value,
            "ai_score": full.score_normalized,
            "tokens_used": 0, "decision_path": "stage_2",
        }
        log.write(rec)
        return

    # ─── Stage 3 — LLM-квалификация ─────────────────────────────────
    if llm_client is None:
        company.ai_status = AIStatus.UNKNOWN.value
        company.ai_comment = "LLM-клиент не настроен"
        company.ai_qualified_at = datetime.now(timezone.utc)
        company.ai_profile_id = profile.id
        _bump(stats.by_status, AIStatus.UNKNOWN.value)
        _bump(stats.decisions_by_stage, "stage_3_no_client")
        rec["stages"]["stage_3_llm"] = {"called": False, "reason": "no_llm_client"}
        rec["final"] = {
            "ai_status": AIStatus.UNKNOWN.value,
            "ai_score": full.score_normalized,
            "tokens_used": 0, "decision_path": "stage_3",
        }
        log.write(rec)
        return

    llm_res = await qualify_company_default(
        client=llm_client,
        company_name=company.name,
        company_region=company.region,
        company_okved=company.okved,
        icp_description=profile.icp_description or "",
        semantic_criteria=list(profile.semantic_criteria or []),
        keyword_matches_positive=kw_res.positive_matches,
        keyword_matches_negative=kw_res.negative_matches,
        website_text=site_result.text or "",
    )
    rec["stages"]["stage_3_llm"] = {
        "called": True,
        "status": llm_res.status,
        "tokens_used": llm_res.tokens_used,
        "error": llm_res.error,
    }

    company.ai_status = llm_res.status
    company.ai_comment = llm_res.comment[:200]
    company.ai_signals = llm_res.signals
    company.ai_hook = llm_res.hook[:100]
    company.ai_tokens_used = (company.ai_tokens_used or 0) + llm_res.tokens_used
    company.ai_qualified_at = datetime.now(timezone.utc)
    company.ai_profile_id = profile.id

    _bump(stats.by_status, llm_res.status)
    _bump(stats.decisions_by_stage, f"stage_3_llm_{llm_res.status}")
    if llm_res.tokens_used:
        stats.llm_calls += 1
        stats.tokens_used_total += llm_res.tokens_used

    rec["final"] = {
        "ai_status": llm_res.status,
        "ai_score": full.score_normalized,
        "tokens_used": llm_res.tokens_used,
        "decision_path": "stage_3",
    }
    log.write(rec)


def _unused_session_arg():  # placeholder для type-hint в выше расположенном фрагменте
    return None


# ---------------------------------------------------------------------------
# Главная функция qualify_run
# ---------------------------------------------------------------------------


async def qualify_run(
    *,
    run_id: int,
    tenant_id: int,
    profile_id: int | None = None,
    enable_cross_enrichment: bool = True,
    yandex_finder: ces.YandexFinder | None = None,
    rusprofile_finder: ces.RusprofileFinder | None = None,
    llm_client: AsyncLLMClient | None = None,
    progress_cb: Callable[[int, int], Awaitable[None]] | None = None,
    force: bool = False,
) -> QualifyRunStats:
    """Основная функция: квалифицирует все is_new=True компании из run.

    Если ``llm_client`` не передан — берётся из ``AsyncLLMClient.from_env()``.
    Если AI-провайдер не настроен — Stage 3 вернёт UNKNOWN, остальные
    стадии работают.
    """
    started = time.monotonic()
    from src.config import (
        AI_COST_PER_1K_INPUT_RUB,
        AI_COST_PER_1K_OUTPUT_RUB,
        QUALIFY_LLM_CONCURRENCY,
        QUALIFY_MAX_COMPANIES_PER_RUN,
        QUALIFY_WEBSITE_CONCURRENCY,
    )

    stats = QualifyRunStats()
    log = StageLogger(run_id)
    today = _date.today()

    if llm_client is None:
        llm_client = AsyncLLMClient.from_env()

    with get_session() as session:
        run, tenant, companies = _load_run_and_companies(
            session, run_id, tenant_id,
            max_companies=QUALIFY_MAX_COMPANIES_PER_RUN,
        )
        if run is None or tenant is None:
            log.close()
            return stats

        # Сбрасываем период квоты, если истёк (для платных ИИ-тарифов
        # и dev-whitelist — у dev'ов тоже период есть для статистики).
        if tariff_helpers.is_ai_available(tenant):
            quota_service.reset_if_period_expired(session, tenant)

        profile = None
        if profile_id is not None:
            profile = session.scalar(
                select(AIProfile).where(
                    AIProfile.id == profile_id,
                    AIProfile.tenant_id == tenant_id,
                )
            )
            if profile is None:
                stats.errors.append(f"profile_id={profile_id} not found")
                logger.warning("qualify_run: profile %s not found", profile_id)

        stats.total = len(companies)
        if not companies:
            log.close()
            return stats

        # Семафор ограничивает только Stage 1 + 3 (где I/O или LLM).
        # Stage 0/2/E — синхронные/быстрые, не нуждаются в ограничении.
        sem = asyncio.Semaphore(max(QUALIFY_LLM_CONCURRENCY, QUALIFY_WEBSITE_CONCURRENCY))

        async def _bound(c: Company):
            async with sem:
                try:
                    await _qualify_one(
                        company=c, tenant=tenant, profile=profile,
                        llm_client=llm_client,
                        yandex_finder=yandex_finder,
                        rusprofile_finder=rusprofile_finder,
                        stats=stats, log=log,
                        enable_cross_enrichment=enable_cross_enrichment,
                        today=today,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("qualify_one for company_id=%s failed: %s", c.id, e)
                    stats.errors.append(f"{type(e).__name__}: {e}")

        # Прогресс-колбек после каждой пачки.
        async def _runner():
            done = 0
            for i in range(0, len(companies), 5):
                batch = companies[i:i + 5]
                await asyncio.gather(*(_bound(c) for c in batch))
                done += len(batch)
                if progress_cb is not None:
                    try:
                        await progress_cb(done, len(companies))
                    except Exception:  # noqa: BLE001
                        pass

        await _runner()

        # Учёт квоты и стоимости — для всех, у кого включён ИИ
        # (платные ИИ-тарифы + dev-whitelist). У dev'а это не для
        # блокировки, а для статистики затрат в Кабинете.
        if tariff_helpers.is_ai_available(tenant) and stats.tokens_used_total:
            quota_service.increment_companies(session, tenant, stats.llm_calls)
            quota_service.increment_tokens(session, tenant, stats.tokens_used_total)

        cost_input = stats.tokens_input * AI_COST_PER_1K_INPUT_RUB / 1000
        cost_output = stats.tokens_output * AI_COST_PER_1K_OUTPUT_RUB / 1000
        stats.cost_rub_estimate = round(cost_input + cost_output, 2)

        stats.duration_seconds = int(time.monotonic() - started)
        run.ai_qualify_enabled = True
        run.ai_qualify_stats = stats.to_dict()
        if profile is not None:
            run.ai_profile_id = profile.id
        run.enable_cross_enrichment = enable_cross_enrichment

        # Финальный коммит происходит автоматически при выходе из get_session().

    log.close()

    if progress_cb is not None:
        try:
            await progress_cb(stats.total, stats.total)
        except Exception:  # noqa: BLE001
            pass

    return stats
