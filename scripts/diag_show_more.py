"""Разведка пагинации — клик по «Показать ещё».

Первый прогон показал:
- Vue SPA грузит данные через XHR к
  https://www.rusprofile.ru/ajax_auth.php?action=search_advanced
- В DOM после рендера есть элемент [class*='show-more']

Задача этого скрипта:
1) Открыть страницу с фильтрами (Самарская + ООО + Действующие).
2) Дождаться появления карточек и кнопки show-more.
3) Включить перехват ВСЕХ запросов с post_data и заголовками.
4) Кликнуть show-more, дождаться нового XHR к ajax_auth.php.
5) Распечатать полные параметры запроса (method, url, post_data,
   относящиеся заголовки) и превью ответа — чтобы понять, как
   Rusprofile просит вторую порцию карточек.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.filters import (
    SearchFilters, build_search_url,
    STATUS_ACTIVE, LEGAL_FORM_OOO,
)


async def main():
    filters = SearchFilters(
        status=[STATUS_ACTIVE],
        region=["63"],
        okopf=[LEGAL_FORM_OOO],
    )
    url = build_search_url(filters)
    print(f"URL стр.1: {url}\n")

    api_calls: list[dict] = []

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

        def on_request(request):
            u = request.url
            if "ajax_auth.php" not in u and "/api" not in urlparse(u).path:
                return
            try:
                hdrs = request.headers
            except Exception:
                hdrs = {}
            api_calls.append({
                "phase": "req",
                "method": request.method,
                "url": u,
                "post": request.post_data or "",
                "headers": {
                    k: v for k, v in hdrs.items()
                    if k.lower() in (
                        "content-type", "x-requested-with",
                        "accept", "referer", "cookie",
                    )
                },
            })

        async def on_response(response):
            u = response.url
            if "ajax_auth.php" not in u and "/api" not in urlparse(u).path:
                return
            try:
                body = await response.body()
                text = body.decode("utf-8", errors="replace")
            except Exception:
                text = "<no-body>"
            api_calls.append({
                "phase": "resp",
                "status": response.status,
                "url": u,
                "preview": text[:1200],
                "length": len(text),
            })

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        try:
            await page.goto("about:blank")
            for attempt in range(1, 4):
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    print(f"goto attempt {attempt}: status={resp.status if resp else '?'}")
                    break
                except Exception as e:
                    print(f"attempt {attempt} fail: {e}")
                    await page.wait_for_timeout(3000)

            # Ждём появления карточек
            print("\nЖдём первые 50 карточек...")
            try:
                await page.wait_for_selector(".list-element", timeout=30000)
            except Exception as e:
                print(f"  карточки не появились: {e}")
                return
            await page.wait_for_timeout(2000)
            cnt = await page.evaluate("document.querySelectorAll('.list-element').length")
            print(f"  карточек в DOM: {cnt}")

            # Смотрим, как выглядит кнопка show-more
            print("\nКнопка show-more:")
            info = await page.evaluate(
                """() => {
                    const el = document.querySelector("[class*='show-more']");
                    if (!el) return null;
                    return {
                        tag: el.tagName,
                        cls: el.className,
                        text: (el.innerText||'').trim(),
                        href: el.getAttribute('href'),
                        visible: el.offsetParent !== null,
                        outer: el.outerHTML.slice(0, 500),
                    };
                }"""
            )
            print(f"  {info}")

            # Сколько вызовов ajax_auth.php было на загрузку страницы 1
            before = len([c for c in api_calls if c["phase"] == "resp" and "ajax_auth.php" in c["url"]])
            print(f"\nВызовов ajax_auth.php ДО клика: {before}")

            # Кликаем
            print("\nКликаем show-more...")
            try:
                await page.locator("[class*='show-more']").first.scroll_into_view_if_needed(timeout=3000)
                await page.locator("[class*='show-more']").first.click(timeout=5000)
            except Exception as e:
                print(f"  клик не прошёл: {e}")

            # Ждём нового XHR
            for i in range(20):
                await page.wait_for_timeout(500)
                after = len([c for c in api_calls if c["phase"] == "resp" and "ajax_auth.php" in c["url"]])
                if after > before:
                    print(f"  [{i}] появился новый XHR (всего: {after})")
                    break
            await page.wait_for_timeout(2000)

            cnt_after = await page.evaluate("document.querySelectorAll('.list-element').length")
            print(f"\nКарточек в DOM после клика: {cnt_after}")

            # Печатаем ВСЕ вызовы ajax_auth.php — запросы и ответы
            print("\n=== ВСЕ XHR к ajax_auth.php ===")
            for i, c in enumerate(api_calls):
                if "ajax_auth.php" not in c.get("url", ""):
                    continue
                if c["phase"] == "req":
                    print(f"\n[{i}] REQ {c['method']} {c['url']}")
                    print(f"   headers: {c['headers']}")
                    print(f"   post_data: {c['post'][:1500]}")
                else:
                    print(f"\n[{i}] RESP [{c['status']}] len={c['length']}")
                    print(f"   preview: {c['preview'][:800]}")

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
