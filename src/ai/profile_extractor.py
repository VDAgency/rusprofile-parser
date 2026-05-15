"""Stage A — извлечение ИИ-профиля из брифа клиента (LLM Call №1).

Принимает свободный текст брифа, возвращает структурированный профиль:
``icp_description``, ``semantic_criteria[]``, ``keywords_positive[]``,
``keywords_negative[]``.

Не лезет в БД — это зона ``profile_service``. Здесь только LLM-вызов
и нормализация ответа.

См. ТЗ v3, раздел 8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.ai.llm_client import AsyncLLMClient, LLMResult

logger = logging.getLogger(__name__)


@dataclass
class ExtractedProfile:
    """Извлечённый профиль для сохранения в ai_profiles."""

    icp_description: str
    semantic_criteria: list[str] = field(default_factory=list)
    keywords_positive: list[str] = field(default_factory=list)
    keywords_negative: list[str] = field(default_factory=list)
    extraction_model: str = ""
    extraction_tokens_used: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.icp_description)


_PROMPT_CACHE: dict[str, str] = {}


def _load_prompt(path: str) -> str:
    if path in _PROMPT_CACHE:
        return _PROMPT_CACHE[path]
    p = Path(path)
    if not p.is_absolute():
        from src.config import BASE_DIR
        p = BASE_DIR / p
    if not p.exists():
        logger.warning("Prompt file not found: %s", p)
        _PROMPT_CACHE[path] = ""
        return ""
    text = p.read_text(encoding="utf-8")
    _PROMPT_CACHE[path] = text
    return text


def _normalize_keyword_list(value: Any, *, max_items: int = 30) -> list[str]:
    """Нормализует list[str] из LLM-ответа: lowercase, без пустых, без дублей."""
    if not value:
        return []
    if not isinstance(value, list):
        value = [str(value)]
    out: list[str] = []
    seen = set()
    for item in value:
        # Пропускаем None/пустые значения, не превращаем None в строку "none".
        if item is None:
            continue
        s = str(item).strip().lower().replace("ё", "е")
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


async def extract_profile_from_brief(
    *,
    client: AsyncLLMClient,
    brief: str,
    model: str | None = None,
    prompt_template: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.2,
) -> ExtractedProfile:
    """LLM Call №1: бриф → структурированный профиль.

    Если LLM не отвечает или вернул не-JSON, возвращает ExtractedProfile
    с ``error != None`` и пустыми полями.
    """
    if not brief or not brief.strip():
        return ExtractedProfile(
            icp_description="", error="empty_brief",
        )

    if prompt_template is None:
        from src import config
        prompt_template = _load_prompt(config.AI_PROMPT_EXTRACT_FILE)
        if not prompt_template:
            return ExtractedProfile(
                icp_description="", error="prompt_file_missing",
            )

    if model is None:
        from src import config
        model = config.AI_MODEL_EXTRACT

    user_prompt = prompt_template.replace("{brief}", brief.strip())
    system_prompt = (
        "Ты — аналитик. Отвечай строго в JSON формате, без markdown."
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
        return ExtractedProfile(
            icp_description="",
            extraction_model=model,
            extraction_tokens_used=result.tokens_used,
            error=result.error,
        )

    data = result.content_dict
    if not isinstance(data, dict):
        return ExtractedProfile(
            icp_description="",
            extraction_model=model,
            extraction_tokens_used=result.tokens_used,
            error="non_json_response",
        )

    return ExtractedProfile(
        icp_description=str(data.get("icp_description") or "").strip(),
        semantic_criteria=_normalize_keyword_list(
            data.get("semantic_criteria"), max_items=10
        ),
        keywords_positive=_normalize_keyword_list(
            data.get("keywords_positive"), max_items=30
        ),
        keywords_negative=_normalize_keyword_list(
            data.get("keywords_negative"), max_items=15
        ),
        extraction_model=model,
        extraction_tokens_used=result.tokens_used,
    )
