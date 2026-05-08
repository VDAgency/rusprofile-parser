"""Диагностика Mini App через playwright.

Открывает страницу как обычный браузер (не Telegram), но смотрим:
1. Что грузится — index.html, app.v2.js (свежие?).
2. Какая версия app.v2.js (по `?v=` в src).
3. Какие fetch-запросы делает JS при переключении на «История».
4. Реакция UI на 401 (показ alert / диагностики).

Также пробует открыть страницу с подстановкой fake-Telegram WebApp,
чтобы JS подумал что есть user.id. Это не настоящая авторизация, но
покажет, проходит ли наш JS-кодом нужная ветка.
"""

import asyncio
import sys
from playwright.async_api import async_playwright

URL = "https://parserclients.ru/app/?uid=5389520473"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        # Логируем все fetch-запросы
        requests_log = []

        def on_request(req):
            if "/api/" in req.url:
                requests_log.append((req.method, req.url, dict(req.headers)))

        page.on("request", on_request)

        # Логируем console
        console_log = []
        page.on("console", lambda msg: console_log.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: console_log.append(f"[PAGEERROR] {err}"))

        # 1. Открываем Mini App
        print("[1] Open " + URL)
        resp = await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        print(f"   status={resp.status} headers cache={resp.headers.get('cache-control')}")

        await page.wait_for_timeout(1500)

        # 2. Что у нас в JS-окружении
        diag = await page.evaluate("""
            () => ({
                tgPresent: typeof window.Telegram !== 'undefined',
                tgWebAppPresent: !!(window.Telegram && window.Telegram.WebApp),
                initDataLen: (window.Telegram && window.Telegram.WebApp
                    ? (window.Telegram.WebApp.initData || '').length : -1),
                initDataUnsafeUser: (window.Telegram && window.Telegram.WebApp
                    && window.Telegram.WebApp.initDataUnsafe
                    ? window.Telegram.WebApp.initDataUnsafe.user || null : null),
                hasApiUrl: typeof apiUrl === 'function',
                hasGetUnsafeUid: typeof getUnsafeUid === 'function',
                hasApiFetchOptions: typeof apiFetchOptions === 'function',
                hasLoadHistory: typeof loadHistory === 'function',
                appJsScriptSrc: Array.from(document.scripts)
                    .map(s => s.src).filter(s => s.includes('app')),
            })
        """)
        print("[2] JS env:")
        for k, v in diag.items():
            print(f"   {k}: {v!r}")

        # 3. Симулируем подмену Telegram.WebApp.initDataUnsafe.user, чтобы наш
        #    JS пошёл по «hot path» — добавил &uid= в URL.
        print("\n[3] Подменяем tg.initDataUnsafe.user и пробуем loadHistory...")
        result = await page.evaluate("""
            () => {
                if (!window.Telegram || !window.Telegram.WebApp) {
                    return {error: 'no Telegram.WebApp at all'};
                }
                window.Telegram.WebApp.initDataUnsafe = {
                    user: { id: 5389520473, first_name: 'Test' }
                };
                if (typeof getUnsafeUid !== 'function') {
                    return {error: 'no getUnsafeUid', tg: !!window.Telegram};
                }
                return {
                    uid: getUnsafeUid(),
                    apiUrlExample: apiUrl('/api/history?limit=50'),
                };
            }
        """)
        print(f"   {result}")

        # 4. Если есть loadHistory — вызываем
        if diag.get("hasLoadHistory"):
            print("\n[4] Вызываем loadHistory force=true и смотрим fetch...")
            try:
                await page.evaluate("loadHistory(true)")
            except Exception as e:
                print(f"   loadHistory упал: {e}")
            await page.wait_for_timeout(3000)

        # 5. Что улетело на сервер
        print("\n[5] Запросы /api/:")
        for method, url, headers in requests_log:
            print(f"   {method} {url}")
            for h in ('x-telegram-init-data', 'x-telegram-user-id-unsafe',
                      'origin', 'referer'):
                v = headers.get(h)
                if v:
                    print(f"      {h}: {v[:80]}")

        # 6. Console
        print("\n[6] Console messages:")
        for m in console_log[-15:]:
            print(f"   {m}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
