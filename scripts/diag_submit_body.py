"""Перехватывает POST-тело AJAX, когда форма /search-advanced реально сабмитится.

Что делаем:
1. Авторизуемся (get_authenticated_context).
2. Открываем /search-advanced, ждём рендера Vue.
3. Через dispatchEvent отмечаем чекбокс Самарской обл. (#region-63)
   и статус «Действующая» (#state-1) и ОКОПФ «ООО» (#okopf-12165\\,12300).
4. Сабмитим форму (requestSubmit).
5. Перехватываем POST-запросы к /ajax_auth.php и печатаем:
   - method, url, заголовки
   - полный post_data
   - превью ответа
6. Для стр.2 — смотрим, что изменится в post_data.

Цель — узнать точные ключи, которые надо класть в JSON-body,
чтобы сервер возвращал отфильтрованные данные и можно было
пагинировать через `page` / `offset`.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context


URL = "https://www.rusprofile.ru/search-advanced"


async def set_checkbox(page, sel, desc):
    try:
        r = await page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return {found: false};
                el.checked = true;
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('input', {bubbles: true}));
                return {found: true, name: el.name, value: el.value};
            }""",
            sel,
        )
        print(f"  {desc}: {r}")
    except Exception as e:
        print(f"  {desc}: FAIL {e}")


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
                    "phase": "req",
                    "method": request.method,
                    "url": request.url,
                    "post": request.post_data or "",
                    "headers": {
                        k: v for k, v in request.headers.items()
                        if k.lower() in ("content-type", "accept", "referer")
                    },
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
                    "phase": "resp",
                    "status": response.status,
                    "url": response.url,
                    "preview": text[:1500],
                    "length": len(text),
                })

            page.on("request", on_request)
            page.on("response", lambda r: asyncio.create_task(on_response(r)))

            # Заходим на форму
            for attempt in range(1, 4):
                try:
                    resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    print(f"goto: status={resp.status if resp else '?'}")
                    break
                except Exception as e:
                    print(f"goto fail: {e}")
                    await page.wait_for_timeout(3000)

            for _ in range(5):
                await page.wait_for_timeout(1500)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            await page.evaluate("window.scrollTo(0, 0)")

            print("\nОтмечаем фильтры: Самарская + ООО + Действующие")
            await set_checkbox(page, "#state-1", "state-1")
            # В HTML чекбоксы регионов имеют id вида #region-63
            await set_checkbox(page, "#region-63", "region-63")
            await set_checkbox(page, "#okopf-12165\\,12300", "okopf-12165,12300")
            await page.wait_for_timeout(800)

            # Очищаем лог перед сабмитом
            api_calls.clear()

            print("\nСабмитим форму...")
            ok = await page.evaluate(
                """() => {
                    const form = document.getElementById('filter-form') || document.querySelector('form');
                    if (!form) return 'no-form';
                    if (typeof form.requestSubmit === 'function') { form.requestSubmit(); return 'requestSubmit'; }
                    form.submit(); return 'submit';
                }"""
            )
            print(f"  submit: {ok}")

            # Ждём, пока придёт ответ и карточки перерендерятся
            for i in range(20):
                await page.wait_for_timeout(500)
                cnt = await page.evaluate("document.querySelectorAll('.list-element').length")
                resps = [c for c in api_calls if c["phase"] == "resp"]
                if cnt > 0 and resps:
                    print(f"  [{i}] карточек {cnt}, ajax ответов {len(resps)}")
                    break

            await page.wait_for_timeout(3000)
            print(f"\nФинальный URL: {page.url}")

            # Что пришло — посмотрим регионы первых 3 карточек:
            print("\nРегионы первых 5 карточек:")
            regs = await page.evaluate(
                """() => Array.from(document.querySelectorAll('.list-element'))
                    .slice(0, 5)
                    .map(c => {
                        const a = c.querySelector('.list-element__address');
                        return a ? a.textContent.trim().slice(0, 80) : '';
                    })"""
            )
            for r in regs:
                print(f"  {r}")

            print("\n=== XHR после сабмита ===")
            for c in api_calls:
                if c["phase"] == "req":
                    print(f"\nREQ {c['method']} {c['url']}")
                    print(f"   headers: {c['headers']}")
                    print(f"   POST: {c['post']}")
                else:
                    print(f"\nRESP [{c['status']}] len={c['length']}")
                    # Находим первую компанию в JSON и её регион
                    print(f"   preview: {c['preview'][:800]}")

            # Пробуем кликнуть на страницу "2" в пагинаторе
            print("\n--- Клик по пагинатору «2» ---")
            api_calls.clear()
            clicked = await page.evaluate(
                """() => {
                    // Внутри .paging-list ищем "2"
                    const items = Array.from(document.querySelectorAll('.paging-list a, .paging-list button, .paging-list li'));
                    const by2 = items.find(el => (el.textContent||'').trim() === '2');
                    if (by2) { by2.click(); return 'clicked-2'; }
                    return 'not-found';
                }"""
            )
            print(f"  результат: {clicked}")
            for i in range(20):
                await page.wait_for_timeout(500)
                resps = [c for c in api_calls if c["phase"] == "resp"]
                if resps:
                    print(f"  [{i}] ajax ответов после клика-2: {len(resps)}")
                    break
            await page.wait_for_timeout(2000)

            print("\n=== XHR после клика «2» ===")
            for c in api_calls:
                if c["phase"] == "req":
                    print(f"\nREQ {c['method']} {c['url']}")
                    print(f"   POST: {c['post']}")
                else:
                    print(f"\nRESP [{c['status']}] len={c['length']}")
                    print(f"   preview: {c['preview'][:600]}")

        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
