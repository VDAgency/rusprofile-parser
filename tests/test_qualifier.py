"""Тесты Stage 3 — qualifier (с моком LLM)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ai.llm_client import AsyncLLMClient
from src.ai.qualifier import (
    QualifyLLMResult,
    _normalize_llm_output,
    build_user_prompt,
    qualify_company_with_llm,
)
from src.db.models import AIStatus


PROMPT_TEMPLATE = """\
ICP: {icp_description}
Критерии:
{semantic_criteria_bulleted}

Компания: {company_name}
Регион: {company_region}
ОКВЭД: {company_okved}
Позитивные ключи: {keyword_matches_positive}
Негативные ключи: {keyword_matches_negative}

Текст сайта:
{website_text}

Ответь JSON.
"""


def test_build_user_prompt_substitutes_all():
    prompt = build_user_prompt(
        template=PROMPT_TEMPLATE,
        company_name="ООО Ромашка",
        company_region="Москва",
        company_okved="46.49.3",
        icp_description="Оптовая компания",
        semantic_criteria=["крит 1", "крит 2"],
        keyword_matches_positive=["опт", "склад"],
        keyword_matches_negative=[],
        website_text="Тут текст сайта",
    )
    assert "ООО Ромашка" in prompt
    assert "Москва" in prompt
    assert "46.49.3" in prompt
    assert "крит 1" in prompt
    assert "опт, склад" in prompt
    assert "(нет)" in prompt  # пустой negative
    assert "Тут текст сайта" in prompt


def test_build_user_prompt_handles_missing_fields():
    prompt = build_user_prompt(
        template=PROMPT_TEMPLATE,
        company_name="X",
        company_region=None,
        company_okved=None,
        icp_description="",
        semantic_criteria=None,
        keyword_matches_positive=None,
        keyword_matches_negative=None,
        website_text="",
    )
    assert "—" in prompt  # дефолт для region/okved
    assert "(нет)" in prompt


def test_normalize_hot_status():
    res = _normalize_llm_output(
        {"status": "hot", "comment": "OK", "signals": ["a", "b"], "hook": "звоните"},
        tokens_used=10,
    )
    assert res.status == AIStatus.HOT.value
    assert res.comment == "OK"
    assert res.signals == ["a", "b"]
    assert res.hook == "звоните"
    assert res.tokens_used == 10


def test_normalize_cold_status():
    res = _normalize_llm_output(
        {"status": "cold", "comment": "не подходит", "signals": [], "hook": ""},
        tokens_used=5,
    )
    assert res.status == AIStatus.COLD.value


def test_normalize_unknown_status_for_garbage():
    res = _normalize_llm_output(
        {"status": "maybe", "comment": "хз"},
        tokens_used=3,
    )
    assert res.status == AIStatus.UNKNOWN.value


def test_normalize_status_synonyms():
    """LLM может вернуть 'yes', 'hot lead' и т.п. — нормализуем."""
    assert _normalize_llm_output({"status": "yes"}, tokens_used=0).status == AIStatus.HOT.value
    assert _normalize_llm_output({"status": "hot lead"}, tokens_used=0).status == AIStatus.HOT.value
    assert _normalize_llm_output({"status": "no"}, tokens_used=0).status == AIStatus.COLD.value
    assert _normalize_llm_output({"status": "reject"}, tokens_used=0).status == AIStatus.COLD.value


def test_normalize_truncates_long_fields():
    res = _normalize_llm_output(
        {"status": "hot", "comment": "x" * 500, "hook": "y" * 200},
        tokens_used=0,
    )
    assert len(res.comment) <= 200
    assert len(res.hook) <= 100


def test_normalize_caps_signals_to_5():
    res = _normalize_llm_output(
        {"status": "hot", "signals": [str(i) for i in range(10)]},
        tokens_used=0,
    )
    assert len(res.signals) == 5


# ─── qualify_company_with_llm — интеграция с моком LLM ──────────────────


def _client_with_response(content: str):
    client = AsyncLLMClient(api_key="test-key", provider="openai", max_retries=0)
    fake_response = MagicMock()
    fake_response.choices = [
        MagicMock(message=MagicMock(content=content))
    ]
    fake_response.usage = MagicMock(prompt_tokens=100, completion_tokens=20)
    client._client.chat.completions.create = AsyncMock(return_value=fake_response)
    return client


@pytest.mark.anyio
async def test_qualify_company_with_llm_success():
    client = _client_with_response(
        '{"status":"hot","comment":"подходит","signals":["B2B сайт"],"hook":"дилерская программа"}'
    )
    res = await qualify_company_with_llm(
        client=client, model="gpt-4o-mini", prompt_template=PROMPT_TEMPLATE,
        company_name="X", company_region="Москва", company_okved=None,
        icp_description="ICP", semantic_criteria=None,
        keyword_matches_positive=["опт"], keyword_matches_negative=None,
        website_text="текст",
    )
    assert res.status == AIStatus.HOT.value
    assert res.comment == "подходит"
    assert res.tokens_used == 120


@pytest.mark.anyio
async def test_qualify_company_with_llm_non_json_returns_unknown():
    client = _client_with_response("LLM ответил текстом без JSON.")
    res = await qualify_company_with_llm(
        client=client, model="gpt-4o-mini", prompt_template=PROMPT_TEMPLATE,
        company_name="X", company_region=None, company_okved=None,
        icp_description="ICP", semantic_criteria=None,
        keyword_matches_positive=None, keyword_matches_negative=None,
        website_text="",
    )
    assert res.status == AIStatus.UNKNOWN.value
    assert res.error == "non_json_response"


@pytest.mark.anyio
async def test_qualify_company_with_llm_api_error():
    client = AsyncLLMClient(api_key="test-key", provider="openai", max_retries=0)
    client._client.chat.completions.create = AsyncMock(
        side_effect=ConnectionError("bad gateway")
    )
    res = await qualify_company_with_llm(
        client=client, model="gpt-4o-mini", prompt_template=PROMPT_TEMPLATE,
        company_name="X", company_region=None, company_okved=None,
        icp_description="ICP", semantic_criteria=None,
        keyword_matches_positive=None, keyword_matches_negative=None,
        website_text="",
    )
    assert res.status == AIStatus.UNKNOWN.value
    assert res.error and "bad gateway" in res.error
