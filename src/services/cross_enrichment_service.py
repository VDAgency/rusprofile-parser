"""Stage E — кросс-обогащение источников.

Запускается между Stage 0a и Stage 0b. Задача:
- Если компания пришла из Rusprofile (Я.Карты-сигналы пусты) — найти её на
  Я.Картах по телефону E.164 и заполнить ``yandex_*``.
- Если компания пришла из Я.Карт (нет ИНН/выручки) — найти её в Rusprofile
  по «название + регион» и подтянуть юр. данные.

Поведение:
- Идемпотентно через ``cross_enriched_at`` (TTL ``CROSS_ENRICH_TTL_DAYS``,
  по умолчанию 30 дней).
- Параллелизм ограничен ``Semaphore(CROSS_ENRICH_CONCURRENCY)`` (бережно
  с Я.Картами и Rusprofile — оба чувствительны к нагрузке).
- Таймаут одного направления — ``CROSS_ENRICH_TIMEOUT_S`` (по умолчанию 30 сек).
- Никогда не роняет вызывающий код: при сбое — лог warning, поля пустые,
  скоринг считается по тому, что есть.

См. ТЗ v3, раздел 5.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

from sqlalchemy.orm import Session

from src.db.models import Company, Source
from src.services.matching import (
    name_similarity,
    phone_to_e164,
    regions_match,
)

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------


def _ttl_days() -> int:
    try:
        return int(os.getenv("CROSS_ENRICH_TTL_DAYS", "30"))
    except (TypeError, ValueError):
        return 30


def _concurrency() -> int:
    try:
        return int(os.getenv("CROSS_ENRICH_CONCURRENCY", "2"))
    except (TypeError, ValueError):
        return 2


def _timeout_s() -> int:
    try:
        return int(os.getenv("CROSS_ENRICH_TIMEOUT_S", "30"))
    except (TypeError, ValueError):
        return 30


def _name_threshold() -> float:
    try:
        return float(os.getenv("CROSS_ENRICH_NAME_SIMILARITY_THRESHOLD", "0.80"))
    except (TypeError, ValueError):
        return 0.80


# ---------------------------------------------------------------------------
# Идемпотентность
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_fresh(company: Company) -> bool:
    """True, если кросс-обогащение делалось недавно (< TTL)."""
    if company.cross_enriched_at is None:
        return False
    ts = company.cross_enriched_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return _now() - ts < timedelta(days=_ttl_days())


def _has_yandex_signals(company: Company) -> bool:
    return bool(
        company.yandex_url
        or company.yandex_rating is not None
        or company.yandex_reviews_count is not None
    )


def _has_rusprofile_signals(company: Company) -> bool:
    return bool(company.inn or company.ogrn or company.revenue)


# ---------------------------------------------------------------------------
# Протоколы поисковых функций (для DI и моков)
# ---------------------------------------------------------------------------


class YandexFinder(Protocol):
    """Поиск карточки на Я.Картах по телефону E.164.

    Должен вернуть dict с ключами ``yandex_url``, ``yandex_rating``,
    ``yandex_reviews_count``, ``yandex_last_review_date``,
    ``yandex_hours_filled``, ``yandex_coordinates_filled``,
    ``yandex_operating_status`` и ``confidence`` (0..1) либо None.
    """

    async def __call__(
        self, phone_e164: str, name_hint: str | None = None
    ) -> dict | None: ...


class RusprofileFinder(Protocol):
    """Поиск компании в Rusprofile по названию + региону.

    Должен вернуть dict с ключами ``inn``, ``ogrn``, ``revenue``, ``profit``,
    ``status``, ``okved``, ``employees_count`` и ``confidence`` (0..1) либо None.
    """

    async def __call__(
        self, name: str, region: str | None = None
    ) -> dict | None: ...


# ---------------------------------------------------------------------------
# Direction 1: Rusprofile → Я.Карты
# ---------------------------------------------------------------------------


async def enrich_from_yandex(
    company: Company,
    yandex_finder: YandexFinder,
    *,
    force: bool = False,
) -> bool:
    """Заполняет ``yandex_*`` поля. Возвращает True, если нашли матч.

    НЕ коммитит. НЕ ставит ``cross_enriched_at`` (это делает обёртка
    ``enrich_company`` после возможного второго направления).
    """
    if not force and _is_fresh(company) and _has_yandex_signals(company):
        return False

    phone_e164 = phone_to_e164(company.phone)
    if not phone_e164:
        # Без телефона матч ненадёжен.
        return False

    try:
        result = await asyncio.wait_for(
            yandex_finder(phone_e164, name_hint=company.name),
            timeout=_timeout_s(),
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Y.Maps lookup timeout (phone=%s, company_id=%s)",
            phone_e164, company.id,
        )
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Y.Maps lookup failed (phone=%s, company_id=%s): %s",
            phone_e164, company.id, e,
        )
        return False

    if not result:
        return False

    confidence = float(result.get("confidence", 0.0) or 0.0)
    if confidence < 0.5:
        # Слабый матч — не доверяем.
        return False

    # Заполняем только пустые поля, не перезатираем существующие данные.
    if company.yandex_url is None and result.get("yandex_url"):
        company.yandex_url = result["yandex_url"]
    if company.yandex_rating is None and result.get("yandex_rating") is not None:
        company.yandex_rating = float(result["yandex_rating"])
    if (
        company.yandex_reviews_count is None
        and result.get("yandex_reviews_count") is not None
    ):
        company.yandex_reviews_count = int(result["yandex_reviews_count"])
    if (
        company.yandex_last_review_date is None
        and result.get("yandex_last_review_date") is not None
    ):
        company.yandex_last_review_date = result["yandex_last_review_date"]
    if (
        company.yandex_hours_filled is None
        and result.get("yandex_hours_filled") is not None
    ):
        company.yandex_hours_filled = bool(result["yandex_hours_filled"])
    if (
        company.yandex_coordinates_filled is None
        and result.get("yandex_coordinates_filled") is not None
    ):
        company.yandex_coordinates_filled = bool(result["yandex_coordinates_filled"])
    if (
        company.yandex_operating_status is None
        and result.get("yandex_operating_status")
    ):
        company.yandex_operating_status = result["yandex_operating_status"]

    company.cross_enrichment_source = "yandex"
    company.cross_match_confidence = confidence
    return True


# ---------------------------------------------------------------------------
# Direction 2: Я.Карты → Rusprofile
# ---------------------------------------------------------------------------


async def enrich_from_rusprofile(
    company: Company,
    rusprofile_finder: RusprofileFinder,
    *,
    force: bool = False,
) -> bool:
    """Заполняет ИНН/ОГРН/выручку/статус из Rusprofile. True если нашли."""
    if not force and _is_fresh(company) and _has_rusprofile_signals(company):
        return False

    name = (company.name or "").strip()
    if not name or len(name) < 3:
        return False

    try:
        result = await asyncio.wait_for(
            rusprofile_finder(name, region=company.region),
            timeout=_timeout_s(),
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Rusprofile lookup timeout (name=%r, company_id=%s)", name, company.id
        )
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Rusprofile lookup failed (name=%r, company_id=%s): %s",
            name, company.id, e,
        )
        return False

    if not result:
        return False

    confidence = float(result.get("confidence", 0.0) or 0.0)
    if confidence < 0.5:
        return False

    # Доп. валидация: имя из Rusprofile похоже на запрошенное и регион совпадает.
    found_name = result.get("name") or ""
    if found_name and name_similarity(name, found_name) < _name_threshold():
        logger.info(
            "Rusprofile name mismatch: %r vs %r (confidence=%s)",
            name, found_name, confidence,
        )
        return False

    found_region = result.get("region")
    if company.region and found_region and not regions_match(company.region, found_region):
        logger.info(
            "Rusprofile region mismatch for %r: %r vs %r",
            name, company.region, found_region,
        )
        return False

    if not company.inn and result.get("inn"):
        company.inn = result["inn"]
    if not company.ogrn and result.get("ogrn"):
        company.ogrn = result["ogrn"]
    if not company.revenue and result.get("revenue"):
        company.revenue = str(result["revenue"])
    if not company.profit and result.get("profit"):
        company.profit = str(result["profit"])
    if not company.status and result.get("status"):
        company.status = result["status"]
    if not company.okved and result.get("okved"):
        company.okved = result["okved"]

    # Дополнительные сигналы — в raw_json для скоринга.
    extra_raw = {}
    for key in ("finance_revenue", "finance_profit", "sshr", "employees_count"):
        if key in result:
            extra_raw[key] = result[key]
    if extra_raw:
        company.raw_json = {**(company.raw_json or {}), **extra_raw}

    # Источник кросс-обогащения. Если уже было обогащение из Я.Карт — пишем "both".
    if company.cross_enrichment_source == "yandex":
        company.cross_enrichment_source = "both"
    else:
        company.cross_enrichment_source = "rusprofile"
    company.cross_match_confidence = max(
        company.cross_match_confidence or 0.0, confidence
    )
    return True


# ---------------------------------------------------------------------------
# Обёртка: одно компания → выбор направления и постановка timestamp
# ---------------------------------------------------------------------------


async def enrich_company(
    company: Company,
    *,
    yandex_finder: YandexFinder | None = None,
    rusprofile_finder: RusprofileFinder | None = None,
    force: bool = False,
) -> bool:
    """Решает, какое направление кросс-обогащения нужно, выполняет.

    Возвращает True, если хотя бы одно направление дало матч.
    Ставит ``cross_enriched_at`` независимо от результата (мы попытались).
    """
    if not force and _is_fresh(company):
        return False

    matched_any = False

    # Направление 1: Rusprofile → Я.Карты — если у компании пусты Я.Карты-сигналы.
    if yandex_finder is not None and not _has_yandex_signals(company):
        try:
            if await enrich_from_yandex(company, yandex_finder, force=force):
                matched_any = True
        except Exception as e:  # noqa: BLE001
            logger.warning("enrich_from_yandex unexpected error: %s", e)

    # Направление 2: Я.Карты → Rusprofile — если у компании пусты Rusprofile-данные.
    if rusprofile_finder is not None and not _has_rusprofile_signals(company):
        try:
            if await enrich_from_rusprofile(company, rusprofile_finder, force=force):
                matched_any = True
        except Exception as e:  # noqa: BLE001
            logger.warning("enrich_from_rusprofile unexpected error: %s", e)

    company.cross_enriched_at = _now()
    return matched_any


# ---------------------------------------------------------------------------
# Пакетная обработка
# ---------------------------------------------------------------------------


async def enrich_batch(
    session: Session,
    companies: list[Company],
    *,
    yandex_finder: YandexFinder | None = None,
    rusprofile_finder: RusprofileFinder | None = None,
    force: bool = False,
    progress_cb: Callable[[int, int], Awaitable[None]] | None = None,
) -> dict[str, int]:
    """Обогащает список компаний с ограниченным параллелизмом.

    Возвращает агрегаты: yandex_found / rusprofile_found / both / none.
    Коммитит сессию по мере обработки (раз в 10 компаний).
    """
    sem = asyncio.Semaphore(_concurrency())
    stats = {"yandex_found": 0, "rusprofile_found": 0, "both": 0, "none": 0}
    total = len(companies)
    done = 0

    async def _one(company: Company) -> None:
        nonlocal done
        async with sem:
            await enrich_company(
                company,
                yandex_finder=yandex_finder,
                rusprofile_finder=rusprofile_finder,
                force=force,
            )
            done += 1
            if company.cross_enrichment_source == "both":
                stats["both"] += 1
            elif company.cross_enrichment_source == "yandex":
                stats["yandex_found"] += 1
            elif company.cross_enrichment_source == "rusprofile":
                stats["rusprofile_found"] += 1
            else:
                stats["none"] += 1
            if progress_cb is not None and done % 5 == 0:
                try:
                    await progress_cb(done, total)
                except Exception:  # noqa: BLE001
                    pass

    # asyncio.gather с семафором даёт нужный параллелизм.
    await asyncio.gather(*(_one(c) for c in companies), return_exceptions=False)

    try:
        session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("enrich_batch commit failed: %s", e)
        session.rollback()

    if progress_cb is not None:
        try:
            await progress_cb(done, total)
        except Exception:  # noqa: BLE001
            pass

    return stats
