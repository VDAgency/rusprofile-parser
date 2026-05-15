"""Stage 3 — ИИ-квалификация компании.

Вызывается только если Stage 2 (keyword-скоринг) вернул ``needs_llm``.
Промпт строится динамически из профиля и текста сайта компании.

См. ТЗ v3, раздел 11.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.ai.llm_client import AsyncLLMClient, LLMResult
from src.db.models import AIStatus

logger = logging.getLogger(__name__)


@dataclass
class QualifyLLMResult:
    """Результат Stage 3 — итог квалификации компании через LLM."""

    status: str           # AIStatus.value (hot / cold / unknown)
    comment: str          # ≤ 200 символов
    signals: list[str]    # ≤ 5 элементов
    hook: str             # ≤ 100 символов
    tokens_used: int
    error: str | None = None


_PROMPT_CACHE: dict[str, str] = {}


def _load_prompt(path: str) -> str:
    """Кеш-load промпта из файла."""
    if path in _PROMPT_CACHE:
        return _PROMPT_CACHE[path]
    p = Path(path)
    if not p.is_absolute():
        from src.config import BASE_DIR
        p = BASE_DIR / p
    if not p.exists():
        logger.warning("Prompt file not found: %s — fallback на встроенный", p)
        _PROMPT_CACHE[path] = ""
        return ""
    text = p.read_text(encoding="utf-8")
    _PROMPT_CACHE[path] = text
    return text


def _bullets(items: list[str] | None) -> str:
    if not items:
        return "— (не задано)"
    return "\n".join(f"- {x}" for x in items)


def _csv(items: list[str] | None) -> str:
    if not items:
        return "(нет)"
    return ", ".join(items)


def build_user_prompt(
    *,
    template: str,
    company_name: str,
    company_region: str | None,
    company_okved: str | None,
    icp_description: str,
    semantic_criteria: list[str] | None,
    keyword_matches_positive: list[str] | None,
    keyword_matches_negative: list[str] | None,
    website_text: str,
) -> str:
    """Подставляет переменные в шаблон промпта.

    Использует ``str.format_map`` с защитой: если шаблон содержит
    неизвестную переменную — оставляет как есть, не падает.
    """
    class _SafeDict(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    return template.format_map(_SafeDict(
        company_name=company_name or "",
        company_region=company_region or "—",
        company_okved=company_okved or "—",
        icp_description=icp_description or "",
        semantic_criteria_bulleted=_bullets(semantic_criteria),
        keyword_matches_positive=_csv(keyword_matches_positive),
        keyword_matches_negative=_csv(keyword_matches_negative),
        website_text=website_text or "",
    ))


async def qualify_company_with_llm(
    *,
    client: AsyncLLMClient,
    model: str,
    prompt_template: str,
    company_name: str,
    company_region: str | None,
    company_okved: str | None,
    icp_description: str,
    semantic_criteria: list[str] | None,
    keyword_matches_positive: list[str] | None,
    keyword_matches_negative: list[str] | None,
    website_text: str,
    max_tokens: int = 400,
    temperature: float = 0.2,
) -> QualifyLLMResult:
    """LLM Call №2: оценка компании по профилю.

    Если LLM вернул не-JSON или ошибку → ``status="unknown"``,
    ``error`` заполнен. Никогда не падает.
    """
    user_prompt = build_user_prompt(
        template=prompt_template,
        company_name=company_name,
        company_region=company_region,
        company_okved=company_okved,
        icp_description=icp_description,
        semantic_criteria=semantic_criteria,
        keyword_matches_positive=keyword_matches_positive,
        keyword_matches_negative=keyword_matches_negative,
        website_text=website_text,
    )

    # System prompt — короткий, основная инструкция в user (так удобнее
    # для динамической подстановки).
    system_prompt = (
        "Ты — аналитик продаж. Отвечай строго в JSON формате, без markdown."
    )

    result: LLMResult = await client.complete_json(
        system=system_prompt,
        user=user_prompt,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        force_json=True,
    )

    if not result.ok:
        return QualifyLLMResult(
            status=AIStatus.UNKNOWN.value,
            comment="Ошибка вызова LLM",
            signals=[],
            hook="",
            tokens_used=result.tokens_used,
            error=result.error,
        )

    data = result.content_dict
    if not isinstance(data, dict):
        logger.info(
            "qualify LLM returned non-JSON: %r…", (result.content or "")[:200]
        )
        return QualifyLLMResult(
            status=AIStatus.UNKNOWN.value,
            comment="Ошибка разбора ответа",
            signals=[],
            hook="",
            tokens_used=result.tokens_used,
            error="non_json_response",
        )

    return _normalize_llm_output(data, tokens_used=result.tokens_used)


def _normalize_llm_output(data: dict, *, tokens_used: int) -> QualifyLLMResult:
    """Приводит ответ LLM к QualifyLLMResult с обрезкой длинных полей."""
    status_raw = str(data.get("status", "")).strip().lower()
    if status_raw not in (s.value for s in AIStatus):
        # Иногда LLM выдаёт "warm" / "hot lead" / "yes" — нормализуем по prefix.
        if status_raw.startswith("hot") or status_raw in ("yes", "lead", "warm"):
            status = AIStatus.HOT.value
        elif status_raw.startswith("cold") or status_raw in ("no", "reject"):
            status = AIStatus.COLD.value
        else:
            status = AIStatus.UNKNOWN.value
    else:
        status = status_raw

    comment = str(data.get("comment") or "")[:200].strip()
    hook = str(data.get("hook") or "")[:100].strip()

    signals_raw: Any = data.get("signals") or []
    if not isinstance(signals_raw, list):
        signals_raw = [str(signals_raw)]
    signals = [str(s)[:200] for s in signals_raw[:5] if s]

    return QualifyLLMResult(
        status=status,
        comment=comment,
        signals=signals,
        hook=hook,
        tokens_used=tokens_used,
    )


# ---------------------------------------------------------------------------
# Удобный helper, тянущий prompt из конфига
# ---------------------------------------------------------------------------


async def qualify_company_default(
    *,
    client: AsyncLLMClient,
    company_name: str,
    company_region: str | None,
    company_okved: str | None,
    icp_description: str,
    semantic_criteria: list[str] | None,
    keyword_matches_positive: list[str] | None,
    keyword_matches_negative: list[str] | None,
    website_text: str,
) -> QualifyLLMResult:
    """Обёртка: model + prompt + max_tokens из ``src.config``."""
    from src import config
    template = _load_prompt(config.AI_PROMPT_QUALIFY_FILE)
    if not template:
        return QualifyLLMResult(
            status=AIStatus.UNKNOWN.value,
            comment="Промпт-файл не найден",
            signals=[], hook="", tokens_used=0,
            error="prompt_file_missing",
        )
    return await qualify_company_with_llm(
        client=client,
        model=config.AI_MODEL_QUALIFY,
        prompt_template=template,
        company_name=company_name,
        company_region=company_region,
        company_okved=company_okved,
        icp_description=icp_description,
        semantic_criteria=semantic_criteria,
        keyword_matches_positive=keyword_matches_positive,
        keyword_matches_negative=keyword_matches_negative,
        website_text=website_text,
        max_tokens=config.AI_MAX_TOKENS_QUALIFY,
        temperature=config.AI_TEMPERATURE,
    )
