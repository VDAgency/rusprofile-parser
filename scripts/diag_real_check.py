"""Проверка: реагирует ли Vue на Playwright page.check() (настоящий event).

Предыдущие попытки через `el.checked = true` + dispatchEvent не обновляли
Vue-state — POST-тело уходило с `{"state-1":true,"okved_strict":true}`.

Playwright page.check() кликает элемент как пользователь (isTrusted=true),
Vue такое событие обычно обрабатывает корректно. Проверяем, попадут ли
фильтры по региону и ОКОПФ в реальное POST-тело ajax_auth.php.

Далее — если работает — пробуем клик по странице «2» в пагинаторе
и смотрим, что меняется в POST-теле (page? offset?).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context


URL = "https://www.rusprofile.ru/search-advanced"


async def main():
    api_calls: list[dict] = []

    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()

            def on_request(request):
                if "ajax_auth.php" not in request.url:
                    return
                api_calls.append({
                    "phase": "req", "method": request.method,
                    "url": request.url, "post": request.post_data or "",
                })

            async def on_response(response):
                if "ajax_auth.php" not in response.url:
                    return
                try:
                    body = await response.body()
                    text = body.decode("utf-8", errors="replace")
                except Exception:
                    text = "<no-body>"
                api_calls.append({
                    "phase": "resp", "status": response.status,
                    "url": response.url, "preview": text[:800],
                })

            page.on("request", on_request)
            page.on("response", lambda r: asyncio.create_task(on_response(r)))

            for attempt in range(1, 4):
                try:
                    resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    print(f"goto: status={resp.status if resp else '?'}")
                    break
                except Exception as e:
                    print(f"goto fail: {e}")
                    await page.wait_for_timeout(3000)

            # Ждём появления чекбоксов
            await page.wait_for_selector("#state-1", timeout=20000)
            await page.wait_for_timeout(2000)

            # Сбрасываем state-1 если он уже отмечен (иногда по умолчанию)
            state1_checked = await page.locator("#state-1").is_checked()
            print(f"state-1 checked by default: {state1_checked}")

            # Для клика чекбоксы должны быть видимыми — раскрываем список регионов
            print("\nРаскрываем список регионов (клик по заголовку 'Регион')...")
            try:
                # Ищем заголовок раздела «Регион»
                await page.get_by_text("Регион", exact=False).first.click(timeout=5000)
                await page.wait_for_timeout(500)
            except Exception as e:
                print(f"  не получилось: {e}")

            print("\nRegional checkbox #region-63 — существует?")
            exists = await page.locator("#region-63").count()
            print(f"  count: {exists}")

            # Чекбоксы в Rusprofile часто скрыты (display:none) для кастомной стилизации.
            # Playwright check поддерживает force=True для скрытых.
            print("\npage.check('#region-63', force=True)...")
            try:
                await page.check("#region-63", force=True, timeout=5000)
                print(f"  checked: {await page.locator('#region-63').is_checked()}")
            except Exception as e:
                print(f"  FAIL: {e}")

            print("\npage.check('#okopf-12165\\\\,12300', force=True)...")
            try:
                await page.check("#okopf-12165\\,12300", force=True, timeout=5000)
                print(f"  checked: {await page.locator('#okopf-12165\\,12300').is_checked()}")
            except Exception as e:
                print(f"  FAIL: {e}")

            if not state1_checked:
                await page.check("#state-1", force=True, timeout=5000)

            await page.wait_for_timeout(1000)
            api_calls.clear()

            print("\nСабмитим форму (requestSubmit)...")
            await page.evaluate(
                """() => {
                    const f = document.getElementById('filter-form') || document.querySelector('form');
                    if (f && f.requestSubmit) f.requestSubmit();
                    else if (f) f.submit();
                }"""
            )
            # Ждём AJAX
            for i in range(20):
                await page.wait_for_timeout(500)
                if any(c["phase"] == "resp" for c in api_calls):
                    break
            await page.wait_for_timeout(2500)

            print(f"\nФинальный URL: {page.url}")
            regs = await page.evaluate(
                """() => Array.from(document.querySelectorAll('.list-element')).slice(0, 6)
                    .map(c => {
                        const a = c.querySelector('.list-element__address');
                        return a ? a.textContent.trim().slice(0, 80) : '';
                    })"""
            )
            print("Регионы первых 6 карточек:")
            for r in regs:
                print(f"  {r}")

            print("\n=== AJAX после сабмита ===")
            for c in api_calls:
                if c["phase"] == "req":
                    print(f"\nREQ {c['method']} {c['url']}")
                    print(f"  POST: {c['post']}")
                else:
                    print(f"\nRESP [{c['status']}]")
                    print(f"  preview: {c['preview']}")

            # ----- Клик по пагинатору «2» -----
            print("\n--- Пробуем клик на '2' в пагинаторе (page.click) ---")
            api_calls.clear()
            try:
                # Локаторы внутри .paging-list
                loc = page.locator(".paging-list a, .paging-list li, .paging-list button").filter(has_text="2")
                cnt = await loc.count()
                print(f"  кандидатов: {cnt}")
                if cnt:
                    await loc.first.click(timeout=5000)
                    print("  кликнули")
            except Exception as e:
                print(f"  клик fail: {e}")

            for i in range(20):
                await page.wait_for_timeout(500)
                if any(c["phase"] == "resp" for c in api_calls):
                    break
            await page.wait_for_timeout(2000)

            print(f"\nURL после клика: {page.url}")
            regs2 = await page.evaluate(
                """() => Array.from(document.querySelectorAll('.list-element')).slice(0, 6)
                    .map(c => {
                        const a = c.querySelector('.list-element__address');
                        return a ? a.textContent.trim().slice(0, 80) : '';
                    })"""
            )
            print("Регионы первых 6 на 'стр. 2':")
            for r in regs2:
                print(f"  {r}")

            print("\n=== AJAX после клика '2' ===")
            for c in api_calls:
                if c["phase"] == "req":
                    print(f"\nREQ {c['method']} {c['url']}")
                    print(f"  POST: {c['post']}")
                else:
                    print(f"\nRESP [{c['status']}]")
                    print(f"  preview: {c['preview']}")

        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
