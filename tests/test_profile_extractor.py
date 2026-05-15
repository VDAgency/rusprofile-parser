"""Тесты Stage A — извлечение профиля из брифа (с моком LLM)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ai.llm_client import AsyncLLMClient
from src.ai.profile_extractor import (
    ExtractedProfile,
    _normalize_keyword_list,
    extract_profile_from_brief,
)


PROMPT = "Бриф клиента:\n---\n{brief}\n---\nВерни JSON."


def _client_with_response(content: str):
    client = AsyncLLMClient(api_key="test-key", provider="openai", max_retries=0)
    fake = MagicMock()
    fake.choices = [MagicMock(message=MagicMock(content=content))]
    fake.usage = MagicMock(prompt_tokens=200, completion_tokens=100)
    client._client.chat.completions.create = AsyncMock(return_value=fake)
    return client


# ─── _normalize_keyword_list ───────────────────────────────────────────


def test_normalize_kws_lowercase_dedup():
    assert _normalize_keyword_list(["ОПТ", "опт", "Опт"]) == ["опт"]


def test_normalize_kws_strips():
    assert _normalize_keyword_list(["  опт  ", "склад"]) == ["опт", "склад"]


def test_normalize_kws_yo_to_e():
    assert _normalize_keyword_list(["партнёр"]) == ["партнер"]


def test_normalize_kws_ignores_empty():
    assert _normalize_keyword_list(["", "  ", None]) == []


def test_normalize_kws_caps_max():
    items = [str(i) for i in range(50)]
    assert len(_normalize_keyword_list(items, max_items=10)) == 10


def test_normalize_kws_handles_non_list():
    assert _normalize_keyword_list("один") == ["один"]
    assert _normalize_keyword_list(None) == []


# ─── extract_profile_from_brief ────────────────────────────────────────


@pytest.mark.anyio
async def test_extract_profile_success():
    client = _client_with_response("""
    {
        "icp_description": "Оптовая B2B-компания.",
        "semantic_criteria": ["работает в B2B", "имеет склад"],
        "keywords_positive": ["опт", "склад", "B2B"],
        "keywords_negative": ["розница"]
    }
    """)
    data = await extract_profile_from_brief(
        client=client, brief="Я продаю сувениры", prompt_template=PROMPT,
        model="gpt-4o-mini",
    )
    assert data.ok is True
    assert data.icp_description == "Оптовая B2B-компания."
    assert data.semantic_criteria == ["работает в b2b", "имеет склад"]
    assert "опт" in data.keywords_positive
    assert "склад" in data.keywords_positive
    assert "b2b" in data.keywords_positive
    assert data.keywords_negative == ["розница"]
    assert data.extraction_tokens_used == 300


@pytest.mark.anyio
async def test_extract_profile_empty_brief():
    client = _client_with_response("{}")
    data = await extract_profile_from_brief(
        client=client, brief="", prompt_template=PROMPT, model="m",
    )
    assert data.ok is False
    assert data.error == "empty_brief"


@pytest.mark.anyio
async def test_extract_profile_non_json():
    client = _client_with_response("просто текст без JSON")
    data = await extract_profile_from_brief(
        client=client, brief="что-то", prompt_template=PROMPT, model="m",
    )
    assert data.ok is False
    assert data.error == "non_json_response"


@pytest.mark.anyio
async def test_extract_profile_llm_error():
    client = AsyncLLMClient(api_key="k", provider="openai", max_retries=0)
    client._client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("api down")
    )
    data = await extract_profile_from_brief(
        client=client, brief="что-то", prompt_template=PROMPT, model="m",
    )
    assert data.ok is False
    assert data.error and "api down" in data.error
