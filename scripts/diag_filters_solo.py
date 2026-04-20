"""Один сценарий за запуск — обходим рейт-лимит.

Запускать:
    python scripts/diag_filters_solo.py status
    python scripts/diag_filters_solo.py region
    python scripts/diag_filters_solo.py legal
    python scripts/diag_filters_solo.py msp
    python scripts/diag_filters_solo.py okved
    python scripts/diag_filters_solo.py contacts
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

URL = "https://www.rusprofile.ru/search-advanced"


async def click_label_by_name(page, input_name):
    """Кликает по label, связанному с input[name=input_name]."""
    # Vue рендерит <label for="id"><input id="id" name="name">Текст</label>
    # или <label><input name="name">Текст</label>.
    el = page.locator(f"input[name='{input_name}']").first
    try:
        # Пробуем кликнуть по родительскому label
        await el.evaluate("""el => {
            const lab = el.closest('label');
            if (lab) lab.click(); else el.click();
        }""")
        return True
    except Exception as e:
        print(f"click fail [{input_name}]: {e}")
        return False


async def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "query"

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
            await page.goto("about:blank")
            await page.goto(URL, wait_until="commit", timeout=60000)
            await page.wait_for_selector("body", timeout=30000)
            for _ in range(4):
                await page.wait_for_timeout(1500)
                try:
                    await page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                except Exception:
                    pass
            await page.wait_for_timeout(2000)

            # Разные сценарии
            if scenario == "query":
                await page.locator("input[name='query']").first.fill("ромашка")
            elif scenario == "status":
                await click_label_by_name(page, "state-1")  # Действующая
            elif scenario == "region":
                await click_label_by_name(page, "77")  # Москва
            elif scenario == "legal":
                # 12300 — ООО (по коду ОКОПФ)
                await click_label_by_name(page, "12300") or \
                    await click_label_by_name(page, "12165,12300")
            elif scenario == "msp":
                await click_label_by_name(page, "SMALL")
            elif scenario == "okved":
                await click_label_by_name(page, "46.90")
            elif scenario == "contacts":
                await click_label_by_name(page, "has_phones")
                await click_label_by_name(page, "has_sites")
            elif scenario == "combo":
                await page.locator("input[name='query']").first.fill("ромашка")
                await click_label_by_name(page, "state-1")
                await click_label_by_name(page, "77")
                await click_label_by_name(page, "has_sites")

            await page.wait_for_timeout(600)

            # Сабмит
            btn = page.locator("button.search-btn").first
            await btn.wait_for(state="visible", timeout=10000)
            await btn.click()
            try:
                await page.wait_for_url("**/search?**", timeout=12000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            final = page.url
            parsed = urlparse(final)
            qs = parse_qs(parsed.query)
            print(f"SCENARIO: {scenario}")
            print(f"URL:      {final}")
            print(f"PATH:     {parsed.path}")
            print(f"PARAMS:   {qs}")

            # Дополнительно — текст h1 с количеством
            try:
                h1 = (await page.locator("h1").first.inner_text(timeout=3000))[:160]
                print(f"H1:       {h1!r}")
            except Exception:
                pass
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
