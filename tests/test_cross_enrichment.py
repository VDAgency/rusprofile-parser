"""Тесты Stage E — кросс-обогащение источников.

Используем моки `yandex_finder` и `rusprofile_finder` (Protocol), не лазая
в Я.Карты/Rusprofile реально.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from src.db.models import Company, Tenant
from src.services import cross_enrichment_service as ces


# ─── Фикстуры ─────────────────────────────────────────────────────────────


@pytest.fixture
def tenant(db_session):
    t = Tenant(telegram_user_id=1)
    db_session.add(t)
    db_session.flush()
    return t


@pytest.fixture
def rusprofile_company(db_session, tenant):
    """Компания с данными Rusprofile, без Я.Карт-сигналов."""
    c = Company(
        tenant_id=tenant.id, source="rusprofile", name="ООО Ромашка",
        inn="7701234567", ogrn="1027700123456",
        phone="+7 (495) 123-45-67",
        region="Москва",
    )
    db_session.add(c)
    db_session.flush()
    return c


@pytest.fixture
def yandex_company(db_session, tenant):
    """Компания, найденная на Я.Картах, без юр. данных."""
    c = Company(
        tenant_id=tenant.id, source="yandex_maps", name="Кафе Уютное",
        phone="+7 495 999-88-77",
        region="Москва",
        yandex_url="https://yandex.ru/maps/org/123",
        yandex_rating=4.5,
        yandex_reviews_count=42,
    )
    db_session.add(c)
    db_session.flush()
    return c


# ─── Фейковые finders ─────────────────────────────────────────────────────


def make_yandex_finder(result):
    async def _finder(phone, name_hint=None):
        return result
    return _finder


def make_rusprofile_finder(result):
    async def _finder(name, region=None):
        return result
    return _finder


# ─── Direction 1: Rusprofile → Я.Карты ────────────────────────────────────


@pytest.mark.anyio
async def test_enrich_from_yandex_fills_signals(db_session, rusprofile_company):
    finder = make_yandex_finder({
        "yandex_url": "https://yandex.ru/maps/org/777",
        "yandex_rating": 4.7,
        "yandex_reviews_count": 128,
        "yandex_last_review_date": date(2026, 4, 1),
        "yandex_hours_filled": True,
        "yandex_coordinates_filled": True,
        "yandex_operating_status": "working",
        "confidence": 1.0,
    })

    matched = await ces.enrich_from_yandex(rusprofile_company, finder)
    assert matched is True
    assert rusprofile_company.yandex_url == "https://yandex.ru/maps/org/777"
    assert rusprofile_company.yandex_rating == 4.7
    assert rusprofile_company.yandex_reviews_count == 128
    assert rusprofile_company.yandex_last_review_date == date(2026, 4, 1)
    assert rusprofile_company.yandex_hours_filled is True
    assert rusprofile_company.cross_enrichment_source == "yandex"
    assert rusprofile_company.cross_match_confidence == 1.0


@pytest.mark.anyio
async def test_enrich_from_yandex_skips_no_phone(db_session, tenant):
    company = Company(tenant_id=tenant.id, source="rusprofile", name="X", inn="7701234567")
    db_session.add(company)
    db_session.flush()
    finder = make_yandex_finder({"confidence": 1.0})
    matched = await ces.enrich_from_yandex(company, finder)
    assert matched is False
    assert company.yandex_url is None


@pytest.mark.anyio
async def test_enrich_from_yandex_skips_low_confidence(db_session, rusprofile_company):
    finder = make_yandex_finder({
        "yandex_url": "x",
        "confidence": 0.3,
    })
    matched = await ces.enrich_from_yandex(rusprofile_company, finder)
    assert matched is False
    assert rusprofile_company.yandex_url is None


@pytest.mark.anyio
async def test_enrich_from_yandex_no_match(db_session, rusprofile_company):
    finder = make_yandex_finder(None)
    matched = await ces.enrich_from_yandex(rusprofile_company, finder)
    assert matched is False
    assert rusprofile_company.yandex_url is None


@pytest.mark.anyio
async def test_enrich_from_yandex_does_not_overwrite(db_session, rusprofile_company):
    rusprofile_company.yandex_url = "https://existing.url"
    db_session.flush()
    finder = make_yandex_finder({
        "yandex_url": "https://new.url",
        "yandex_rating": 4.0,
        "confidence": 1.0,
    })
    await ces.enrich_from_yandex(rusprofile_company, finder)
    # Существующее не затирается, но пустое заполняется.
    assert rusprofile_company.yandex_url == "https://existing.url"
    assert rusprofile_company.yandex_rating == 4.0


# ─── Direction 2: Я.Карты → Rusprofile ────────────────────────────────────


@pytest.mark.anyio
async def test_enrich_from_rusprofile_fills_legal_data(db_session, yandex_company):
    finder = make_rusprofile_finder({
        "name": "Кафе Уютное",  # точно совпадает
        "region": "Москва",
        "inn": "7707083893",
        "ogrn": "1027700132195",
        "revenue": "5 000 000",
        "status": "Действующая",
        "okved": "56.10",
        "finance_revenue": 5_000_000,
        "sshr": 12,
        "confidence": 0.9,
    })
    matched = await ces.enrich_from_rusprofile(yandex_company, finder)
    assert matched is True
    assert yandex_company.inn == "7707083893"
    assert yandex_company.ogrn == "1027700132195"
    assert yandex_company.revenue == "5 000 000"
    assert yandex_company.status == "Действующая"
    assert yandex_company.okved == "56.10"
    assert (yandex_company.raw_json or {}).get("finance_revenue") == 5_000_000
    assert yandex_company.cross_enrichment_source == "rusprofile"


@pytest.mark.anyio
async def test_enrich_from_rusprofile_rejects_name_mismatch(db_session, yandex_company):
    """Если найденное имя сильно отличается — не доверяем."""
    finder = make_rusprofile_finder({
        "name": "Совершенно другая компания",
        "region": "Москва",
        "inn": "9999999999",
        "confidence": 0.9,
    })
    matched = await ces.enrich_from_rusprofile(yandex_company, finder)
    assert matched is False
    assert yandex_company.inn is None


@pytest.mark.anyio
async def test_enrich_from_rusprofile_rejects_region_mismatch(db_session, yandex_company):
    finder = make_rusprofile_finder({
        "name": "Кафе Уютное",
        "region": "Санкт-Петербург",  # Я.Карты говорят Москва
        "inn": "9999999999",
        "confidence": 0.9,
    })
    matched = await ces.enrich_from_rusprofile(yandex_company, finder)
    assert matched is False


@pytest.mark.anyio
async def test_enrich_from_rusprofile_skips_short_name(db_session, tenant):
    company = Company(tenant_id=tenant.id, source="yandex_maps", name="X")
    db_session.add(company)
    db_session.flush()
    finder = make_rusprofile_finder({"name": "X", "inn": "1", "confidence": 1.0})
    matched = await ces.enrich_from_rusprofile(company, finder)
    assert matched is False


# ─── Идемпотентность через TTL ────────────────────────────────────────────


@pytest.mark.anyio
async def test_idempotent_within_ttl(db_session, rusprofile_company):
    rusprofile_company.cross_enriched_at = datetime.now(timezone.utc) - timedelta(days=5)
    rusprofile_company.yandex_url = "https://stale"
    db_session.flush()

    finder = make_yandex_finder({
        "yandex_url": "https://fresh",
        "confidence": 1.0,
    })
    matched = await ces.enrich_from_yandex(rusprofile_company, finder)
    assert matched is False  # Свежо, пропускаем.
    assert rusprofile_company.yandex_url == "https://stale"


@pytest.mark.anyio
async def test_force_overrides_ttl(db_session, rusprofile_company):
    rusprofile_company.cross_enriched_at = datetime.now(timezone.utc) - timedelta(days=5)
    db_session.flush()

    finder = make_yandex_finder({
        "yandex_url": "https://fresh",
        "confidence": 1.0,
    })
    matched = await ces.enrich_from_yandex(rusprofile_company, finder, force=True)
    assert matched is True


@pytest.mark.anyio
async def test_expired_ttl_re_enriches(db_session, rusprofile_company):
    rusprofile_company.cross_enriched_at = datetime.now(timezone.utc) - timedelta(days=40)
    db_session.flush()

    finder = make_yandex_finder({
        "yandex_url": "https://fresh",
        "confidence": 1.0,
    })
    matched = await ces.enrich_from_yandex(rusprofile_company, finder)
    assert matched is True


# ─── enrich_company (обёртка с timestamp) ─────────────────────────────────


@pytest.mark.anyio
async def test_enrich_company_sets_timestamp(db_session, rusprofile_company):
    finder = make_yandex_finder(None)
    await ces.enrich_company(rusprofile_company, yandex_finder=finder)
    assert rusprofile_company.cross_enriched_at is not None


@pytest.mark.anyio
async def test_enrich_company_both_directions(db_session, tenant):
    """Компания без сигналов с обеих сторон — оба направления отрабатывают."""
    company = Company(
        tenant_id=tenant.id, source="yandex_maps", name="ООО Ромашка",
        phone="+7 495 123-45-67", region="Москва",
    )
    db_session.add(company)
    db_session.flush()

    yfinder = make_yandex_finder({
        "yandex_url": "https://yandex.ru/maps/org/1",
        "yandex_rating": 5.0, "confidence": 1.0,
    })
    rfinder = make_rusprofile_finder({
        "name": "ООО Ромашка", "region": "Москва",
        "inn": "7701234567", "confidence": 0.95,
    })
    await ces.enrich_company(
        company, yandex_finder=yfinder, rusprofile_finder=rfinder,
    )
    assert company.yandex_url is not None
    assert company.inn == "7701234567"
    # Оба источника подтянули данные → "both"
    assert company.cross_enrichment_source == "both"


# ─── Пакетная обработка ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_enrich_batch_aggregates_stats(db_session, tenant):
    # Используем валидные мобильные номера, чтобы phone_to_e164 их принял.
    companies = [
        Company(tenant_id=tenant.id, source="rusprofile", name=f"Co{i}",
                phone=f"+7 999 12345{i:02d}", inn=f"77000000{i:02d}")
        for i in range(5)
    ]
    for c in companies:
        db_session.add(c)
    db_session.flush()

    yfinder = make_yandex_finder({
        "yandex_url": "https://x", "yandex_rating": 4.0, "confidence": 1.0,
    })

    stats = await ces.enrich_batch(
        db_session, companies,
        yandex_finder=yfinder,
    )
    assert stats["yandex_found"] == 5
    assert stats["both"] == 0
    assert stats["none"] == 0
