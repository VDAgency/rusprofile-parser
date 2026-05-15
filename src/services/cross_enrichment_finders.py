"""Адаптеры (finder-ы) поверх существующих парсеров для Stage E.

Реализуют протоколы ``YandexFinder`` и ``RusprofileFinder`` из
``cross_enrichment_service``. Не переписывают парсеры — только используют
их через те же entry-точки, что и обычный парсинг.

**Важное правило:** оба finder-а — best-effort. При любой ошибке возвращают
None, не роняют qualify_service.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.services.matching import name_similarity, regions_match

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Direction 1: Rusprofile → Я.Карты по телефону E.164
# ---------------------------------------------------------------------------


def make_yandex_finder(context: "BrowserContext"):
    """Возвращает async-функцию-finder, которую можно передать в qualify_run.

    Использует `scrape_list` с категорией = телефон. Я.Карты понимают
    поиск по телефону: при вводе номера в поисковую строку обычно
    показывают карточку организации с этим номером.
    """
    from src.yandex_maps.scraper import enrich_place_details, scrape_list

    async def finder(phone_e164: str, name_hint: str | None = None) -> dict | None:
        # `scrape_list` требует регион + категорию. Используем телефон как
        # «запрос», а регион оставим пустым — Я.Карты разберётся сами.
        try:
            places = await scrape_list(
                context=context,
                region="",
                category=phone_e164,
                max_places=3,  # хватает: телефон обычно отдаёт 1 совпадение
            )
        except Exception as e:  # noqa: BLE001
            logger.info("Yandex scrape_list failed for phone=%s: %s", phone_e164, e)
            return None

        if not places:
            return None

        # Если нашли несколько — фильтруем по названию (если передано).
        chosen = places[0]
        confidence = 1.0 if len(places) == 1 else 0.7
        if len(places) > 1 and name_hint:
            scored = sorted(
                places,
                key=lambda p: name_similarity(name_hint, p.name),
                reverse=True,
            )
            top = scored[0]
            sim = name_similarity(name_hint, top.name)
            if sim < 0.5:
                # Никто из найденных нормально не похож — ненадёжно.
                return None
            chosen = top
            confidence = max(0.5, sim)

        # Дозаливаем детали (телефон и т.п. — для проверки).
        try:
            enriched = await enrich_place_details(
                context=context, places=[chosen], max_details=1,
            )
            chosen = enriched[0]
        except Exception as e:  # noqa: BLE001
            logger.info("enrich_place_details failed: %s", e)

        signals = chosen.yandex_signals_dict()
        signals["confidence"] = confidence
        return signals

    return finder


# ---------------------------------------------------------------------------
# Direction 2: Я.Карты → Rusprofile по «название + регион»
# ---------------------------------------------------------------------------


def make_rusprofile_finder(context: "BrowserContext"):
    """Возвращает async-функцию-finder, которую передаст qualify_run."""
    from src.rusprofile.filters import SearchFilters
    from src.rusprofile.parser import parse_search_results

    async def finder(name: str, region: str | None = None) -> dict | None:
        if not name or len(name) < 3:
            return None

        # Используем существующий поиск Rusprofile с query = name.
        # Region передавать в фильтрах нельзя без кода ОКАТО — лучше
        # фильтровать на стороне finder через regions_match.
        filters = SearchFilters(query=name)
        try:
            companies = await parse_search_results(
                context=context, filters=filters, max_new=5,
            )
        except Exception as e:  # noqa: BLE001
            logger.info("Rusprofile parse_search_results failed for %r: %s", name, e)
            return None

        if not companies:
            return None

        # Фильтр: совпадение названия ≥ 0.7 + (регион совпадает или не задан).
        candidates = []
        for c in companies:
            sim = name_similarity(name, c.name)
            if sim < 0.7:
                continue
            if region and c.region and not regions_match(region, c.region):
                continue
            candidates.append((sim, c))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        sim, top = candidates[0]
        confidence = sim if len(candidates) == 1 else max(0.5, sim - 0.1)

        # Возвращаем словарь по контракту enrich_from_rusprofile.
        return {
            "name": top.name,
            "region": top.region,
            "inn": top.inn,
            "ogrn": top.ogrn,
            "revenue": top.revenue,
            "profit": top.profit,
            "status": top.status,
            "okved": top.okved,
            "confidence": confidence,
        }

    return finder
