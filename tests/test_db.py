"""Тесты модуля src/db: нормализация, хеш фильтров, дедуп, upsert."""

import pytest

from src.db.normalize import format_phone_for_display, normalize_phone


# ---- normalize_phone ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+7 (495) 123-45-67", "74951234567"),
        ("8 (495) 123-45-67", "74951234567"),
        ("8(495)1234567", "74951234567"),
        ("+74951234567", "74951234567"),
        ("74951234567", "74951234567"),
        ("4951234567", "74951234567"),         # 10 цифр — добавляем 7
        ("89991234567", "79991234567"),
        ("8 999 123 45 67", "79991234567"),
        ("", None),
        (None, None),
        ("abc", None),
        ("12345", None),                        # слишком коротко
        ("+49 30 1234567", None),              # неросс. номер
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_format_phone_for_display():
    assert format_phone_for_display("74951234567") == "+7 (495) 123-45-67"
    assert format_phone_for_display("8 (495) 123-45-67") == "+7 (495) 123-45-67"
    assert format_phone_for_display("") == ""
    # Если не смогли нормализовать — вернём «как есть».
    assert format_phone_for_display("ext.123") == "ext.123"


# ---- canonicalize_filters / filters_hash ---------------------------------


def test_filters_hash_stable_across_order():
    from src.db.dedup import filters_hash

    a = filters_hash("rusprofile", {"okved": ["46.49.3", "73.11"], "region": ["77"]})
    b = filters_hash("rusprofile", {"region": ["77"], "okved": ["73.11", "46.49.3"]})
    assert a == b, "Перестановка кодов не должна менять хеш"


def test_filters_hash_strips_falsy():
    from src.db.dedup import filters_hash

    a = filters_hash("rusprofile", {"okved": ["46.49.3"], "query": ""})
    b = filters_hash("rusprofile", {"okved": ["46.49.3"]})
    assert a == b, "Пустые поля не должны влиять на хеш"


def test_filters_hash_different_for_different_inputs():
    from src.db.dedup import filters_hash

    a = filters_hash("rusprofile", {"okved": ["46.49.3"]})
    b = filters_hash("rusprofile", {"okved": ["46.49.4"]})
    assert a != b


def test_humanize_filters_yandex():
    from src.db.dedup import humanize_filters

    title = humanize_filters("yandex_maps", {"region": "Москва", "category": "стоматологии"})
    assert "Москва" in title
    assert "стоматологии" in title


def test_humanize_filters_rusprofile():
    from src.db.dedup import humanize_filters

    title = humanize_filters(
        "rusprofile",
        {"okved": ["73.11", "73.20", "46.49.3"], "region": ["77"]},
    )
    assert "ОКВЭД" in title
    assert "Регион" in title


# ---- ensure_tenant / ensure_theme ----------------------------------------


def test_ensure_tenant_idempotent(db_session):
    from src.db.dedup import ensure_tenant

    t1 = ensure_tenant(db_session, telegram_user_id=42, username="vasya")
    t2 = ensure_tenant(db_session, telegram_user_id=42, username="vasya")
    assert t1.id == t2.id


def test_ensure_theme_idempotent(db_session):
    from src.db.dedup import ensure_tenant, ensure_theme

    t = ensure_tenant(db_session, telegram_user_id=42)
    th1 = ensure_theme(db_session, t, "rusprofile", {"okved": ["46.49.3"]})
    th2 = ensure_theme(db_session, t, "rusprofile", {"okved": ["46.49.3"]})
    assert th1.id == th2.id

    # Другая тема — другой id
    th3 = ensure_theme(db_session, t, "rusprofile", {"okved": ["73.11"]})
    assert th3.id != th1.id


# ---- upsert_company + дедуп ----------------------------------------------


def test_upsert_company_creates_then_finds(db_session):
    from src.db.dedup import ensure_tenant, upsert_company

    t = ensure_tenant(db_session, telegram_user_id=1)

    a, created_a = upsert_company(
        db_session, t,
        name="ООО Ромашка", source="rusprofile",
        inn="7701234567", ogrn="1027700123456",
        phone="+7 (495) 123-45-67",
    )
    assert created_a is True
    assert a.phone_normalized == "74951234567"

    # По ИНН должны найти ту же компанию.
    b, created_b = upsert_company(
        db_session, t,
        name="ООО Ромашка (новая)", source="rusprofile",
        inn="7701234567",  # тот же ИНН
        site="romashka.ru",
    )
    assert created_b is False
    assert b.id == a.id
    assert b.site == "romashka.ru"  # дозалили


