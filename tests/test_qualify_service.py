"""Интеграционные тесты qualify_service.

Используем patch для website_extractor / qualifier / cross_enrichment,
чтобы не лазить в сеть и не вызывать LLM.
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.ai.profile_extractor import ExtractedProfile
from src.ai.qualifier import QualifyLLMResult
from src.ai.website_extractor import WebsiteExtractResult
from src.db.models import (
    AIStatus,
    Company,
    ParseRun,
    RunCompany,
    RunStatus,
    Source,
    TariffPlan,
    Tenant,
    Theme,
)
from src.services import profile_service, qualify_service


@pytest.fixture
def setup_run(db_session):
    """Создаёт tenant + theme + run + 3 компании (is_new=True)."""
    tenant = Tenant(
        telegram_user_id=42,
        tariff_plan=TariffPlan.AI.value,
        ai_quota_companies_monthly=100,
        ai_quota_tokens_monthly=1_000_000,
        quota_period_start=datetime.now(timezone.utc),
    )
    db_session.add(tenant)
    db_session.flush()

    theme = Theme(
        tenant_id=tenant.id, source=Source.RUSPROFILE.value,
        filters_hash="x", filters_json={}, title="Test",
    )
    db_session.add(theme)
    db_session.flush()

    run = ParseRun(
        tenant_id=tenant.id, theme_id=theme.id,
        source=Source.RUSPROFILE.value,
        status=RunStatus.DONE.value, requested_new=10,
    )
    db_session.add(run)
    db_session.flush()

    companies = [
        Company(
            tenant_id=tenant.id, source=Source.RUSPROFILE.value,
            name=f"ООО Компания {i}",
            phone="+7 495 123-45-67", site=f"https://example{i}.ru",
            email=f"a{i}@example.ru",
            status="Действующая",
            inn=f"77000000{i:02d}", ogrn=f"10277000000{i:02d}",
        )
        for i in range(3)
    ]
    for c in companies:
        db_session.add(c)
    db_session.flush()

    for c in companies:
        db_session.add(RunCompany(run_id=run.id, company_id=c.id, is_new=True))
    db_session.commit()

    return {"tenant": tenant, "run": run, "companies": companies}


@pytest.fixture
def saved_profile(db_session, setup_run):
    profile = profile_service.save_profile(
        session=db_session, tenant=setup_run["tenant"],
        name="Test Profile", brief="Я ищу B2B компании",
        data=ExtractedProfile(
            icp_description="B2B оптовые компании",
            semantic_criteria=["работает с бизнесом"],
            # Несколько словоформ — реальные клиенты так и делают.
            keywords_positive=[
                "опт", "оптовый", "склад", "b2b",
                "дилер", "дилеров", "партнерская программа",
            ],
            keywords_negative=["розница"],
        ),
    )
    db_session.commit()
    return profile


def _fake_llm_client():
    """LLM-клиент с замоканым chat.completions.create — не падает при init."""
    from unittest.mock import MagicMock
    from src.ai.llm_client import AsyncLLMClient
    client = AsyncLLMClient(api_key="test-key", provider="openai", max_retries=0)
    return client


# ─── Тесты ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_qualify_run_simple_tariff_only_scoring(db_session, setup_run):
    """Simple-тариф: ИИ-стадии не выполняются, только Stage 0."""
    setup_run["tenant"].tariff_plan = TariffPlan.SIMPLE.value
    db_session.commit()

    stats = await qualify_service.qualify_run(
        run_id=setup_run["run"].id,
        tenant_id=setup_run["tenant"].id,
        profile_id=None,
        enable_cross_enrichment=False,
    )
    assert stats.total == 3
    # Все компании прошли Stage 0, без ИИ — ai_score выставлен.
    db_session.expire_all()
    for c in setup_run["companies"]:
        db_session.refresh(c)
        assert c.ai_score is not None
        assert c.ai_qualified_at is not None
        # Status остался None — это ок для Simple-тарифа.
    # Проверяем decisions_by_stage
    assert stats.decisions_by_stage.get("simple_done", 0) == 3


@pytest.mark.anyio
async def test_qualify_run_ai_tariff_keyword_hot(db_session, setup_run, saved_profile):
    """AI-тариф + Stage 2 решает hot по ключам — без вызова LLM."""
    # Подменяем website_extractor: возвращает текст с позитивными ключами.
    fake_site = WebsiteExtractResult(
        text=(
            "Мы оптовый поставщик товаров для b2b. У нас огромный склад "
            "и партнёрская программа для дилеров." * 10
        ),
        pages_fetched=["https://x.ru"],
    )
    with patch(
        "src.ai.website_extractor.extract_website_text",
        new=AsyncMock(return_value=fake_site),
    ):
        stats = await qualify_service.qualify_run(
            run_id=setup_run["run"].id,
            tenant_id=setup_run["tenant"].id,
            profile_id=saved_profile.id,
            enable_cross_enrichment=False,
        )
    assert stats.total == 3
    assert stats.by_status.get("hot", 0) == 3
    # LLM не вызывался.
    assert stats.llm_calls == 0
    assert stats.tokens_used_total == 0


@pytest.mark.anyio
async def test_qualify_run_ai_tariff_no_site_means_cold(db_session, setup_run, saved_profile):
    """Если у компании нет сайта — сразу cold, LLM не нужен."""
    for c in setup_run["companies"]:
        c.site = None
    db_session.commit()

    stats = await qualify_service.qualify_run(
        run_id=setup_run["run"].id,
        tenant_id=setup_run["tenant"].id,
        profile_id=saved_profile.id,
        enable_cross_enrichment=False,
    )
    assert stats.by_status.get("cold", 0) == 3
    assert stats.llm_calls == 0


@pytest.mark.anyio
async def test_qualify_run_ai_tariff_calls_llm_for_ambiguous(
    db_session, setup_run, saved_profile,
):
    """Если keyword-скоринг дал needs_llm — вызываем LLM (мок)."""
    fake_site = WebsiteExtractResult(
        text="Просто магазин. " * 50,  # без ключей
        pages_fetched=["https://x.ru"],
    )
    fake_llm = QualifyLLMResult(
        status=AIStatus.HOT.value,
        comment="LLM решил что hot",
        signals=["B2B"],
        hook="зацепка",
        tokens_used=200,
    )
    with patch(
        "src.ai.website_extractor.extract_website_text",
        new=AsyncMock(return_value=fake_site),
    ), patch(
        "src.services.qualify_service.qualify_company_default",
        new=AsyncMock(return_value=fake_llm),
    ):
        stats = await qualify_service.qualify_run(
            run_id=setup_run["run"].id,
            tenant_id=setup_run["tenant"].id,
            profile_id=saved_profile.id,
            enable_cross_enrichment=False,
            llm_client=_fake_llm_client(),
        )
    assert stats.by_status.get("hot", 0) == 3
    assert stats.llm_calls == 3
    assert stats.tokens_used_total == 600


@pytest.mark.anyio
async def test_qualify_run_skips_dead_companies(db_session, setup_run, saved_profile):
    """Stage 0a отсекает «ликвидируется» компании."""
    setup_run["companies"][0].status = "Ликвидируется"
    db_session.commit()

    stats = await qualify_service.qualify_run(
        run_id=setup_run["run"].id,
        tenant_id=setup_run["tenant"].id,
        profile_id=saved_profile.id,
        enable_cross_enrichment=False,
    )
    assert stats.by_status.get("skip", 0) >= 1
    db_session.refresh(setup_run["companies"][0])
    assert setup_run["companies"][0].ai_status == AIStatus.SKIP.value


@pytest.mark.anyio
async def test_qualify_run_quota_exceeded_marks_quota_status(
    db_session, setup_run, saved_profile,
):
    """Когда квота исчерпана — компании получают quota_exceeded, LLM не вызывается."""
    # Истощаем квоту.
    setup_run["tenant"].ai_companies_processed_period = 100  # = quota_companies_monthly
    db_session.commit()

    fake_site = WebsiteExtractResult(
        text="ambiguous content " * 50, pages_fetched=["https://x.ru"],
    )
    fake_llm = AsyncMock()  # не должен быть вызван
    with patch(
        "src.ai.website_extractor.extract_website_text",
        new=AsyncMock(return_value=fake_site),
    ), patch(
        "src.services.qualify_service.qualify_company_default",
        new=fake_llm,
    ):
        stats = await qualify_service.qualify_run(
            run_id=setup_run["run"].id,
            tenant_id=setup_run["tenant"].id,
            profile_id=saved_profile.id,
            enable_cross_enrichment=False,
            llm_client=_fake_llm_client(),
        )
    fake_llm.assert_not_called()
    assert stats.by_status.get("quota_exceeded", 0) >= 1


@pytest.mark.anyio
async def test_qualify_run_writes_stats_to_parse_run(db_session, setup_run, saved_profile):
    """ai_qualify_stats и ai_profile_id записываются в ParseRun."""
    fake_site = WebsiteExtractResult(
        text="оптовая компания склад b2b " * 20,
        pages_fetched=["https://x.ru"],
    )
    with patch(
        "src.ai.website_extractor.extract_website_text",
        new=AsyncMock(return_value=fake_site),
    ):
        await qualify_service.qualify_run(
            run_id=setup_run["run"].id,
            tenant_id=setup_run["tenant"].id,
            profile_id=saved_profile.id,
            enable_cross_enrichment=False,
        )
    db_session.refresh(setup_run["run"])
    assert setup_run["run"].ai_qualify_enabled is True
    assert setup_run["run"].ai_qualify_stats is not None
    assert setup_run["run"].ai_profile_id == saved_profile.id


@pytest.mark.anyio
async def test_qualify_run_handles_invalid_run_id():
    """Несуществующий run_id — пустой stats без падения."""
    stats = await qualify_service.qualify_run(
        run_id=999999, tenant_id=999999, profile_id=None,
        enable_cross_enrichment=False,
    )
    assert stats.total == 0
