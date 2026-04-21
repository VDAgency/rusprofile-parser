"""Перехват POST к ajax_auth.php через Playwright page.route() с подменой тела.

Предыдущий подход (патч window.fetch) не сработал: Vue отправляет запрос
либо через XMLHttpRequest, либо fetch навигирует страницу до того, как
мы успеваем прочитать window.__lastResponse. Route-интерсептер работает
на уровне браузерной сети — не важно, fetch это или XHR.

Сценарий:
1. Регистрируем route для **/ajax_auth.php?*.
2. Внутри handler парсим оригинальное POST-тело, Object.assign(overrides),
   отдаём запрос дальше с новым body — остальные заголовки, включая
   X-CSRF-Token, сохраняются (Vue сам вставляет свежий токен).
3. Через page.on('response') ловим ответ и складываем в список.
4. Кликаем кнопку сабмита формы (или dispatch submit) — ждём ответа.
5. Повторяем с разными overrides: регион, ОКОПФ, page=2, query.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context


URL = "https://www.rusprofile.ru/search-advanced"


def describe(tag: str, sent_body: str | None, raw_resp: str | None):
    print(f"\n--- {tag} ---")
    print(f"  sent body: {sent_body}")
    if not raw_resp:
        print(f"  (нет ответа)")
        return
    try:
        j = json.loads(raw_resp)
    except Exception:
        print(f"  raw[:300]: {raw_resp[:300]}")
        return
    if "code" in j and "message" in j:
        print(f"  ERR code={j['code']} msg={j['message'][:200]}")
        return
    print(
        f"  ul_count={j.get('ul_count')} ip_count={j.get('ip_count')} "
        f"total={j.get('total_count')} has_more={j.get('has_more')}"
    )
    res = j.get("result", [])
    print(f"  result: {len(res)} карточек")
    for c in res[:5]:
        name = (c.get("name") or "")[:38]
        region = (c.get("region") or "")[:25]
        address = (c.get("address") or "")[:70]
        print(f"    {name:38s} | {region:25s} | {address}")


async def run_case(page, overrides: dict, tag: str):
    captured = {"sent": None, "resp": None}

    async def handle_route(route):
        req = route.request
        post = req.post_data or ""
        try:
            body = json.loads(post) if post else {}
        except Exception:
            body = {}
        body.update(overrides)
        new_post = json.dumps(body, ensure_ascii=False)
        captured["sent"] = new_post
        # Отдаём запрос с новым телом; заголовки (в т.ч. CSRF) сохраняются
        await route.continue_(post_data=new_post)

    async def on_response(response):
        if "ajax_auth.php" not in response.url:
            return
        if captured["resp"] is not None:
            return
        try:
            body = await response.body()
            captured["resp"] = body.decode("utf-8", errors="replace")
        except Exception as e:
            captured["resp"] = f"<err:{e}>"

    # Перезагружаем страницу — гарантируем свежий CSRF и чистое состояние Vue
    for attempt in range(1, 4):
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    await page.wait_for_selector("#state-1", state="attached", timeout=20000)
    await page.wait_for_timeout(2000)

    # Ставим route + response-listener только на время этого кейса
    await page.route("**/ajax_auth.php*", handle_route)
    page.on("response", lambda r: asyncio.create_task(on_response(r)))

    # Сабмитим Vue-форму. Пробуем в порядке: submit-кнопка → requestSubmit → submit.
    submitted = await page.evaluate(
        """() => {
            const form = document.getElementById('filter-form') || document.querySelector('form');
            if (!form) return 'no-form';
            // Ищем видимую кнопку сабмита — Vue навешивает на неё @click
            const btn = form.querySelector('button[type="submit"], input[type="submit"], .submit-button, .btn-submit');
            if (btn) { btn.click(); return 'btn-click'; }
            if (typeof form.requestSubmit === 'function') { form.requestSubmit(); return 'requestSubmit'; }
            form.submit(); return 'submit';
        }"""
    )
    print(f"  [{tag}] submit: {submitted}")

    # Ждём ответ (до 20с)
    for _ in range(40):
        if captured["resp"] is not None:
            break
        await page.wait_for_timeout(500)

    await page.unroute("**/ajax_auth.php*")
    describe(tag, captured["sent"], captured["resp"])
    return captured


async def main():
    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()

            # 1) Самарская + ООО
            await run_case(page, {
                "state-1": True, "okved_strict": True,
                "63": True, "12165,12300": True,
            }, "Самарская + ООО")

            # 2) Москва (97,77) + ООО
            await run_case(page, {
                "state-1": True, "okved_strict": True,
                "97,77": True, "12165,12300": True,
            }, "Москва (97,77) + ООО")

            # 3) Самарская + ООО, стр.2
            await run_case(page, {
                "state-1": True, "okved_strict": True,
                "63": True, "12165,12300": True, "page": "2",
            }, "Самарская + ООО, стр.2")

            # 4) Самарская + ОКВЭД 46.9
            await run_case(page, {
                "state-1": True, "okved_strict": True,
                "63": True, "46.9": True,
            }, "Самарская + ОКВЭД 46.9")

            # 5) query=ромашка
            await run_case(page, {
                "state-1": True, "okved_strict": True,
                "query": "ромашка",
            }, "query=ромашка")

        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
