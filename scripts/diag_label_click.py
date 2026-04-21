"""Пробуем кликать по label чекбокса (не по самому скрытому input).

Чекбоксы в Rusprofile стилизуются как: input[type=checkbox] display:none + label[for=id].
Клик по label через page.click() — настоящий user-event → Vue обновит state.
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
                    "phase": "req", "url": request.url, "post": request.post_data or "",
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
                    "preview": text[:400],
                })

            page.on("request", on_request)
            page.on("response", lambda r: asyncio.create_task(on_response(r)))

            for attempt in range(1, 4):
                try:
                    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    break
                except Exception:
                    await page.wait_for_timeout(3000)

            await page.wait_for_selector("#state-1", timeout=20000)
            await page.wait_for_timeout(2000)

            # Смотрим HTML рядом с чекбоксом #region-63 — где label?
            print("\nСтруктура вокруг #region-63:")
            struct = await page.evaluate(
                """() => {
                    const el = document.getElementById('region-63');
                    if (!el) return null;
                    const parent = el.parentElement;
                    return {
                        inputOuter: el.outerHTML,
                        parentOuter: parent ? parent.outerHTML.slice(0, 500) : null,
                        nextSiblingOuter: el.nextElementSibling ? el.nextElementSibling.outerHTML.slice(0, 300) : null,
                    };
                }"""
            )
            for k, v in (struct or {}).items():
                print(f"  {k}: {v}")

            # Ищем label[for="region-63"]
            print("\nlabel[for='region-63']:")
            cnt = await page.locator("label[for='region-63']").count()
            print(f"  count: {cnt}")
            if cnt:
                info = await page.locator("label[for='region-63']").first.evaluate(
                    """el => ({
                        visible: el.offsetParent !== null,
                        text: (el.innerText||'').trim(),
                        cls: el.className,
                    })"""
                )
                print(f"  {info}")

            # Возможно, регионы лежат за кнопкой-раскрытием. Попробуем раскрыть
            # секцию через клик по заголовку "Регион регистрации".
            print("\nРаскрываем секцию 'Регион регистрации'...")
            # Ищем h2/h3/button с текстом «Регион»
            opened = await page.evaluate(
                """() => {
                    // Ищем все элементы-триггеры для сворачивания/раскрытия
                    const triggers = Array.from(document.querySelectorAll(
                        '.filter-title, .filter-header, .filter-section-header, button, .dropdown-header'
                    ));
                    const byText = triggers.filter(el => {
                        const t = (el.innerText||el.textContent||'').trim();
                        return t.toLowerCase().includes('регион');
                    });
                    const out = byText.slice(0, 6).map(el => ({
                        tag: el.tagName, cls: el.className,
                        text: (el.innerText||'').trim().slice(0, 50),
                        visible: el.offsetParent !== null,
                    }));
                    // Кликнем первый видимый
                    const visible = byText.find(el => el.offsetParent !== null);
                    if (visible) visible.click();
                    return {list: out, clicked: visible ? (visible.innerText||'').trim().slice(0,50) : null};
                }"""
            )
            print(f"  {opened}")
            await page.wait_for_timeout(1500)

            # Теперь смотрим: label[for=region-63] стал видимым?
            print("\nПовторная проверка label[for='region-63']:")
            cnt2 = await page.locator("label[for='region-63']").count()
            if cnt2:
                info2 = await page.locator("label[for='region-63']").first.evaluate(
                    """el => ({
                        visible: el.offsetParent !== null,
                        text: (el.innerText||'').trim(),
                        box: el.getBoundingClientRect(),
                    })"""
                )
                print(f"  {info2}")

            # Попытка клика
            print("\nКликаем label[for='region-63']...")
            try:
                await page.locator("label[for='region-63']").first.click(timeout=5000)
                await page.wait_for_timeout(500)
                is_checked = await page.locator("#region-63").is_checked()
                print(f"  checked after click: {is_checked}")
            except Exception as e:
                print(f"  FAIL: {e}")

            # Аналогично для ОКОПФ
            print("\nРаскрываем секцию 'Организационно-правовая форма'...")
            await page.evaluate(
                """() => {
                    const triggers = Array.from(document.querySelectorAll(
                        '.filter-title, .filter-header, .filter-section-header, button'
                    ));
                    const byText = triggers.find(el => {
                        const t = (el.innerText||el.textContent||'').trim();
                        return t.toLowerCase().includes('организационно') || t.toLowerCase().includes('окопф');
                    });
                    if (byText) byText.click();
                    return byText ? (byText.innerText||'').trim().slice(0,50) : null;
                }"""
            )
            await page.wait_for_timeout(1000)
            print("Кликаем label[for='okopf-12165,12300']...")
            try:
                await page.locator("label[for='okopf-12165,12300']").first.click(timeout=5000)
                await page.wait_for_timeout(500)
                is_checked = await page.locator("#okopf-12165\\,12300").is_checked()
                print(f"  checked: {is_checked}")
            except Exception as e:
                print(f"  FAIL: {e}")

            api_calls.clear()

            # Сабмитим
            print("\nSubmit формы...")
            await page.evaluate(
                """() => {
                    const f = document.getElementById('filter-form') || document.querySelector('form');
                    if (f && f.requestSubmit) f.requestSubmit();
                    else if (f) f.submit();
                }"""
            )
            for i in range(20):
                await page.wait_for_timeout(500)
                if any(c["phase"] == "resp" for c in api_calls):
                    break
            await page.wait_for_timeout(2500)

            # Смотрим POST-тело и регионы карточек
            print("\n=== AJAX после submit ===")
            for c in api_calls:
                if c["phase"] == "req":
                    print(f"  REQ POST {c['url']}")
                    print(f"    body: {c['post']}")
                else:
                    print(f"  RESP [{c['status']}] preview: {c['preview'][:200]}")

            regs = await page.evaluate(
                """() => Array.from(document.querySelectorAll('.list-element')).slice(0,5)
                    .map(c => {
                        const a = c.querySelector('.list-element__address');
                        return a ? a.textContent.trim().slice(0,80) : '';
                    })"""
            )
            print("\nРегионы первых 5 карточек:")
            for r in regs:
                print(f"  {r}")

        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
