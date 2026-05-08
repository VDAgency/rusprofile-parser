"""Сервис парсинга: оркестрация ParseRun, дедупа и записи результатов.

Отвечает за:
1. Подготовку tenant + theme (создание при первом запуске).
2. Загрузку «известных ключей» темы — по ним парсер фильтрует
   дубликаты на лету.
3. Запуск самого парсера (Rusprofile или Яндекс.Карты) с предикатом
   «новая ли компания» и лимитом ``max_new``.
4. Обогащение деталями (телефон/email/сайт) **только новых** —
   экономим на самой дорогой части парсинга.
5. Upsert компаний в БД, связывание с ParseRun через RunCompany.
6. Запись результата в Google Sheets (lower-level write_companies).
7. Финализацию ParseRun (status, метрики, sheet_url).

Возвращает структуру ``ParseResult`` с метриками для UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from playwright.async_api import async_playwright

from src.config import (
    DEFAULT_MAX_NEW,
    LOG_DIR,
    MAX_NEW_HARD_LIMIT,
    PROXY_PASSWORD,
    PROXY_SERVER,
    PROXY_USERNAME,
    SHEET_HEADERS,
    YANDEX_SHEET_HEADERS,
    YANDEX_SHEET_NAME,
)
from src.db import (
    Company as DBCompany,
    ParseRun,
    RunCompany,
    RunStatus,
    Source,
    Tenant,
    canonicalize_filters,
    ensure_tenant,
    ensure_theme,
    format_phone_for_display,
    get_session,
    is_duplicate,
    load_known_keys,
    normalize_phone,
    upsert_company,
)
from src.rusprofile.auth import get_authenticated_context
from src.rusprofile.parser import Company as RusprofileCompany
from src.rusprofile.parser import enrich_company_details, parse_search_results
from src.rusprofile.filters import SearchFilters
from src.sheets.client import write_companies
from src.yandex_maps.parser import YandexPlace
from src.yandex_maps.scraper import enrich_place_details, scrape_list

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Возвращаемые данные
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    run_id: int
    total_new: int
    total_skipped: int
    sheet_url: str | None
    status: str
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Yandex helpers
# ---------------------------------------------------------------------------


def _yandex_proxy_config() -> dict | None:
    if not PROXY_SERVER:
        return None
    cfg = {"server": PROXY_SERVER}
    if PROXY_USERNAME:
        cfg["username"] = PROXY_USERNAME
    if PROXY_PASSWORD:
        cfg["password"] = PROXY_PASSWORD
    return cfg


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------


def _clamp_max_new(max_new: int | None) -> int:
    if not max_new or max_new <= 0:
        return DEFAULT_MAX_NEW
    return min(int(max_new), MAX_NEW_HARD_LIMIT)


def _make_predicate(known):
    """Возвращает предикат `is_new` для парсера, замыкая `known`.

    Парсер вызывает предикат после построения каждой карточки. Если
    компания не дубликат — добавляем её ключи в `known`, чтобы в
    рамках того же запуска повторно не считать её «новой».
    """
    from src.db.dedup import update_known

    def is_new(company) -> bool:
        inn = (company.inn or None) or None
        ogrn = (company.ogrn or None) or None
        phone_norm = normalize_phone(company.phone) if company.phone else None
        if is_duplicate(inn=inn, ogrn=ogrn, phone_normalized=phone_norm, known=known):
            return False

        # Создаём суррогат с правильными атрибутами для update_known
        class _Sur:
            pass
        sur = _Sur()
        sur.inn = inn
        sur.ogrn = ogrn
        sur.phone_normalized = phone_norm
        update_known(known, sur)
        return True

    return is_new


# ---------------------------------------------------------------------------
# Запуск Rusprofile-парсинга
# ---------------------------------------------------------------------------


async def run_rusprofile(
    *,
    telegram_user_id: int,
    username: str | None,
    filters: SearchFilters,
    filters_for_theme: dict,
    max_new: int | None = None,
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> ParseResult:
    """Запускает парсинг Rusprofile с дедупом и записью в БД/Sheets.

    ``filters_for_theme`` — сырой dict из Mini App (или handlers),
    используется для генерации filters_hash и человекочитаемого title.
    """
    max_new_clamped = _clamp_max_new(max_new)

    # 1. Tenant + theme + run + known keys
    with get_session() as session:
        tenant = ensure_tenant(session, telegram_user_id, username)
        theme = ensure_theme(session, tenant, Source.RUSPROFILE.value, filters_for_theme)
        known = load_known_keys(session, theme)

        run = ParseRun(
            tenant_id=tenant.id,
            theme_id=theme.id,
            source=Source.RUSPROFILE.value,
            status=RunStatus.RUNNING.value,
            requested_new=max_new_clamped,
        )
        session.add(run)
        session.flush()
        run_id = run.id
        tenant_id = tenant.id
        # `tenant` объект ещё нужен после выхода из сессии — но связь
        # нам не нужна, есть id; ниже создадим новую сессию.

    if progress_callback:
        await progress_callback(
            f"Подключаюсь к Rusprofile (лимит — {max_new_clamped} новых)…"
        )

    # 2. Парсинг с дедуп-предикатом
    is_new = _make_predicate(known)
    skipped = 0

    async def parser_progress(total, processed):
        if progress_callback:
            await progress_callback(
                f"Парсинг… найдено {processed} новых "
                f"(в источнике ~{total})"
            )

    new_companies: list[RusprofileCompany] = []
    error_message: str | None = None
    try:
        async with async_playwright() as pw:
            context = await get_authenticated_context(pw)

            # Сохраняем «оригинальный» предикат и оборачиваем для подсчёта skipped
            def is_new_with_skip(company):
                ok = is_new(company)
                nonlocal skipped
                if not ok:
                    skipped += 1
                return ok

            new_companies = await parse_search_results(
                context,
                filters,
                progress_callback=parser_progress,
                is_new_predicate=is_new_with_skip,
                max_new=max_new_clamped,
            )

            if new_companies:
                if progress_callback:
                    await progress_callback(
                        f"Найдено {len(new_companies)} новых "
                        f"(пропущено {skipped} дубликатов). Собираю контакты…"
                    )
                new_companies = await enrich_company_details(
                    context, new_companies, parser_progress
                )

            await context.browser.close()
    except Exception as e:  # noqa: BLE001
        logger.exception("Rusprofile parse failed")
        error_message = str(e)

    # 3. Persist в БД
    sheet_url: str | None = None
    persisted_companies: list[RusprofileCompany] = []
    with get_session() as session:
        run = session.get(ParseRun, run_id)
        tenant = session.get(Tenant, tenant_id)

        if error_message and not new_companies:
            run.status = RunStatus.ERROR.value
            run.error_message = error_message[:1000]
            run.finished_at = datetime.now(timezone.utc)
            return ParseResult(
                run_id=run_id, total_new=0, total_skipped=skipped,
                sheet_url=None, status=run.status, error_message=run.error_message,
            )

        seen_company_ids: set[int] = set()
        for c in new_companies:
            db_c, _ = upsert_company(
                session, tenant,
                name=c.name, source=Source.RUSPROFILE.value,
                inn=c.inn or None, ogrn=c.ogrn or None,
                phone=c.phone or None, region=c.region or None,
                address=c.address or None, okved=c.okved or None,
                revenue=c.revenue or None, profit=c.profit or None,
                email=c.email or None, site=c.site or None,
                status=c.status or None,
                raw={"detail_href": c.detail_href},
            )
            # Несколько новых карточек могут после enrich оказаться одной
            # компанией (редкий случай у Rusprofile, но возможный) — не
            # добавляем дубль RunCompany.
            if db_c.id in seen_company_ids:
                continue
            seen_company_ids.add(db_c.id)
            session.add(RunCompany(run_id=run.id, company_id=db_c.id, is_new=True))
            persisted_companies.append(c)

        run.total_new = len(persisted_companies)
        run.total_skipped = skipped
        run.finished_at = datetime.now(timezone.utc)
        run.status = RunStatus.DONE.value if not error_message else RunStatus.ERROR.value
        if error_message:
            run.error_message = error_message[:1000]

    # 4. Sheets — переписываем телефоны через format_phone_for_display
    if persisted_companies:
        for c in persisted_companies:
            if c.phone:
                c.phone = format_phone_for_display(c.phone)
        sheet_url = write_companies(persisted_companies, replace=True)

        with get_session() as session:
            run = session.get(ParseRun, run_id)
            run.sheet_url = sheet_url

    return ParseResult(
        run_id=run_id,
        total_new=len(persisted_companies),
        total_skipped=skipped,
        sheet_url=sheet_url,
        status=RunStatus.DONE.value if not error_message else RunStatus.ERROR.value,
        error_message=error_message,
    )


# ---------------------------------------------------------------------------
# Запуск Яндекс.Карт
# ---------------------------------------------------------------------------


async def run_yandex(
    *,
    telegram_user_id: int,
    username: str | None,
    region: str,
    category: str,
    max_new: int | None = None,
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> ParseResult:
    max_new_clamped = _clamp_max_new(max_new)

    filters_for_theme = {"region": region, "category": category}

    with get_session() as session:
        tenant = ensure_tenant(session, telegram_user_id, username)
        theme = ensure_theme(session, tenant, Source.YANDEX_MAPS.value, filters_for_theme)
        known = load_known_keys(session, theme)

        run = ParseRun(
            tenant_id=tenant.id,
            theme_id=theme.id,
            source=Source.YANDEX_MAPS.value,
            status=RunStatus.RUNNING.value,
            requested_new=max_new_clamped,
        )
        session.add(run)
        session.flush()
        run_id = run.id
        tenant_id = tenant.id

    if progress_callback:
        await progress_callback(
            f"Подключаюсь к Яндекс.Картам (лимит — {max_new_clamped} новых)…"
        )

    error_message: str | None = None
    new_places: list[YandexPlace] = []
    skipped = 0
    try:
        proxy = _yandex_proxy_config()
        launch_kwargs = {"headless": True}
        if proxy:
            launch_kwargs["proxy"] = proxy

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**launch_kwargs)
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1366, "height": 900},
                    locale="ru-RU",
                    timezone_id="Europe/Moscow",
                )
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', "
                    "{get: () => undefined});"
                )

                async def yandex_progress(total, processed):
                    if progress_callback:
                        await progress_callback(
                            f"Яндекс.Карты: найдено {processed} (в источнике ~{total})"
                        )

                # Берём с запасом x2 (но не более MAX_NEW_HARD_LIMIT*1.5):
                # часть карточек может оказаться дубликатами.
                fetch_limit = min(max_new_clamped * 2, int(MAX_NEW_HARD_LIMIT * 1.5))
                places = await scrape_list(
                    context, region=region, category=category,
                    max_places=fetch_limit,
                    progress_callback=yandex_progress, logs_dir=LOG_DIR,
                )

                # Дедуп по списочной выдаче (без enrich): тут есть телефон
                # не всегда; ИНН не бывает.
                for p in places:
                    phone_norm = normalize_phone(p.phone) if p.phone else None
                    if is_duplicate(inn=None, ogrn=None,
                                    phone_normalized=phone_norm, known=known):
                        skipped += 1
                        continue
                    new_places.append(p)
                    if phone_norm:
                        known.phone.add(phone_norm)
                    if len(new_places) >= max_new_clamped:
                        break

                if new_places:
                    if progress_callback:
                        await progress_callback(
                            f"Найдено {len(new_places)} новых "
                            f"(пропущено {skipped}). Собираю детали…"
                        )
                    new_places = await enrich_place_details(
                        context, new_places, yandex_progress
                    )

                await context.close()
            finally:
                await browser.close()
    except Exception as e:  # noqa: BLE001
        logger.exception("Yandex parse failed")
        error_message = str(e)

    sheet_url: str | None = None
    persisted_places: list[YandexPlace] = []

    with get_session() as session:
        run = session.get(ParseRun, run_id)
        tenant = session.get(Tenant, tenant_id)

        if error_message and not new_places:
            run.status = RunStatus.ERROR.value
            run.error_message = error_message[:1000]
            run.finished_at = datetime.now(timezone.utc)
            return ParseResult(
                run_id=run_id, total_new=0, total_skipped=skipped,
                sheet_url=None, status=run.status, error_message=run.error_message,
            )

        # После enrich мог появиться телефон — на этом этапе ещё одна
        # проверка дубликата через upsert (внутри _find_existing_company).
        # Несколько YandexPlace могут после enrich «схлопнуться» в одну
        # и ту же компанию (общий телефон у сети ресторанов и т.п.) —
        # тогда upsert вернёт один и тот же db_c.id. RunCompany имеет
        # PRIMARY KEY (run_id, company_id), поэтому второй INSERT упадёт
        # с UNIQUE constraint failed. Дедуплируем через set.
        seen_company_ids: set[int] = set()
        for p in new_places:
            db_c, created = upsert_company(
                session, tenant,
                name=p.name, source=Source.YANDEX_MAPS.value,
                phone=p.phone or None, region=p.region or region,
                address=p.address or None, okved=p.categories or None,
                site=p.site or None,
                raw={
                    "rating": p.rating, "reviews_count": p.reviews_count,
                    "hours": p.hours, "coordinates": p.coordinates,
                    "yandex_url": p.yandex_url,
                },
            )
            if db_c.id in seen_company_ids:
                # В этом запуске уже есть RunCompany для этой компании —
                # пропускаем, иначе IntegrityError.
                skipped += 1
                continue
            seen_company_ids.add(db_c.id)
            session.add(RunCompany(
                run_id=run.id, company_id=db_c.id, is_new=created,
            ))
            if created:
                persisted_places.append(p)
            else:
                # Стало дублем после enrich (нашли телефон, который уже есть)
                skipped += 1

        run.total_new = len(persisted_places)
        run.total_skipped = skipped
        run.finished_at = datetime.now(timezone.utc)
        run.status = RunStatus.DONE.value if not error_message else RunStatus.ERROR.value
        if error_message:
            run.error_message = error_message[:1000]

    if persisted_places:
        for p in persisted_places:
            if p.phone:
                p.phone = format_phone_for_display(p.phone)
        sheet_url = write_companies(
            persisted_places,
            sheet_name=YANDEX_SHEET_NAME,
            headers=YANDEX_SHEET_HEADERS,
            replace=True,
        )
        with get_session() as session:
            run = session.get(ParseRun, run_id)
            run.sheet_url = sheet_url

    return ParseResult(
        run_id=run_id,
        total_new=len(persisted_places),
        total_skipped=skipped,
        sheet_url=sheet_url,
        status=RunStatus.DONE.value if not error_message else RunStatus.ERROR.value,
        error_message=error_message,
    )
