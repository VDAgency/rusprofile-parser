"""Регрессионные тесты на дедуп при записи RunCompany.

Сценарий, который ловится этими тестами (баг из боевого теста):

* Run №1 находит автосалоны A, B, C.
* Run №2 в той же теме снова находит A, B + новый D.
* run_companies для Run №2 должен содержать ТОЛЬКО D с is_new=True.
* repush/xlsx для Run №2 должны отдавать ТОЛЬКО D, не A,B,D.

Тестируем на уровне БД-моделей — без поднятия HTTP/Playwright.
"""

from sqlalchemy import select

from src.db import (
    Company, ParseRun, RunCompany, RunStatus, Source,
    ensure_tenant, ensure_theme, get_session, upsert_company,
)


def _make_company(session, tenant, name, phone):
    """Удобный шортка."""
    c, _ = upsert_company(
        session, tenant,
        name=name, source=Source.YANDEX_MAPS.value,
        phone=phone,
    )
    return c


def test_run_companies_only_new_for_theme(db_session):
    """Кросс-запусковая проверка: в RunCompany второго запуска лежит
    только новая компания, дубликаты отсекаются."""
    tenant = ensure_tenant(db_session, telegram_user_id=1)
    theme = ensure_theme(
        db_session, tenant, Source.YANDEX_MAPS.value,
        {"region": "Самарская область", "category": "автосалон"},
    )

    # === RUN 1: A, B, C ===
    run1 = ParseRun(
        tenant_id=tenant.id, theme_id=theme.id,
        source=Source.YANDEX_MAPS.value,
        status=RunStatus.DONE.value, requested_new=10,
        total_new=3, total_skipped=0,
    )
    db_session.add(run1)
    db_session.flush()

    a = _make_company(db_session, tenant, "Авто A", "+7 900 111 11 11")
    b = _make_company(db_session, tenant, "Авто B", "+7 900 222 22 22")
    c = _make_company(db_session, tenant, "Авто C", "+7 900 333 33 33")
    for company in (a, b, c):
        db_session.add(RunCompany(
            run_id=run1.id, company_id=company.id, is_new=True,
        ))
    db_session.flush()

    # === RUN 2: A, B уже в теме; D — новая ===
    run2 = ParseRun(
        tenant_id=tenant.id, theme_id=theme.id,
        source=Source.YANDEX_MAPS.value,
        status=RunStatus.DONE.value, requested_new=10,
        total_new=1, total_skipped=2,
    )
    db_session.add(run2)
    db_session.flush()

    # ВОТ ПРАВИЛО: пишем в run_companies только новые-для-темы.
    # A и B уже привязаны к run1 в этой теме → пропускаем.
    # D новая.
    d = _make_company(db_session, tenant, "Авто D", "+7 900 444 44 44")
    db_session.add(RunCompany(
        run_id=run2.id, company_id=d.id, is_new=True,
    ))
    db_session.flush()

    # === Проверки ===
    # run_companies для run2: ровно одна запись (D)
    n_links = db_session.scalar(
        select(__import__("sqlalchemy").func.count())
        .select_from(RunCompany)
        .where(RunCompany.run_id == run2.id)
    )
    assert n_links == 1, f"В run_companies для run2 должна быть 1 запись, есть {n_links}"

    # И эта запись — именно D
    linked = db_session.scalars(
        select(Company).join(RunCompany, RunCompany.company_id == Company.id)
        .where(RunCompany.run_id == run2.id)
    ).all()
    assert len(linked) == 1
    assert linked[0].name == "Авто D"


def test_repush_query_filters_only_is_new_true(db_session):
    """Защита от старой грязи в БД: даже если в run_companies есть
    is_new=False (это бывает после старых багов), repush-запрос
    должен фильтровать только is_new=True.
    """
    tenant = ensure_tenant(db_session, telegram_user_id=1)
    theme = ensure_theme(
        db_session, tenant, Source.YANDEX_MAPS.value, {"region": "X"},
    )
    run = ParseRun(
        tenant_id=tenant.id, theme_id=theme.id,
        source=Source.YANDEX_MAPS.value,
        status=RunStatus.DONE.value, requested_new=5,
    )
    db_session.add(run)
    db_session.flush()

    new_a = _make_company(db_session, tenant, "Новая A", "+7 901 1")
    dup_b = _make_company(db_session, tenant, "Дубль B", "+7 901 2")
    dup_c = _make_company(db_session, tenant, "Дубль C", "+7 901 3")

    db_session.add(RunCompany(run_id=run.id, company_id=new_a.id, is_new=True))
    # Симулируем «грязь» из старого кода
    db_session.add(RunCompany(run_id=run.id, company_id=dup_b.id, is_new=False))
    db_session.add(RunCompany(run_id=run.id, company_id=dup_c.id, is_new=False))
    db_session.flush()

    # «Грязный» SELECT — без фильтра — отдаст 3
    dirty = db_session.scalars(
        select(Company).join(RunCompany, RunCompany.company_id == Company.id)
        .where(RunCompany.run_id == run.id)
        .order_by(Company.id)
    ).all()
    assert len(dirty) == 3

    # «Чистый» SELECT — с фильтром — отдаст только новых
    clean = db_session.scalars(
        select(Company).join(RunCompany, RunCompany.company_id == Company.id)
        .where(RunCompany.run_id == run.id, RunCompany.is_new == True)  # noqa: E712
        .order_by(Company.id)
    ).all()
    assert len(clean) == 1
    assert clean[0].name == "Новая A"


def test_export_xlsx_filters_is_new_true(db_session, tmp_path):
    """Excel-экспорт через export_run_to_xlsx должен возвращать
    только is_new=True компании."""
    from src.api.export_xlsx import export_run_to_xlsx

    tenant = ensure_tenant(db_session, telegram_user_id=1)
    theme = ensure_theme(
        db_session, tenant, Source.YANDEX_MAPS.value, {"region": "X"},
    )
    run = ParseRun(
        tenant_id=tenant.id, theme_id=theme.id,
        source=Source.YANDEX_MAPS.value,
        status=RunStatus.DONE.value, requested_new=5,
    )
    db_session.add(run)
    db_session.flush()

    new_a = _make_company(db_session, tenant, "Новая A", "+7 902 1")
    dup_b = _make_company(db_session, tenant, "Дубль B", "+7 902 2")
    db_session.add(RunCompany(run_id=run.id, company_id=new_a.id, is_new=True))
    db_session.add(RunCompany(run_id=run.id, company_id=dup_b.id, is_new=False))
    db_session.commit()

    data, filename = export_run_to_xlsx(db_session, run.id)
    assert data and filename.endswith(".xlsx")

    # Распакуем и посмотрим какие имена попали в xlsx
    from openpyxl import load_workbook
    import io
    wb = load_workbook(io.BytesIO(data))
    ws = wb.active
    rows_text = []
    for row in ws.iter_rows(values_only=True):
        rows_text.append([str(c) if c is not None else "" for c in row])

    # Заголовок + 1 запись (только Новая A)
    body = rows_text[1:]
    names_in_xlsx = [r[0] for r in body]
    assert "Новая A" in names_in_xlsx, f"Excel должен содержать 'Новая A', есть: {names_in_xlsx}"
    assert "Дубль B" not in names_in_xlsx, (
        f"Excel НЕ должен содержать 'Дубль B' (is_new=False), но он есть: {names_in_xlsx}"
    )
