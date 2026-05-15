"""Тесты тонкой обёртки над OpenAI/OpenRouter SDK.

Без реальных вызовов LLM — патчим _client.chat.completions.create.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ai.llm_client import (
    AsyncLLMClient,
    LLMResult,
    _parse_json_loose,
)


# ─── _parse_json_loose ────────────────────────────────────────────────────


def test_parse_clean_json():
    assert _parse_json_loose('{"x": 1}') == {"x": 1}


def test_parse_markdown_wrapped():
    text = '```json\n{"status": "hot", "comment": "ok"}\n```'
    assert _parse_json_loose(text) == {"status": "hot", "comment": "ok"}


def test_parse_lowercase_json_block():
    text = '```\n{"a": 1}\n```'
    assert _parse_json_loose(text) == {"a": 1}


def test_parse_with_leading_text():
    text = 'Sure, here it is: {"x": 2}'
    assert _parse_json_loose(text) == {"x": 2}


def test_parse_invalid_returns_none():
    assert _parse_json_loose("not json") is None
    assert _parse_json_loose("") is None
    assert _parse_json_loose(None) is None


def test_parse_array_returns_none():
    """Только dict, не array."""
    assert _parse_json_loose("[1,2,3]") is None


# ─── AsyncLLMClient (с моком) ─────────────────────────────────────────────


def _make_client_with_mocked_response(content: str, prompt_tokens=10, completion_tokens=5):
    """Хелпер: создаёт AsyncLLMClient с замоканым chat.completions.create."""
    client = AsyncLLMClient(api_key="test-key", provider="openai")
    fake_response = MagicMock()
    fake_response.choices = [
        MagicMock(message=MagicMock(content=content))
    ]
    fake_response.usage = MagicMock(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    client._client.chat.completions.create = AsyncMock(return_value=fake_response)
    return client


@pytest.mark.anyio
async def test_complete_json_success():
    client = _make_client_with_mocked_response('{"status": "hot"}')
    result = await client.complete_json(
        system="sys", user="usr", model="gpt-4o-mini",
    )
    assert result.ok is True
    assert result.content == '{"status": "hot"}'
    assert result.content_dict == {"status": "hot"}
    assert result.tokens_used == 15


@pytest.mark.anyio
async def test_complete_json_invalid_response():
    client = _make_client_with_mocked_response("not json at all")
    result = await client.complete_json(
        system="sys", user="usr", model="gpt-4o-mini",
    )
    assert result.ok is True  # API не упал
    assert result.content_dict is None  # просто не JSON


@pytest.mark.anyio
async def test_complete_json_api_error_returns_error_result():
    client = AsyncLLMClient(api_key="test-key", provider="openai", max_retries=0)
    client._client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("connection refused")
    )
    result = await client.complete_json(
        system="sys", user="usr", model="gpt-4o-mini",
    )
    assert result.ok is False
    assert result.error and "connection refused" in result.error
    assert result.content == ""
    assert result.tokens_used == 0


def test_from_env_returns_none_without_key(monkeypatch):
    """Без ключа .from_env() должен вернуть None.

    Патчим напрямую атрибуты ``src.config``, потому что load_dotenv
    перетирает env vars из системы значениями из .env-файла (а в проде
    туда уже добавлен реальный ключ).
    """
    import src.config as cfg
    monkeypatch.setattr(cfg, "OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "", raising=False)
    monkeypatch.setattr(cfg, "AI_PROVIDER", "openai", raising=False)
    client = AsyncLLMClient.from_env()
    assert client is None
