"""Тесты сервиса матчинга компаний."""

import pytest

from src.services.matching import (
    name_similarity,
    normalize_company_name,
    phone_to_e164,
    regions_match,
)


# ─── normalize_company_name ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('ООО "Ромашка"', "ромашка"),
        ("ОАО Газпром", "газпром"),
        ("  ООО   «Ромашка-Сервис»  ", "ромашка-сервис"),
        ("ИП Иванов И.И.", "иванов и.и."),
        ("ПАО Сбербанк", "сбербанк"),
        ("ао газпромнефть", "газпромнефть"),
        ('ЗАО "Северная Звезда"', "северная звезда"),
        ("Просто название", "просто название"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_company_name(raw, expected):
    assert normalize_company_name(raw) == expected


# ─── name_similarity ────────────────────────────────────────────────────────


def test_name_similarity_same():
    assert name_similarity('ООО "Ромашка"', "Ромашка") == pytest.approx(1.0, abs=0.05)


def test_name_similarity_word_order():
    """token_set_ratio устойчив к перестановке слов."""
    sim = name_similarity("Ромашка-Сервис", "Сервис Ромашка")
    assert sim >= 0.8


def test_name_similarity_different():
    sim = name_similarity("Ромашка", "Газпром")
    assert sim < 0.5


def test_name_similarity_empty():
    assert name_similarity("", "Ромашка") == 0.0
    assert name_similarity("Ромашка", None) == 0.0


# ─── phone_to_e164 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+7 (495) 123-45-67", "+74951234567"),
        ("8 (495) 123-45-67", "+74951234567"),
        ("8(495)1234567", "+74951234567"),
        ("+74951234567", "+74951234567"),
        ("8 999 123 45 67", "+79991234567"),
        ("", None),
        (None, None),
        ("abc", None),
        ("12", None),
    ],
)
def test_phone_to_e164(raw, expected):
    assert phone_to_e164(raw) == expected


# ─── regions_match ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "r1,r2,expected",
    [
        ("Самарская область", "Самарская обл.", True),
        ("Самарская обл.", "САМАРСКАЯ ОБЛАСТЬ", True),
        ("Москва", "г. Москва", True),
        ("Москва", "город Москва", True),
        ("Москва", "Санкт-Петербург", False),
        ("Самарская область", "Россия, Самарская область", True),
        ("", "Москва", False),
        ("Москва", None, False),
    ],
)
def test_regions_match(r1, r2, expected):
    assert regions_match(r1, r2) is expected
