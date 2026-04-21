"""Проверка: парсер реально получает отфильтрованные по региону карточки?

Запускает parse_search_results c Самарской обл. и печатает адрес каждой
карточки. Если все из 63-го региона — SSR фильтрует; если там Москва
и Питер — фильтр не работает, и мы заливали в таблицу всероссийскую
выдачу.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context
from src.rusprofile.parser import parse_search_results
from src.rusprofile.filters import (
    SearchFilters, build_search_url,
    STATUS_ACTIVE, LEGAL_FORM_OOO,
)


async def main():
    filters = SearchFilters(
        status=[STATUS_ACTIVE],
        region=["63"],
        okopf=[LEGAL_FORM_OOO],
    )
    url = build_search_url(filters)
    print(f"URL: {url}\n")

    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            companies = await parse_search_results(context, filters)
            print(f"\nСобрано {len(companies)} компаний\n")
            for i, c in enumerate(companies, 1):
                print(f"{i:3d}. {c.name[:50]:50s} | {c.region[:30]:30s} | {c.address[:80]}")
        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
