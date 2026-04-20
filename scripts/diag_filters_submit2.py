"""Ждём полной инициализации Vue, заполняем форму и смотрим итоговый URL."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

URL = "https://www.rusprofile.ru/search-advanced"
OUT = Path(__file__).resolve().parent.parent / "logs" / "diag"
OUT.mkdir(parents=True, exist_ok=True)


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
        page = await ctx.new_page()
        try:
            await page.goto("about:blank")
            await page.goto(URL, wait_until="commit", timeout=45000)
            await page.wait_for_selector("body", timeout=30000)
            # Ждём 10 секунд Vue-рендера и скроллим, чтобы ленивые компоненты
            # подтянулись
            for _ in range(5):
                await page.wait_for_timeout(1500)
                await page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
            await page.wait_for_timeout(1500)

            html = await page.content()
            (OUT / "search-advanced-vue.html").write_text(html, encoding="utf-8")
            print(f"saved: logs/diag/search-advanced-vue.html, len={len(html)}")

            # Список всех кнопок/ссылок — ищем «Найти»
            buttons = await page.evaluate(
                """() => Array.from(document.querySelectorAll('button, a'))
                    .map(b => ({text: (b.textContent||'').trim().slice(0,40),
                                 tag: b.tagName,
                                 cls: b.className,
                                 href: b.getAttribute('href'),
                                 type: b.getAttribute('type')}))
                    .filter(x => x.text && (x.text.includes('Найти') || x.text.includes('Показать') || x.text.includes('Поиск')))"""
            )
            print("buttons found:")
            for b in buttons[:30]:
                print("  ", b)

            # Заполним query и посмотрим что меняется в DOM
            q = page.locator("input[name='query']").first
            await q.fill("ромашка")
            await page.wait_for_timeout(1000)

            # Попробуем найти и кликнуть кнопку «Найти» — перебираем варианты
            submit_selectors = [
                "button:has-text('Найти')",
                "a:has-text('Найти')",
                "[type='submit']",
                ".filter-block__submit",
                ".advanced-filters-search",
                ".btn-blue",
            ]
            for sel in submit_selectors:
                loc = page.locator(sel).first
                try:
                    if await loc.is_visible(timeout=800):
                        text = (await loc.text_content() or "").strip()[:40]
                        print(f"clicking {sel!r} text={text!r}")
                        await loc.click()
                        await page.wait_for_timeout(3000)
                        print(f"   → url: {page.url}")
                        break
                except Exception:
                    continue
            else:
                print("submit button not found")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
