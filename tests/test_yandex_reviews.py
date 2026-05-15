"""Тесты парсера форматов дат отзывов Я.Карт.

Чисто синхронные — без Playwright. Сетевые/UI-тесты `get_last_review_date`
не пишем: они ломкие к вёрстке Я.Карт и должны проверяться руками на проде.
"""

from datetime import date, timedelta

import pytest

from src.yandex_maps.reviews import parse_relative_date


TODAY = date(2026, 5, 15)


# ─── Точные форматы дат ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024-04-23", date(2024, 4, 23)),
        ("23.04.2024", date(2024, 4, 23)),
        ("23.4.2024", date(2024, 4, 23)),
        ("23/04/2024", date(2024, 4, 23)),
        ("23.04.24", date(2024, 4, 23)),
        ("опубликовано 2024-04-23T12:00:00", date(2024, 4, 23)),
    ],
)
def test_absolute_dates(raw, expected):
    assert parse_relative_date(raw, today=TODAY) == expected


# ─── Относительные форматы ─────────────────────────────────────────────────


def test_today():
    assert parse_relative_date("сегодня", today=TODAY) == TODAY
    assert parse_relative_date("сегодня в 14:32", today=TODAY) == TODAY


def test_yesterday():
    assert parse_relative_date("вчера", today=TODAY) == TODAY - timedelta(days=1)
    assert parse_relative_date("вчера в 18:00", today=TODAY) == TODAY - timedelta(days=1)


def test_day_before_yesterday():
    assert parse_relative_date("позавчера", today=TODAY) == TODAY - timedelta(days=2)


@pytest.mark.parametrize(
    "raw,delta_days",
    [
        ("3 дня назад", 3),
        ("1 день назад", 1),
        ("день назад", 1),
        ("10 дней назад", 10),
    ],
)
def test_days_ago(raw, delta_days):
    assert parse_relative_date(raw, today=TODAY) == TODAY - timedelta(days=delta_days)


@pytest.mark.parametrize(
    "raw,delta_weeks",
    [
        ("2 недели назад", 2),
        ("неделю назад", 1),
        ("3 недель назад", 3),
    ],
)
def test_weeks_ago(raw, delta_weeks):
    assert parse_relative_date(raw, today=TODAY) == TODAY - timedelta(weeks=delta_weeks)


@pytest.mark.parametrize(
    "raw,delta_months",
    [
        ("месяц назад", 1),
        ("3 месяца назад", 3),
        ("5 месяцев назад", 5),
    ],
)
def test_months_ago(raw, delta_months):
    assert parse_relative_date(raw, today=TODAY) == TODAY - timedelta(days=30 * delta_months)


def test_year_ago():
    res = parse_relative_date("год назад", today=TODAY)
    assert res == date(2025, 5, 15)


def test_years_ago():
    res = parse_relative_date("2 года назад", today=TODAY)
    assert res == date(2024, 5, 15)


def test_n_years_ago():
    res = parse_relative_date("5 лет назад", today=TODAY)
    assert res == date(2021, 5, 15)


# ─── Дата с месяцем словом ────────────────────────────────────────────────


def test_day_month_no_year_past():
    """«23 апреля» — текущий год, т.к. апрель прошёл."""
    res = parse_relative_date("23 апреля", today=TODAY)
    assert res == date(2026, 4, 23)


def test_day_month_no_year_future_means_past_year():
    """«23 декабря» — TODAY = 15 мая, декабрь в будущем → берём прошлый год."""
    res = parse_relative_date("23 декабря", today=TODAY)
    assert res == date(2025, 12, 23)


def test_day_month_with_year():
    res = parse_relative_date("23 апреля 2024 г.", today=TODAY)
    assert res == date(2024, 4, 23)
    res = parse_relative_date("23 апреля 2024", today=TODAY)
    assert res == date(2024, 4, 23)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1 января 2025", date(2025, 1, 1)),
        ("28 февраля 2025", date(2025, 2, 28)),
        ("15 марта 2025", date(2025, 3, 15)),
        ("10 мая 2025", date(2025, 5, 10)),
        ("30 июня 2025", date(2025, 6, 30)),
        ("4 июля 2025", date(2025, 7, 4)),
        ("31 августа 2025", date(2025, 8, 31)),
        ("1 сентября 2025", date(2025, 9, 1)),
        ("12 октября 2025", date(2025, 10, 12)),
        ("7 ноября 2025", date(2025, 11, 7)),
        ("31 декабря 2025", date(2025, 12, 31)),
    ],
)
def test_all_months_with_year(raw, expected):
    assert parse_relative_date(raw, today=TODAY) == expected


# ─── Кейс-инсенситивность и нормализация ё ────────────────────────────────


def test_case_insensitive():
    assert parse_relative_date("ВЧЕРА", today=TODAY) == TODAY - timedelta(days=1)
    assert parse_relative_date("Сегодня", today=TODAY) == TODAY


def test_yo_normalization():
    """Дополнительно: «ё» приводится к «е», на случай если в названии месяца
    встретится 'июнЁ' и т.п. Не должен ломать другие парсеры."""
    assert parse_relative_date("Сёгодня", today=TODAY) == TODAY


# ─── Невалидный вход ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "",
        None,
        "просто текст без дат",
        "asdf 12345",
        "32 апреля",   # невалидное число дня
        "15 несуществующего месяца",
    ],
)
def test_invalid_inputs(raw):
    assert parse_relative_date(raw, today=TODAY) is None


# ─── YandexPlace.yandex_signals_dict ──────────────────────────────────────


def test_yandex_signals_dict_basic():
    from src.yandex_maps.parser import YandexPlace
    p = YandexPlace(
        name="X", yandex_url="https://yandex.ru/maps/org/123",
        rating="4.5", reviews_count="128",
        last_review_date=date(2026, 1, 15),
        hours_filled=True, coordinates_filled=True,
        operating_status="working",
    )
    d = p.yandex_signals_dict()
    assert d["yandex_url"] == "https://yandex.ru/maps/org/123"
    assert d["yandex_rating"] == 4.5
    assert d["yandex_reviews_count"] == 128
    assert d["yandex_last_review_date"] == date(2026, 1, 15)
    assert d["yandex_hours_filled"] is True
    assert d["yandex_coordinates_filled"] is True
    assert d["yandex_operating_status"] == "working"


def test_yandex_signals_dict_handles_garbage():
    from src.yandex_maps.parser import YandexPlace
    p = YandexPlace(name="X", rating="abc", reviews_count="not a number")
    d = p.yandex_signals_dict()
    assert d["yandex_rating"] is None
    assert d["yandex_reviews_count"] is None
    assert d["yandex_url"] is None


def test_yandex_signals_dict_comma_decimal():
    """Я.Карты иногда отдают рейтинг как '4,5' вместо '4.5'."""
    from src.yandex_maps.parser import YandexPlace
    p = YandexPlace(name="X", rating="4,7")
    d = p.yandex_signals_dict()
    assert d["yandex_rating"] == 4.7
