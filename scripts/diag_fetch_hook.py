"""Перехват window.fetch — подмена body POST-запроса перед отправкой.

Схема: Vue-форма сама сабмитит на /ajax_auth.php со свежим CSRF-токеном.
Мы патчим window.fetch, ловим POST к ajax_auth.php и ДОБАВЛЯЕМ в body
свои фильтры (регион, ОКОПФ и т. д.), прежде чем Vue отправит запрос.

Если подход работает — в ответе вернутся отфильтрованные карточки,
пагинация через {"page":"2"} тоже будет работать.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context


URL = "https://www.rusprofile.ru/search-advanced"


async def run_with_overrides(page, overrides: dict):
    """Устанавливает перехват fetch с этими overrides и сабмитит форму."""
    # Ставим хук заново каждый раз — нужно задать свежий overrides
    await page.evaluate(
        """(overrides) => {
            if (window.__origFetch) window.fetch = window.__origFetch;
            window.__origFetch = window.fetch;
            window.__lastBody = null;
            window.__lastResponse = null;
            window.fetch = async function(input, init) {
                const url = typeof input === 'string' ? input : input.url;
                if (url && url.includes('ajax_auth.php') && init && init.body) {
                    try {
                        const body = JSON.parse(init.body);
                        Object.assign(body, overrides);
                        init.body = JSON.stringify(body);
                        window.__lastBody = init.body;
                    } catch (e) { window.__lastBody = 'parse-err:' + e.message; }
                }
                const resp = await window.__origFetch(input, init);
                if (url && url.includes('ajax_auth.php')) {
                    try {
                        const clone = resp.clone();
                        const txt = await clone.text();
                        window.__lastResponse = txt;
                    } catch {}
                }
                return resp;
            };
            return 'installed';
        }""",
        overrides,
    )

    # Сабмитим форму
    await page.evaluate(
        """() => {
            const f = document.getElementById('filter-form') || document.querySelector('form');
            if (f && f.requestSubmit) f.requestSubmit();
            else if (f) f.submit();
        }"""
    )

    # Ждём ответа
    for _ in range(30):
        last = await page.evaluate("() => window.__lastResponse")
        if last:
            break
        await page.wait_for_timeout(500)

    sent_body = await page.evaluate("() => window.__lastBody")
    raw_resp = await page.evaluate("() => window.__lastResponse")
    return sent_body, raw_resp


def describe(tag: str, sent_body: str, raw_resp: str):
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
        print(f"  ERR code={j['code']} msg={j['message']}")
        return
    print(f"  ul_count={j.get('ul_count')} ip_count={j.get('ip_count')} "
          f"total={j.get('total_count')} has_more={j.get('has_more')}")
    res = j.get("result", [])
    print(f"  result: {len(res)} карточек")
    for c in res[:5]:
        print(f"    {c.get('name','')[:38]:38s} | {c.get('region','')[:25]:25s} | {c.get('address','')[:70]}")


async def main():
    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()
            for attempt in range(1, 4):
                try:
                    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    break
                except Exception:
                    await page.wait_for_timeout(3000)
            await page.wait_for_selector("#state-1", state="attached", timeout=20000)
            await page.wait_for_timeout(2000)

            # 1) Регион 63 + ОКОПФ ООО
            sb, rr = await run_with_overrides(page, {
                "state-1": True, "okved_strict": True,
                "63": True, "12165,12300": True,
            })
            describe("Самарская + ООО", sb, rr)

            # 2) Москва + ООО
            # Vue на странице «забыт» после первого сабмита, перезагрузим
            await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_selector("#state-1", state="attached", timeout=20000)
            await page.wait_for_timeout(2000)
            sb, rr = await run_with_overrides(page, {
                "state-1": True, "okved_strict": True,
                "97,77": True, "12165,12300": True,
            })
            describe("Москва (97,77) + ООО", sb, rr)

            # 3) Самарская + ООО + page=2
            await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_selector("#state-1", state="attached", timeout=20000)
            await page.wait_for_timeout(2000)
            sb, rr = await run_with_overrides(page, {
                "state-1": True, "okved_strict": True,
                "63": True, "12165,12300": True, "page": "2",
            })
            describe("Самарская + ООО, стр.2", sb, rr)

            # 4) Самарская + ОКВЭД 46.9
            await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_selector("#state-1", state="attached", timeout=20000)
            await page.wait_for_timeout(2000)
            sb, rr = await run_with_overrides(page, {
                "state-1": True, "okved_strict": True,
                "63": True, "46.9": True,
            })
            describe("Самарская + ОКВЭД 46.9", sb, rr)

            # 5) Только query
            await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_selector("#state-1", state="attached", timeout=20000)
            await page.wait_for_timeout(2000)
            sb, rr = await run_with_overrides(page, {
                "state-1": True, "okved_strict": True,
                "query": "ромашка",
            })
            describe("query=ромашка", sb, rr)

        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
