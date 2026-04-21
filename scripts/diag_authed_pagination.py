"""Разведка пагинации в АВТОРИЗОВАННОМ контексте.

Анонимный прогон показал:
- Первая страница (50 карточек) подгружается через XHR:
    POST /ajax_auth.php?action=search_advanced
    body: {"state-1":true,"okved_strict":true}
- Но POST-тело НЕ содержит фильтры из URL — Rusprofile похоже
  отдаёт первые 50 по SSR через HTML, а XHR используется только
  для «Показать все 7 847 177 организаций» (это CTA на подписку).
- В анонимной сессии пагинации нет вообще.

Гипотеза: пагинация существует только для авторизованных
(и, возможно, премиум-) пользователей. Проверяем это:
1. Используем боевой get_authenticated_context() — он восстановит
   сохранённые cookies или выполнит логин.
2. Открываем ту же страницу поиска, ждём рендера.
3. Скроллим, перехватываем XHR, ищем кнопки пагинации —
   в DOM и по тексту.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context
from src.rusprofile.filters import (
    SearchFilters, build_search_url,
    STATUS_ACTIVE, LEGAL_FORM_OOO,
)


async def main():
    filters = SearchFilters(
        status=[STATUS_ACTIVE],
        region=["63"],            # Самарская
        okopf=[LEGAL_FORM_OOO],
    )
    url = build_search_url(filters)
    print(f"URL: {url}\n")

    api_calls: list[dict] = []

    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()

            def on_request(request):
                u = request.url
                path = urlparse(u).path
                # Интересно всё, что не картинки/шрифты/CSS и идёт на rusprofile.ru
                rt = request.resource_type
                if rt in ("image", "font", "stylesheet", "media"):
                    return
                if "rusprofile.ru" not in u:
                    return
                api_calls.append({
                    "phase": "req",
                    "method": request.method,
                    "type": rt,
                    "url": u,
                    "post": (request.post_data or "")[:1500],
                })

            async def on_response(response):
                u = response.url
                path = urlparse(u).path
                rt = response.request.resource_type
                if rt in ("image", "font", "stylesheet", "media"):
                    return
                if "rusprofile.ru" not in u:
                    return
                text = ""
                if "ajax_auth.php" in u or "search" in path or "/api" in path:
                    try:
                        body = await response.body()
                        text = body.decode("utf-8", errors="replace")[:1500]
                    except Exception:
                        text = "<no-body>"
                api_calls.append({
                    "phase": "resp",
                    "status": response.status,
                    "type": rt,
                    "url": u,
                    "preview": text,
                })

            page.on("request", on_request)
            page.on("response", lambda r: asyncio.create_task(on_response(r)))

            # Открываем поиск
            for attempt in range(1, 4):
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    print(f"goto attempt {attempt}: status={resp.status if resp else '?'}")
                    break
                except Exception as e:
                    print(f"attempt {attempt} fail: {e}")
                    await page.wait_for_timeout(3000)

            # Ждём появления хотя бы одной карточки
            try:
                await page.wait_for_selector(".list-element", timeout=30000)
            except Exception as e:
                print(f"Карточки не появились: {e}")
                return

            # Проверяем, залогинены ли мы (триггер меню)
            logged = await page.evaluate(
                """() => {
                    const t = document.querySelector('#menu-personal-trigger');
                    return t ? (t.textContent||'').trim() : '(нет)';
                }"""
            )
            print(f"\n#menu-personal-trigger: {logged!r}")

            await page.wait_for_timeout(2000)
            cnt = await page.evaluate("document.querySelectorAll('.list-element').length")
            print(f"Карточек до скролла: {cnt}")

            # Скроллим — вдруг infinite scroll оживает после логина
            print("\nСкроллим...")
            prev = 0
            for i in range(15):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)
                cur = await page.evaluate("document.querySelectorAll('.list-element').length")
                print(f"  скролл {i}: карточек {cur}")
                if cur == prev and i > 2:
                    break
                prev = cur

            # Смотрим кнопки/ссылки
            print("\n--- Кнопки «show-more» и подобные ---")
            info = await page.evaluate(
                """() => {
                    const out = [];
                    document.querySelectorAll("[class*='show-more'], [class*='load-more'], [class*='more'], [class*='pagin'], [class*='pager']").forEach(el => {
                        out.push({
                            tag: el.tagName,
                            cls: el.className,
                            text: (el.innerText||'').trim().slice(0,80),
                            href: el.getAttribute('href'),
                            visible: el.offsetParent !== null,
                        });
                    });
                    return out;
                }"""
            )
            for it in info:
                print(f"  {it}")

            # Пробуем кликнуть «Показать все» / «Показать ещё» / «Далее»
            print("\n--- Пробуем клик по pagination-кнопке ---")
            clicked = await page.evaluate(
                """() => {
                    const cand = document.querySelector("[class*='show-more'] button, [class*='load-more'] button, button[class*='more'], a[class*='more']");
                    if (!cand) return null;
                    const info = {text: (cand.innerText||'').trim(), tag: cand.tagName, cls: cand.className};
                    cand.click();
                    return info;
                }"""
            )
            print(f"  кликнули: {clicked}")
            await page.wait_for_timeout(5000)
            cnt2 = await page.evaluate("document.querySelectorAll('.list-element').length")
            print(f"Карточек после клика: {cnt2}")

            # Если клик сработал и появились новые карточки — отлично.
            # Смотрим XHR, которые ушли после клика
            print(f"\n=== Всего XHR/fetch записано: {len(api_calls)} ===")
            for c in api_calls:
                u = c.get("url", "")
                if "ajax_auth.php" in u:
                    if c["phase"] == "req":
                        print(f"\nREQ {c['method']} {u}")
                        print(f"  post: {c['post']}")
                    else:
                        print(f"\nRESP [{c['status']}] {u}")
                        print(f"  preview: {c['preview'][:500]}")

            # Выводим URL страницы после действий — вдруг Vue поменял URL
            print(f"\nФинальный URL: {page.url}")

        finally:
            await context.browser.close() if context.browser else None


if __name__ == "__main__":
    asyncio.run(main())
