"""Stage 0 — универсальный скоринг компаний (без ИИ).

Состоит из двух фаз:

* **Stage 0a — быстрые хард-фильтры**: режут очевидно мёртвые компании
  ДО кросс-обогащения, чтобы не тратить I/O на enrichment.
* **Stage 0b — сорт-скор 0–100**: считается ПОСЛЕ кросс-обогащения, по
  трём группам сигналов A (контактность) / B (платёжеспособность Rusprofile) /
  C (активность Я.Карт). Используется для сортировки в Sheets.

Функции синхронные, без I/O — легко тестировать и кешировать.
См. ТЗ v3, раздел 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from src.db.models import AIStatus, Source

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Группа A — макс 30, группа B — макс 35, группа C — макс 35.
# Если оба источника подтвердились (group B + group C посчитались) — макс 100.
# Только Rusprofile (без Я.Карт) — макс A+B = 65.
# Только Я.Карты (без Rusprofile) — макс A+C = 65.
GROUP_A_MAX = 30
GROUP_B_MAX = 35
GROUP_C_MAX = 35

# Статусы компании, при которых Stage 0a отсекает её.
_DEAD_STATUS_KEYWORDS = (
    "ликвидируется",
    "ликвидирована",
    "ликвидация",
    "банкротство",
    "банкрот",
    "недействующее",
)

# Я.Карты operating_status, при которых компания мертва.
_DEAD_YANDEX_STATUSES = (
    "permanently_closed",
    "temporarily_closed",
)


# ---------------------------------------------------------------------------
# Результат
# ---------------------------------------------------------------------------


@dataclass
class ScoreResult:
    """Результат скоринга компании."""

    score_raw: int
    score_max_possible: int
    score_normalized: int  # 0..100
    hard_filter_passed: bool
    kill_reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def is_skipped(self) -> bool:
        return not self.hard_filter_passed


# ---------------------------------------------------------------------------
# Вспомогательные геттеры (компания может быть SQLAlchemy-моделью или dict-ом
# из тестов; обёртка позволяет не тащить SQL в чистые тесты).
# ---------------------------------------------------------------------------


def _get(company: Any, attr: str, default=None):
    if company is None:
        return default
    if isinstance(company, dict):
        return company.get(attr, default)
    return getattr(company, attr, default)


def _raw(company: Any) -> dict:
    raw = _get(company, "raw_json") or {}
    return raw if isinstance(raw, dict) else {}


def _has_text(value: Any) -> bool:
    return bool(value) and bool(str(value).strip())


def _status_is_dead(status: str | None) -> bool:
    if not status:
        return False
    s = str(status).strip().lower()
    return any(kw in s for kw in _DEAD_STATUS_KEYWORDS)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Stage 0a — быстрые хард-фильтры
# ---------------------------------------------------------------------------


def score_company_early(company: Any) -> ScoreResult:
    """Быстрый kill-фильтр перед кросс-обогащением.

    Возвращает ScoreResult с ``hard_filter_passed=False``, если:
    * статус юр. лица — ликвидация / банкротство;
    * Я.Карты operating_status в чёрном списке;
    * у компании нет ни сайта, ни телефона, ни email;
    * raw_json.invalid_address == True.
    """
    kill_reasons: list[str] = []

    if _status_is_dead(_get(company, "status")):
        kill_reasons.append(f"status:{_get(company, 'status')!r}")

    yandex_status = _get(company, "yandex_operating_status")
    if yandex_status in _DEAD_YANDEX_STATUSES:
        kill_reasons.append(f"yandex_operating:{yandex_status}")

    if _raw(company).get("invalid_address") is True:
        kill_reasons.append("invalid_address")

    if (
        not _has_text(_get(company, "site"))
        and not _has_text(_get(company, "phone"))
        and not _has_text(_get(company, "email"))
    ):
        kill_reasons.append("no_contacts_at_all")

    passed = not kill_reasons
    return ScoreResult(
        score_raw=0,
        score_max_possible=100,
        score_normalized=0,
        hard_filter_passed=passed,
        kill_reasons=kill_reasons,
        signals={"phase": "0a"},
    )


# ---------------------------------------------------------------------------
# Stage 0b — полный сорт-скоринг по 3 группам
# ---------------------------------------------------------------------------


def _group_a_contactability(company: Any) -> tuple[int, list[str]]:
    """Группа A — каналы связи. Макс 30.

    Все компании всегда проходят группу A (требует только данных
    парсера). Считаем Telegram/VK/WhatsApp по эвристикам в `raw_json.socials`
    или в полях `phone` / `site`.
    """
    pts = 0
    signals: list[str] = []

    if _has_text(_get(company, "site")):
        pts += 10
        signals.append("site:+10")
    if _has_text(_get(company, "phone")):
        pts += 5
        signals.append("phone:+5")
    if _has_text(_get(company, "email")):
        pts += 5
        signals.append("email:+5")

    socials = _raw(company).get("socials") or {}
    extra_channels = 0
    for ch, weight in (
        ("telegram", 2),
        ("vk", 2),
        ("whatsapp", 2),
        ("instagram", 2),
    ):
        if socials.get(ch):
            extra_channels += weight
            signals.append(f"{ch}:+{weight}")
    # Капим A в 30.
    pts += min(extra_channels, GROUP_A_MAX - 20)
    return min(pts, GROUP_A_MAX), signals


def _group_b_solvency(company: Any) -> tuple[int, list[str]] | None:
    """Группа B — платёжеспособность по Rusprofile. Макс 35.

    Возвращает None, если данных Rusprofile в компании нет
    (источник Я.Карты и кросс-обогащение не сработало). В этом случае
    группа B не учитывается в `score_max_possible`.
    """
    has_rusprofile_data = (
        _has_text(_get(company, "inn"))
        or _has_text(_get(company, "ogrn"))
        or _has_text(_get(company, "status"))
        or _has_text(_get(company, "revenue"))
    )
    if not has_rusprofile_data:
        return None

    pts = 0
    signals: list[str] = []

    status = _get(company, "status")
    if status and "действующая" in str(status).lower():
        pts += 10
        signals.append("status:active:+10")

    raw = _raw(company)
    revenue_value = _to_float(raw.get("finance_revenue"))
    if revenue_value is not None or _has_text(_get(company, "revenue")):
        pts += 5
        signals.append("revenue_known:+5")
        if revenue_value is not None and revenue_value >= 5_000_000:
            pts += 5
            signals.append("revenue>=5M:+5")
        if revenue_value is not None and revenue_value >= 50_000_000:
            pts += 5
            signals.append("revenue>=50M:+5")

    profit_value = _to_float(raw.get("finance_profit"))
    if profit_value is not None and profit_value > 0:
        pts += 5
        signals.append("profit>0:+5")

    sshr = _to_int(raw.get("sshr") or raw.get("employees_count"))
    if sshr is not None and sshr > 5:
        pts += 5
        signals.append("employees>5:+5")

    return min(pts, GROUP_B_MAX), signals


def _group_c_yandex_activity(
    company: Any, today: date | None = None
) -> tuple[int, list[str]] | None:
    """Группа C — активность по Я.Картам. Макс 35.

    Возвращает None, если Я.Карты-сигналов нет совсем (нет ни рейтинга,
    ни URL, ни yandex_url) — тогда не учитываем в макс.
    """
    today = today or date.today()
    rating = _to_float(_get(company, "yandex_rating"))
    reviews_count = _to_int(_get(company, "yandex_reviews_count"))
    yandex_url = _get(company, "yandex_url")
    last_review = _get(company, "yandex_last_review_date")

    if rating is None and reviews_count is None and not yandex_url and last_review is None:
        return None

    pts = 0
    signals: list[str] = []

    if yandex_url:
        pts += 5
        signals.append("yandex_card_exists:+5")

    if rating is not None and rating >= 4.0:
        pts += 5
        signals.append("rating>=4.0:+5")

    if reviews_count is not None and reviews_count >= 10:
        pts += 5
        signals.append("reviews>=10:+5")

    if last_review:
        if isinstance(last_review, date):
            age_days = (today - last_review).days
            if age_days < 90:
                pts += 10
                signals.append("last_review<3mo:+10")
            elif age_days < 365:
                pts += 5
                signals.append("last_review<12mo:+5")
            elif age_days < 730:
                pts += 0
                signals.append("last_review<24mo:+0")
            # > 24 месяцев — поздний хард-фильтр, обработается в score_company_full.

    if _get(company, "yandex_hours_filled"):
        pts += 5
        signals.append("hours_filled:+5")
    if _get(company, "yandex_coordinates_filled"):
        pts += 5
        signals.append("coordinates_filled:+5")

    return min(pts, GROUP_C_MAX), signals


def _late_hard_filters(company: Any, today: date | None = None) -> list[str]:
    """Поздние хард-фильтры — выполняются после скоринга, могут перевести
    компанию в skip даже с ненулевым баллом.

    * Я.Карты-карточка >24 мес без свежих отзывов → skip (мёртвая точка).
    * 0 отзывов и карточка существует > 12 мес → skip (никто не ходит).
    """
    today = today or date.today()
    reasons: list[str] = []

    last_review = _get(company, "yandex_last_review_date")
    yandex_url = _get(company, "yandex_url")

    if yandex_url and isinstance(last_review, date):
        age = (today - last_review).days
        if age > 730:
            reasons.append("yandex_card_inactive>24mo")

    reviews_count = _to_int(_get(company, "yandex_reviews_count"))
    if yandex_url and reviews_count == 0:
        # Если карточка существует, но отзывов нет — пометка слабого сигнала.
        # Без даты создания карточки строгого «>12мес» проверить нельзя,
        # поэтому ставим менее агрессивно: только если нет вообще никакой
        # активности (rating нулевой/отсутствует).
        rating = _to_float(_get(company, "yandex_rating"))
        if rating is None or rating == 0:
            reasons.append("yandex_card_zero_engagement")

    return reasons


def score_company_full(
    company: Any, today: date | None = None
) -> ScoreResult:
    """Полный скоринг (Stage 0b) — после кросс-обогащения.

    Считает 3 группы сигналов, выбирает применимый максимум, нормализует
    балл в 0..100. Применяет поздние хард-фильтры.
    """
    today = today or date.today()
    early = score_company_early(company)
    if not early.hard_filter_passed:
        # Уже отсечена в Stage 0a.
        return early

    a_pts, a_signals = _group_a_contactability(company)
    b = _group_b_solvency(company)
    c = _group_c_yandex_activity(company, today=today)

    score_raw = a_pts
    max_possible = GROUP_A_MAX
    signals: dict[str, Any] = {"phase": "0b", "group_a": a_signals}

    if b is not None:
        b_pts, b_signals = b
        score_raw += b_pts
        max_possible += GROUP_B_MAX
        signals["group_b"] = b_signals
    if c is not None:
        c_pts, c_signals = c
        score_raw += c_pts
        max_possible += GROUP_C_MAX
        signals["group_c"] = c_signals

    score_normalized = (
        int(round(score_raw / max_possible * 100)) if max_possible > 0 else 0
    )

    late_kill = _late_hard_filters(company, today=today)
    if late_kill:
        return ScoreResult(
            score_raw=score_raw,
            score_max_possible=max_possible,
            score_normalized=score_normalized,
            hard_filter_passed=False,
            kill_reasons=late_kill,
            signals=signals,
        )

    return ScoreResult(
        score_raw=score_raw,
        score_max_possible=max_possible,
        score_normalized=score_normalized,
        hard_filter_passed=True,
        kill_reasons=[],
        signals=signals,
    )


# ---------------------------------------------------------------------------
# Удобный helper для qualify_service: применить результат скоринга к Company
# ---------------------------------------------------------------------------


def apply_score_to_company(company: Any, result: ScoreResult) -> None:
    """Записывает результат в поля Company.

    Не коммитит — это зона вызывающего.
    """
    company.ai_score = result.score_normalized
    if not result.hard_filter_passed:
        company.ai_status = AIStatus.SKIP.value
        company.ai_comment = "; ".join(result.kill_reasons)[:200]
