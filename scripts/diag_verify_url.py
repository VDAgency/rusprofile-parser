"""Проверка: собранный build_search_url открывается и даёт результаты.

Генерируем URL с парой разумных фильтров, открываем его в Playwright,
смотрим H1 и количество найденных организаций.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.filters import (
    SearchFilters,
    build_search_url,
    STATUS_ACTIVE,
    MSP_SMALL,
    LEGAL_FORM_OOO,
)


async def check_url(page, label, url):
    print(f"\n=== {label} ===")
    print(f"URL: {url}")
    for attempt in range(1, 4):
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            print(f"  attempt {attempt}: status={resp.status if resp else '?'}")
            break
        except Exception as e:
            print(f"  attempt {attempt} fail: {e}")
            await page.wait_for_timeout(3000)
    await page.wait_for_timeout(3000)
    try:
        h1 = (await page.locator("h1").first.inner_text(timeout=5000))[:250]
        print(f"  H1: {h1!r}")
    except Exception as e:
        print(f"  H1 fail: {e}")
    try:
        final = page.url
        print(f"  final URL: {final}")
        parsed = urlparse(final)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        non_empty = {k: v for k, v in qs.items() if v and v[0] != ""}
        print(f"  NON-EMPTY PARAMS ({len(non_empty)}): {sorted(non_empty.keys())}")
    except Exception:
        pass


async def main():
    scenarios = [
        (
            "только query",
            SearchFilters(query="ромашка"),
        ),
        (
            "Москва + ООО + Действующие",
            SearchFilters(
                status=[STATUS_ACTIVE],
                region=["97,77"],
                okopf=[LEGAL_FORM_OOO],
            ),
        ),
        (
            "Малый бизнес с контактами и отчётом, ОКВЭД 46.9",
            SearchFilters(
                status=[STATUS_ACTIVE],
                region=["97,77"],
                okopf=[LEGAL_FORM_OOO],
                msp=[MSP_SMALL],
                okved=["46.9"],
                has_phones=True,
                has_sites=True,
                finance_has_actual_year_data=True,
                finance_revenue_from=1000000,
            ),
        ),
    ]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = await ctx.new_page()
        try:
            # Прогрев — один общий контекст избегает rate-limit на сериях
            # новых сессий (мы это уже выяснили в прошлой диагностике).
            await page.goto("about:blank")
            for label, filters in scenarios:
                await check_url(page, label, build_search_url(filters))
                # Небольшая пауза между запросами, чтобы не срывать лимит.
                await page.wait_for_timeout(4000)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
