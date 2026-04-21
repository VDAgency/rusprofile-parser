"""Один сабмит со всеми виджетами — читаем имена URL-параметров за один запрос.

Стратегия: Rusprofile блокирует серию новых браузер-сессий на /search-advanced.
Открываем страницу ровно один раз, отмечаем по чекбоксу в каждом виджете
(статус/регион/правовая форма/МСП/ОКВЭД/контакты), жмём «Найти», смотрим
итоговый URL — он и покажет реальные имена параметров для всех виджетов
сразу.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

URL = "https://www.rusprofile.ru/search-advanced"


async def click_input_parent_label(page, input_name):
    """Кликает по label-родителю input[name=...]. Vue ловит change по label."""
    return await page.evaluate(
        """(name) => {
            const el = document.querySelector(`input[name="${name}"]`);
            if (!el) return {found: false};
            const lab = el.closest('label');
            (lab || el).click();
            return {found: true, checked: el.checked};
        }""",
        input_name,
    )


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
            # Несколько попыток — rusprofile иногда рвёт сокет на старте.
            last_err = None
            for attempt in range(1, 5):
                try:
                    resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    print(f"attempt {attempt}: status={resp.status if resp else '?'} url={page.url}")
                    if page.url.startswith("http"):
                        break
                except Exception as e:
                    last_err = e
                    print(f"attempt {attempt} goto failed: {e}")
                    await page.wait_for_timeout(3000)
            else:
                raise last_err or RuntimeError("Не удалось открыть страницу")

            await page.wait_for_selector("body", timeout=30000)

            # Даём Vue полностью отрендериться (виджеты ленивые).
            for _ in range(6):
                await page.wait_for_timeout(1500)
                try:
                    await page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                except Exception:
                    pass
            await page.wait_for_timeout(2500)

            # Текстовое поле
            try:
                await page.locator("input[name='query']").first.fill("ромашка")
            except Exception as e:
                print(f"query fill fail: {e}")

            # Все виджеты за один проход.
            clicks = [
                ("state-1", "Статус: Действующая"),
                ("77", "Регион: Москва"),
                ("12300", "Правовая форма: ООО"),
                ("SMALL", "МСП: Малое"),
                ("46.90", "ОКВЭД: 46.90"),
                ("has_phones", "Контакты: телефон"),
                ("has_sites", "Контакты: сайт"),
                ("has_emails", "Контакты: email"),
                ("not_defendant", "Не был ответчиком"),
                ("finance_has_actual_year_data", "Есть актуальный отчёт"),
            ]
            for name, desc in clicks:
                try:
                    res = await click_input_parent_label(page, name)
                    print(f"  click {name:32s} ({desc:38s}) → {res}")
                except Exception as e:
                    print(f"  click {name:32s} FAIL: {e}")

            # Числовые поля
            for name, value in [
                ("finance_revenue_from", "1000000"),
                ("finance_revenue_to", "50000000"),
                ("finance_profit_from", "100000"),
            ]:
                try:
                    await page.locator(f"input[name='{name}']").first.fill(
                        value, force=True
                    )
                    print(f"  fill  {name:32s} = {value}")
                except Exception as e:
                    print(f"  fill  {name:32s} FAIL: {e}")

            await page.wait_for_timeout(1000)

            # Сабмит
            btn = page.locator("button.search-btn").first
            await btn.wait_for(state="visible", timeout=10000)
            await btn.click()
            try:
                await page.wait_for_url("**/search?**", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(2500)

            final = page.url
            parsed = urlparse(final)
            qs = parse_qs(parsed.query, keep_blank_values=True)

            print("\n" + "=" * 60)
            print(f"PATH:   {parsed.path}")
            print(f"URL:    {final}")
            print("PARAMS:")
            for k, vs in sorted(qs.items()):
                print(f"  {k:40s} = {vs}")

            try:
                h1 = (await page.locator("h1").first.inner_text(timeout=3000))[:200]
                print(f"\nH1: {h1!r}")
            except Exception:
                pass
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
