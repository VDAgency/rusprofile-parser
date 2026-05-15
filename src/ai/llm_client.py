"""Тонкая обёртка над OpenAI / OpenRouter SDK.

Поддерживает оба провайдера через ``base_url``: OpenRouter совместим с
OpenAI Python SDK. Делает 1 retry при 429/5xx с exponential backoff.

Использование:

    client = AsyncLLMClient.from_env()
    result = await client.complete_json(
        system="Ты аналитик ...",
        user="Бриф клиента: ...",
        model="gpt-4o-mini",
        max_tokens=400,
        temperature=0.2,
    )
    # result.content_dict — распарсенный JSON
    # result.tokens_used — сумма prompt + completion
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass
class LLMResult:
    """Результат вызова LLM."""

    content: str                         # сырой текст ответа
    content_dict: dict | None            # распарсенный JSON (None если не JSON)
    tokens_input: int                    # prompt токены
    tokens_output: int                   # completion токены
    model: str                           # имя модели
    error: str | None = None             # текст ошибки, если запрос упал

    @property
    def tokens_used(self) -> int:
        return self.tokens_input + self.tokens_output

    @property
    def ok(self) -> bool:
        return self.error is None


class AsyncLLMClient:
    """Клиент к OpenAI / OpenRouter API.

    Не падает при сетевых ошибках или невалидном JSON — возвращает
    LLMResult с ``error != None`` и ``content_dict = None``. Зона
    ответственности вызывающего — решить, что делать (retry, fallback,
    `ai_status="unknown"`).
    """

    def __init__(
        self,
        api_key: str,
        provider: str = "openai",
        base_url: str | None = None,
        timeout_s: float = 30.0,
        max_retries: int = 1,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries

        # Импортим openai лениво, чтобы модуль грузился даже без ключа.
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout_s}
        if base_url:
            kwargs["base_url"] = base_url
        elif provider == "openrouter":
            kwargs["base_url"] = OPENROUTER_BASE_URL

        self._client = AsyncOpenAI(**kwargs)

    # ─── Конструкторы из конфига ──────────────────────────────────────

    @classmethod
    def from_env(cls) -> "AsyncLLMClient | None":
        """Создаёт клиент из ``src.config``. None, если ключа нет."""
        from src import config

        provider = (config.AI_PROVIDER or "openai").strip().lower()
        if provider == "openrouter":
            key = config.OPENROUTER_API_KEY
            base_url = OPENROUTER_BASE_URL
        else:
            key = config.OPENAI_API_KEY
            base_url = None

        if not key:
            logger.info(
                "AI provider %r не настроен (нет ключа в .env), LLM-вызовы недоступны",
                provider,
            )
            return None
        return cls(api_key=key, provider=provider, base_url=base_url)

    # ─── Основной метод: complete_json ───────────────────────────────

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 400,
        temperature: float = 0.2,
        force_json: bool = True,
    ) -> LLMResult:
        """Запрос с попыткой получить JSON-ответ.

        Если ``force_json=True`` — указывает ``response_format=json_object``
        (поддерживается OpenAI gpt-4o*, gpt-3.5-turbo-1106+, многими
        моделями OpenRouter).
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if force_json:
            kwargs["response_format"] = {"type": "json_object"}

        attempt = 0
        backoff = 5.0
        last_error: str | None = None
        while True:
            try:
                resp = await self._client.chat.completions.create(**kwargs)
            except Exception as e:  # noqa: BLE001
                last_error = f"{type(e).__name__}: {e}"
                attempt += 1
                if attempt > self.max_retries:
                    logger.warning(
                        "LLM call failed after %d attempts: %s",
                        attempt, last_error,
                    )
                    return LLMResult(
                        content="", content_dict=None,
                        tokens_input=0, tokens_output=0,
                        model=model, error=last_error,
                    )
                logger.info(
                    "LLM retry %d/%d after error: %s (sleep %.1fs)",
                    attempt, self.max_retries, last_error, backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= 3
                continue
            break

        # Извлекаем поля ответа.
        try:
            choice = resp.choices[0]
            content = choice.message.content or ""
        except (IndexError, AttributeError) as e:  # noqa: BLE001
            return LLMResult(
                content="", content_dict=None,
                tokens_input=0, tokens_output=0,
                model=model, error=f"malformed_response: {e}",
            )

        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        completion_tokens = (
            int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        )

        content_dict = _parse_json_loose(content)
        return LLMResult(
            content=content,
            content_dict=content_dict,
            tokens_input=prompt_tokens,
            tokens_output=completion_tokens,
            model=model,
        )


# ---------------------------------------------------------------------------
# Парсинг JSON с фоллбэком на regex (LLM иногда возвращает markdown).
# ---------------------------------------------------------------------------

_MARKDOWN_JSON_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE
)


def _parse_json_loose(text: str) -> dict | None:
    """Парсит JSON. Если LLM обернул его в ```json ... ``` — извлекает."""
    if not text:
        return None
    text = text.strip()
    # Чистый JSON
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except (ValueError, TypeError):
        pass
    # Markdown-обёртка
    m = _MARKDOWN_JSON_RE.search(text)
    if m:
        try:
            v = json.loads(m.group(1))
            return v if isinstance(v, dict) else None
        except (ValueError, TypeError):
            return None
    # Попытка найти первый { ... } в тексте.
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        try:
            v = json.loads(text[first_brace:last_brace + 1])
            return v if isinstance(v, dict) else None
        except (ValueError, TypeError):
            return None
    return None
