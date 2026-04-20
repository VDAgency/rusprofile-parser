"""Заполняем форму /search-advanced частично и смотрим, какой URL строит сайт.

Задача — узнать реальные имена URL-параметров для фильтров, которые реализованы
Vue-компонентами (регион/ОКВЭД/статус/правовая форма/контакты).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright


URL = "https://www.rusprofile.ru/search-advanced"


async def open_panel(page, header_text):
    """Раскрывает toggle-fields по заголовку раздела (если они свернуты)."""
    try:
        btn = page.locator(f".filter-block__header:has-text('{header_text}')").first
        if await btn.is_visible(timeout=2000):
            await btn.click()
            await page.wait_for_timeout(400)
    except Exception:
        pass


async def submit_and_get_url(page, scenario):
    """scenario — callable, получает page и заполняет форму. Возвращает URL результата."""
    await page.goto("about:blank")
    await page.goto(URL, wait_until="commit", timeout=45000)
    await page.wait_for_selector("body", timeout=30000)
    await page.wait_for_timeout(3500)
    try:
        await scenario(page)
    except Exception as e:
        print(f"    scenario failed: {e}")

    # Кнопка «Найти» / «Показать»
    btns = [
        "button:has-text('Найти')",
        "button:has-text('Показать')",
        "a:has-text('Найти')",
        "a:has-text('Показать')",
        ".filter-block__submit",
        "button.btn-blue[type=submit]",
    ]
    clicked = False
    for sel in btns:
        try:
            b = page.locator(sel).first
            if await b.is_visible(timeout=1500):
                await b.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        # отправим форму вручную через JS
        await page.evaluate("document.querySelector('form.form')?.requestSubmit?.()")

    try:
        await page.wait_for_url("**/search**", timeout=10000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)
    return page.url


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
            async def s_query(p):
                q = p.locator("input[name='query']").first
                await q.fill("ромашка")

            async def s_revenue(p):
                await p.locator("input[name='finance_revenue_from']").first.fill("1000000")
                await p.locator("input[name='finance_revenue_to']").first.fill("50000000")

            async def s_region(p):
                # Пробуем раскрыть и кликнуть чекбокс «Москва»
                await open_panel(p, "Регион")
                # ищем input/label «Москва»
                try:
                    lab = p.locator("label:has-text('Москва')").first
                    if await lab.is_visible(timeout=2500):
                        await lab.click()
                        return
                except Exception:
                    pass
                # или кликаем по tree-list-filter
                try:
                    t = p.locator(".tree-list-filter-btn, .tree-list-filter").first
                    if await t.is_visible(timeout=1500):
                        await t.click()
                        await p.wait_for_timeout(600)
                        lab = p.locator("label:has-text('Москва')").first
                        if await lab.is_visible(timeout=2500):
                            await lab.click()
                except Exception:
                    pass

            async def s_status(p):
                await open_panel(p, "Статус")
                for lbl in ["Действующие", "Действующая", "Действующий"]:
                    try:
                        l = p.locator(f"label:has-text('{lbl}')").first
                        if await l.is_visible(timeout=1200):
                            await l.click()
                            return
                    except Exception:
                        continue

            async def s_contacts(p):
                await open_panel(p, "Контакты")
                for lbl in ["сайт", "телефон", "email", "e-mail", "почту"]:
                    try:
                        l = p.locator(f"label:has-text('{lbl}')").first
                        if await l.is_visible(timeout=1200):
                            await l.click()
                    except Exception:
                        continue

            async def s_legal_form(p):
                await open_panel(p, "Правовая форма")
                for lbl in ["ООО", "АО", "ИП", "Общество с ограниченной"]:
                    try:
                        l = p.locator(f"label:has-text('{lbl}')").first
                        if await l.is_visible(timeout=1200):
                            await l.click()
                            return
                    except Exception:
                        continue

            # Сценарии
            scenarios = [
                ("query", s_query),
                ("revenue", s_revenue),
                ("region_moscow", s_region),
                ("status_active", s_status),
                ("contacts_any", s_contacts),
                ("legal_form_ooo", s_legal_form),
            ]

            print("\n=== реальные URL после сабмита ===")
            for name, fn in scenarios:
                try:
                    url = await submit_and_get_url(page, fn)
                    print(f"  [{name:20s}] → {url}")
                except Exception as e:
                    print(f"  [{name:20s}] !! {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
