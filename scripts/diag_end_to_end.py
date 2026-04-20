"""Сквозной тест: логин → парсинг выдачи → обогащение первых 3 карточек."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright
from src.rusprofile.auth import get_authenticated_context
from src.rusprofile.filters import SearchFilters
from src.rusprofile.parser import parse_search_results, enrich_company_details


async def main():
    async with async_playwright() as pw:
        ctx = await get_authenticated_context(pw)
        try:
            filters = SearchFilters(query="ромашка")
            print("=== searching ...")
            companies = await parse_search_results(ctx, filters)
            print(f"=== всего найдено: {len(companies)}")
            for c in companies[:5]:
                print(f"  {c.name[:50]:50s} | ИНН {c.inn} | ОГРН {c.ogrn}")
                print(f"    addr: {c.address[:100]}")
                print(f"    okved: {c.okved[:80]}")
                print(f"    href: {c.detail_href}")

            if companies:
                print("\n=== enrich first 3 ...")
                enriched = await enrich_company_details(ctx, companies[:3])
                for c in enriched:
                    print(f"  {c.name[:40]}: phone={c.phone!r} email={c.email!r} site={c.site!r}")
                    print(f"    revenue={c.revenue!r} profit={c.profit!r}")
        finally:
            await ctx.browser.close()


if __name__ == "__main__":
    asyncio.run(main())
