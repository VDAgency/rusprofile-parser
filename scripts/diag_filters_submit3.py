"""Сабмит нескольких сценариев → извлекаем реальные URL-параметры.

Для каждого фильтра: открываем /search-advanced, кликаем ровно один
компонент (например, Москва в регионе), нажимаем «Найти», читаем URL.
Так узнаём настоящее имя параметра для каждого виджета."""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

URL = "https://www.rusprofile.ru/search-advanced"


async def load_page(ctx):
    page = await ctx.new_page()
    await page.goto("about:blank")
    await page.goto(URL, wait_until="commit", timeout=45000)
    await page.wait_for_selector("body", timeout=30000)
    # Даём Vue время отрендерить tree-list компоненты
    for _ in range(4):
        await page.wait_for_timeout(1200)
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
    await page.wait_for_timeout(1500)
    return page


async def submit(page):
    btn = page.locator("button.search-btn, button:has-text('Найти')").first
    await btn.click()
    try:
        await page.wait_for_url("**/search?**", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)
    return page.url


async def scenario_query_only(ctx):
    page = await load_page(ctx)
    try:
        await page.locator("input[name='query']").first.fill("ромашка")
        return await submit(page)
    finally:
        await page.close()


async def scenario_revenue(ctx):
    page = await load_page(ctx)
    try:
        # Раскрываем блок «Выручка»
        try:
            hdr = page.locator(".filter-block__header:has-text('Выручка')").first
            if await hdr.is_visible(timeout=1500):
                await hdr.click()
                await page.wait_for_timeout(400)
        except Exception:
            pass
        await page.locator("input[name='finance_revenue_from']").first.fill(
            "1000000", force=True
        )
        await page.locator("input[name='finance_revenue_to']").first.fill(
            "50000000", force=True
        )
        return await submit(page)
    finally:
        await page.close()


async def click_in_tree(page, section_header, label_text):
    """Раскрывает секцию и кликает по чекбоксу с нужным текстом внутри."""
    # Раскрываем блок
    try:
        hdr = page.locator(
            f".filter-block__header:has-text('{section_header}')"
        ).first
        if await hdr.is_visible(timeout=1500):
            await hdr.click()
            await page.wait_for_timeout(400)
    except Exception:
        pass

    # Ищем label внутри соседнего filter-block
    loc = page.locator(
        f".filter-block:has(.filter-block__header:has-text('{section_header}'))"
        f" label:has-text('{label_text}')"
    ).first
    try:
        await loc.click(timeout=3000, force=True)
        return True
    except Exception as e:
        print(f"    click failed for [{section_header}→{label_text}]: {e}")
        return False


async def scenario_section(ctx, section, value):
    page = await load_page(ctx)
    try:
        await click_in_tree(page, section, value)
        await page.wait_for_timeout(500)
        return await submit(page)
    finally:
        await page.close()


async def main():
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
        try:
            scenarios = [
                ("query", scenario_query_only, None, None),
                ("revenue", scenario_revenue, None, None),
                ("status_active", scenario_section, "Статус", "Действующая"),
                ("region_moscow", scenario_section, "Регион", "Москва"),
                ("legal_ooo", scenario_section, "Правовая форма", "Общество с ограниченной"),
                ("contacts_site", scenario_section, "Контакты", "Есть сайт"),
                ("contacts_phone", scenario_section, "Контакты", "Есть телефон"),
                ("contacts_email", scenario_section, "Контакты", "Есть email"),
                ("msp_small", scenario_section, "Реестр МСП", "Малое"),
                ("msp_micro", scenario_section, "Реестр МСП", "Микро"),
            ]

            print("=== результаты сабмита ===")
            for name, fn, arg1, arg2 in scenarios:
                try:
                    if arg1 is None:
                        url = await fn(ctx)
                    else:
                        url = await fn(ctx, arg1, arg2)
                    parsed = urlparse(url)
                    qs = parse_qs(parsed.query)
                    print(f"\n[{name}]")
                    print(f"  url: {url[:200]}")
                    print(f"  path: {parsed.path}")
                    print(f"  params: {qs}")
                except Exception as e:
                    print(f"[{name}] !! {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
