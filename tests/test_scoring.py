"""Тесты универсального скоринга компаний (Stage 0a + 0b)."""

from datetime import date, timedelta

import pytest

from src.ai.scoring import (
    GROUP_A_MAX,
    GROUP_B_MAX,
    GROUP_C_MAX,
    apply_score_to_company,
    score_company_early,
    score_company_full,
)


# Удобные dict-ы вместо моделей — Stage 0 не лезет в БД.


def _company(**overrides):
    base = {
        "site": None, "phone": None, "email": None,
        "status": None, "inn": None, "ogrn": None, "revenue": None,
        "raw_json": None, "source": "rusprofile",
        "yandex_rating": None, "yandex_reviews_count": None,
        "yandex_last_review_date": None, "yandex_hours_filled": None,
        "yandex_coordinates_filled": None, "yandex_url": None,
        "yandex_operating_status": None,
    }
    base.update(overrides)
    return base


# ─── Stage 0a ─────────────────────────────────────────────────────────────


def test_stage_0a_passes_normal_company():
    c = _company(site="https://x.ru", phone="+7 495 123-45-67")
    res = score_company_early(c)
    assert res.hard_filter_passed
    assert res.kill_reasons == []


def test_stage_0a_kills_liquidating():
    c = _company(site="x.ru", status="Ликвидация")
    res = score_company_early(c)
    assert not res.hard_filter_passed
    assert any("status" in r for r in res.kill_reasons)


def test_stage_0a_kills_bankrupt():
    c = _company(site="x.ru", status="Банкротство (открыто конкурсное)")
    res = score_company_early(c)
    assert not res.hard_filter_passed


def test_stage_0a_kills_yandex_closed():
    c = _company(phone="+7 495 1", yandex_operating_status="permanently_closed")
    res = score_company_early(c)
    assert not res.hard_filter_passed
    assert any("yandex_operating" in r for r in res.kill_reasons)


def test_stage_0a_kills_invalid_address():
    c = _company(site="x.ru", raw_json={"invalid_address": True})
    res = score_company_early(c)
    assert not res.hard_filter_passed


def test_stage_0a_kills_no_contacts():
    c = _company()
    res = score_company_early(c)
    assert not res.hard_filter_passed
    assert "no_contacts_at_all" in res.kill_reasons


# ─── Stage 0b — отдельные группы ─────────────────────────────────────────


def test_group_a_full_with_socials():
    c = _company(
        site="x.ru", phone="+7 495 1", email="a@b",
        raw_json={"socials": {"telegram": "@x", "vk": "vk.com/x", "whatsapp": "...", "instagram": "..."}},
    )
    res = score_company_full(c)
    assert res.hard_filter_passed
    # max A = 30; site+phone+email = 20; socials capped to +10 → итого 30.
    assert res.signals["group_a"]
    # Поскольку нет Я.Карт и Rusprofile-данных кроме контактов, группа A одна.
    # Но раз нет inn/ogrn/status/revenue, group B вообще не считается.
    # group_c тоже не считается (нет yandex_*).
    # max_possible = 30, score = 30, normalized = 100.
    assert res.score_max_possible == GROUP_A_MAX
    # site(10) + phone(5) + email(5) + 4 socials × 2 = 28, кап на 30.
    assert res.score_raw == 28
    assert res.score_normalized == 93


def test_only_rusprofile_data_max_is_a_plus_b():
    c = _company(
        site="x.ru", phone="+7 495 1",
        inn="7701234567", status="Действующая",
        revenue="10 млн ₽",
        raw_json={"finance_revenue": 10_000_000, "sshr": 10},
    )
    res = score_company_full(c)
    assert res.hard_filter_passed
    assert res.score_max_possible == GROUP_A_MAX + GROUP_B_MAX
    # group A = site(10)+phone(5) = 15
    # group B = active(10)+revenue_known(5)+>=5M(5)+employees>5(5) = 25
    assert res.score_raw == 15 + 25


def test_only_yandex_data_max_is_a_plus_c():
    c = _company(
        phone="+7 495 1", source="yandex_maps",
        yandex_url="https://yandex.ru/maps/org/123",
        yandex_rating=4.5, yandex_reviews_count=20,
        yandex_last_review_date=date.today() - timedelta(days=10),
        yandex_hours_filled=True, yandex_coordinates_filled=True,
    )
    res = score_company_full(c)
    assert res.hard_filter_passed
    assert res.score_max_possible == GROUP_A_MAX + GROUP_C_MAX
    # group A = phone(5) = 5
    # group C = card(5)+rating(5)+reviews>=10(5)+last_review<3mo(10)+hours(5)+coords(5) = 35
    assert res.score_raw == 5 + 35


def test_both_sources_max_100():
    c = _company(
        site="x.ru", phone="+7 495 1", email="a@b",
        inn="7701234567", status="Действующая",
        revenue="50 млн", raw_json={"finance_revenue": 60_000_000, "sshr": 100},
        yandex_url="https://yandex.ru/maps/org/123",
        yandex_rating=5.0, yandex_reviews_count=200,
        yandex_last_review_date=date.today() - timedelta(days=2),
        yandex_hours_filled=True, yandex_coordinates_filled=True,
    )
    res = score_company_full(c)
    assert res.hard_filter_passed
    assert res.score_max_possible == 100


def test_group_c_old_review_no_bonus():
    c = _company(
        phone="+7 495 1",
        yandex_url="https://yandex.ru/maps/x",
        yandex_rating=4.0, yandex_reviews_count=15,
        yandex_last_review_date=date.today() - timedelta(days=600),
    )
    res = score_company_full(c)
    # last_review 600 дней < 730 → +0 баллов за свежесть
    # card(5)+rating(5)+reviews>=10(5)+0 = 15
    assert res.signals["group_c"]
    raw_c_pts = sum(int(s.split(":+")[-1]) for s in res.signals["group_c"])
    assert raw_c_pts == 15


# ─── Поздние хард-фильтры ────────────────────────────────────────────────


def test_late_hard_filter_kills_dead_yandex_card():
    c = _company(
        phone="+7 495 1",
        yandex_url="https://yandex.ru/maps/x",
        yandex_reviews_count=2,
        yandex_last_review_date=date.today() - timedelta(days=800),
    )
    res = score_company_full(c)
    assert not res.hard_filter_passed
    assert "yandex_card_inactive>24mo" in res.kill_reasons


def test_late_hard_filter_kills_zero_engagement():
    c = _company(
        phone="+7 495 1",
        yandex_url="https://yandex.ru/maps/x",
        yandex_reviews_count=0,
    )
    res = score_company_full(c)
    assert not res.hard_filter_passed
    assert "yandex_card_zero_engagement" in res.kill_reasons


# ─── apply_score_to_company ──────────────────────────────────────────────


def test_apply_score_passes(db_session):
    """Подопытная — настоящий Company. Скоринг проставляет ai_score."""
    from src.db.models import Company
    c = Company(
        tenant_id=1, source="rusprofile", name="X",
        site="x.ru", phone="+7 1", email="a@b",
        status="Действующая",
    )
    res = score_company_full(c)
    apply_score_to_company(c, res)
    assert c.ai_score == res.score_normalized
    assert c.ai_status is None  # не skip — нет ai_status


def test_apply_score_skip(db_session):
    from src.db.models import AIStatus, Company
    c = Company(
        tenant_id=1, source="rusprofile", name="X",
        status="ликвидируется",
    )
    res = score_company_full(c)
    apply_score_to_company(c, res)
    assert c.ai_status == AIStatus.SKIP.value
    assert c.ai_comment is not None
