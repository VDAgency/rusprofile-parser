"""Ищем CSRF-токен: смотрим все заголовки Vue-запроса, meta-теги, cookies."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context


URL = "https://www.rusprofile.ru/search-advanced"


async def main():
    captured = []

    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()

            def on_request(request):
                if "ajax_auth.php" not in request.url:
                    return
                captured.append({
                    "method": request.method,
                    "url": request.url,
                    "headers": dict(request.headers),  # ВСЕ заголовки
                    "post": request.post_data or "",
                })

            page.on("request", on_request)

            for attempt in range(1, 4):
                try:
                    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    break
                except Exception:
                    await page.wait_for_timeout(3000)

            await page.wait_for_timeout(3000)

            # Ждём XHR
            for _ in range(20):
                if captured:
                    break
                await page.wait_for_timeout(500)

            print(f"Перехвачено запросов: {len(captured)}")
            for i, c in enumerate(captured):
                print(f"\n[{i}] {c['method']} {c['url']}")
                print(f"   POST: {c['post']}")
                print(f"   ВСЕ ЗАГОЛОВКИ:")
                for k, v in sorted(c["headers"].items()):
                    vv = v if len(v) < 200 else v[:200] + "..."
                    print(f"     {k}: {vv}")

            # Meta-теги
            print("\n=== META-теги на странице ===")
            metas = await page.evaluate(
                """() => Array.from(document.querySelectorAll('meta'))
                    .map(m => ({
                        name: m.getAttribute('name'),
                        content: (m.getAttribute('content') || '').slice(0, 100),
                        httpEquiv: m.getAttribute('http-equiv'),
                    }))
                    .filter(m => m.name || m.httpEquiv)"""
            )
            for m in metas:
                print(f"  {m}")

            # Глобальные переменные, которые часто хранят CSRF
            print("\n=== window.csrf_token / _csrf / _token ===")
            globs = await page.evaluate(
                """() => ({
                    csrf_token: window.csrf_token,
                    _csrf: window._csrf,
                    _token: window._token,
                    CSRF_TOKEN: window.CSRF_TOKEN,
                    RUSPROFILE_CSRF: window.RUSPROFILE_CSRF,
                    // Проверим, нет ли у body data-атрибута
                    bodyData: Object.assign({}, document.body.dataset),
                    // Может есть в <html>
                    htmlData: Object.assign({}, document.documentElement.dataset),
                })"""
            )
            print(f"  {globs}")

            # Cookies
            print("\n=== Cookies ===")
            cookies = await context.cookies("https://www.rusprofile.ru/")
            for c in cookies:
                nm = c["name"]
                val = c["value"]
                if len(val) > 80:
                    val = val[:80] + "..."
                if any(k in nm.lower() for k in ("csrf", "xsrf", "token", "session")):
                    print(f"  [!] {nm}: {val}")
                else:
                    print(f"      {nm}: {val}")

            # Поиск слова CSRF в исходнике страницы
            print("\n=== Упоминания 'csrf' в HTML (до 400 симв. контекста) ===")
            html = await page.content()
            idx = 0
            found = 0
            hay = html.lower()
            while True:
                k = hay.find("csrf", idx)
                if k < 0 or found >= 6:
                    break
                start = max(0, k - 100)
                end = min(len(html), k + 200)
                print(f"  …{html[start:end]}…")
                idx = k + 4
                found += 1

        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