def test_upsert_company_phone_only_when_no_inn_ogrn(db_session):
    """Гибрид: телефон-фолбэк только если у обеих сторон нет ИНН/ОГРН."""
    from src.db.dedup import ensure_tenant, upsert_company

    t = ensure_tenant(db_session, telegram_user_id=1)

    a, _ = upsert_company(
        db_session, t,
        name="A", source="yandex_maps",
        phone="+7 (495) 123-45-67",
    )
    # Та же компания без ИНН — телефон совпал, дедуп.
    b, created_b = upsert_company(
        db_session, t,
        name="A duplicate", source="yandex_maps",
        phone="8 495 1234567",
    )
    assert created_b is False
    assert b.id == a.id

    # Теперь приходит компания с ТЕМ ЖЕ телефоном, но с ИНН — это другая
    # компания (телефон-фолбэк не должен сработать).
    c, created_c = upsert_company(
        db_session, t,
        name="B with INN", source="rusprofile",
        inn="7701111111",
        phone="+7 (495) 123-45-67",
    )
    assert created_c is True
    assert c.id != a.id


def test_dedup_in_theme_skips_known(db_session):
    """End-to-end: загрузка known_keys для темы и проверка."""
    from src.db.dedup import (
        ensure_tenant, ensure_theme, load_known_keys,
        is_duplicate, upsert_company, update_known,
    )
    from src.db.models import ParseRun, RunCompany, RunStatus

    t = ensure_tenant(db_session, telegram_user_id=1)
    theme = ensure_theme(db_session, t, "rusprofile", {"okved": ["46.49.3"]})

    # Симулируем первый запуск: 2 компании.
    run1 = ParseRun(
        tenant_id=t.id, theme_id=theme.id, source="rusprofile",
        status=RunStatus.DONE.value, requested_new=10,
    )
    db_session.add(run1)
    db_session.flush()

    for inn in ("7701234567", "7702345678"):
        c, _ = upsert_company(
            db_session, t,
            name=f"Company {inn}", source="rusprofile", inn=inn,
        )
        db_session.add(RunCompany(run_id=run1.id, company_id=c.id, is_new=True))
    db_session.flush()

    # Известные ключи темы
    known = load_known_keys(db_session, theme)
    assert "7701234567" in known.inn
    assert "7702345678" in known.inn

    # Проверка дедупа
    assert is_duplicate(inn="7701234567", ogrn=None, phone_normalized=None, known=known)
    assert not is_duplicate(inn="7703333333", ogrn=None, phone_normalized=None, known=known)

    # update_known работает локально
    new_company = type("C", (), {})()
    new_company.inn = "7703333333"
    new_company.ogrn = None
    new_company.phone_normalized = None
    update_known(known, new_company)
    assert "7703333333" in known.inn


def test_known_keys_isolated_between_themes(db_session):
    """Дедуп идёт по теме: компания из темы A не блокирует тему B."""
    from src.db.dedup import (
        ensure_tenant, ensure_theme, load_known_keys, upsert_company,
    )
    from src.db.models import ParseRun, RunCompany

    t = ensure_tenant(db_session, telegram_user_id=1)
    theme_a = ensure_theme(db_session, t, "rusprofile", {"okved": ["46.49.3"]})
    theme_b = ensure_theme(db_session, t, "rusprofile", {"okved": ["73.11"]})

    run_a = ParseRun(tenant_id=t.id, theme_id=theme_a.id, source="rusprofile")
    db_session.add(run_a)
    db_session.flush()

    c, _ = upsert_company(
        db_session, t, name="X", source="rusprofile", inn="7701234567",
    )
    db_session.add(RunCompany(run_id=run_a.id, company_id=c.id, is_new=True))
    db_session.flush()

    known_a = load_known_keys(db_session, theme_a)
    known_b = load_known_keys(db_session, theme_b)

    assert "7701234567" in known_a.inn
    assert "7701234567" not in known_b.inn  # в теме B компании ещё не было
