"""Тесты CRUD-сервиса ИИ-профилей."""

import pytest

from src.ai.profile_extractor import ExtractedProfile
from src.db.models import Tenant
from src.services import profile_service as ps


@pytest.fixture
def tenant(db_session):
    t = Tenant(telegram_user_id=1)
    db_session.add(t)
    db_session.flush()
    return t


def _profile_data() -> ExtractedProfile:
    return ExtractedProfile(
        icp_description="Тестовый ICP",
        semantic_criteria=["крит 1"],
        keywords_positive=["опт", "склад"],
        keywords_negative=["розница"],
        extraction_model="gpt-4o-mini",
        extraction_tokens_used=300,
    )


def test_compute_brief_hash_normalizes_whitespace():
    h1 = ps.compute_brief_hash("  Я продаю сувениры  ")
    h2 = ps.compute_brief_hash("я продаю   сувениры")
    assert h1 == h2


def test_compute_brief_hash_different_for_different_text():
    h1 = ps.compute_brief_hash("Я продаю сувениры")
    h2 = ps.compute_brief_hash("Я продаю канцтовары")
    assert h1 != h2


def test_save_profile_creates_new(db_session, tenant):
    profile = ps.save_profile(
        session=db_session, tenant=tenant,
        name="Сувенирка", brief="Я продаю сувениры", data=_profile_data(),
    )
    assert profile.id is not None
    assert profile.name == "Сувенирка"
    assert profile.icp_description == "Тестовый ICP"
    assert profile.is_active is True


def test_save_profile_updates_existing_by_brief_hash(db_session, tenant):
    """Второй save с тем же брифом — апдейтит, не создаёт второй."""
    p1 = ps.save_profile(
        session=db_session, tenant=tenant,
        name="v1", brief="Я продаю сувениры", data=_profile_data(),
    )
    db_session.flush()
    p2 = ps.save_profile(
        session=db_session, tenant=tenant,
        name="v2", brief="  я продаю сувениры  ", data=_profile_data(),
    )
    assert p1.id == p2.id
    assert p2.name == "v2"


def test_find_cached_profile(db_session, tenant):
    ps.save_profile(
        session=db_session, tenant=tenant,
        name="Сувенирка", brief="Я продаю сувениры", data=_profile_data(),
    )
    db_session.flush()
    cached = ps.find_cached_profile(db_session, tenant, "  Я ПРОДАЮ сувениры  ")
    assert cached is not None
    assert cached.name == "Сувенирка"


def test_find_cached_returns_none_for_other_tenant(db_session, tenant):
    ps.save_profile(
        session=db_session, tenant=tenant,
        name="X", brief="бриф", data=_profile_data(),
    )
    db_session.flush()
    other = Tenant(telegram_user_id=2)
    db_session.add(other)
    db_session.flush()
    assert ps.find_cached_profile(db_session, other, "бриф") is None


def test_list_profiles_excludes_inactive(db_session, tenant):
    p1 = ps.save_profile(
        session=db_session, tenant=tenant,
        name="A", brief="бриф 1", data=_profile_data(),
    )
    p2 = ps.save_profile(
        session=db_session, tenant=tenant,
        name="B", brief="бриф 2", data=_profile_data(),
    )
    db_session.flush()
    ps.soft_delete(db_session, p1)
    db_session.flush()
    visible = ps.list_profiles(db_session, tenant)
    assert len(visible) == 1
    assert visible[0].id == p2.id


def test_update_profile_keywords(db_session, tenant):
    p = ps.save_profile(
        session=db_session, tenant=tenant,
        name="X", brief="b", data=_profile_data(),
    )
    db_session.flush()
    ps.update_profile_keywords(
        session=db_session, profile=p,
        keywords_positive=["новое"],
        keywords_negative=["плохое"],
        name="новое имя",
    )
    assert p.keywords_positive == ["новое"]
    assert p.keywords_negative == ["плохое"]
    assert p.name == "новое имя"


def test_update_profile_normalizes_keywords(db_session, tenant):
    p = ps.save_profile(
        session=db_session, tenant=tenant,
        name="X", brief="b", data=_profile_data(),
    )
    ps.update_profile_keywords(
        session=db_session, profile=p,
        keywords_positive=["ОПТ", "  склад  ", "партнёр"],
    )
    assert p.keywords_positive == ["опт", "склад", "партнер"]
