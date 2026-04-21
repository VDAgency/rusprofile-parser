"""Перехватываем x-csrf-token из первого Vue-запроса и переиспользуем.

Если токен валиден на всю сессию — можно напрямую слать fetch к
/ajax_auth.php?action=search_advanced с любыми фильтрами и пагинацией.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context


URL = "https://www.rusprofile.ru/search-advanced"


async def fetch_with_token(page, token: str, body: dict) -> dict:
    result = await page.evaluate(
        """async ({token, body}) => {
            const resp = await fetch('/ajax_auth.php?action=search_advanced', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'X-CSRF-Token': token,
                },
                body: JSON.stringify(body),
                credentials: 'same-origin',
            });
            const txt = await resp.text();
            let j = null;
            try { j = JSON.parse(txt); } catch {}
            return {status: resp.status, size: txt.length, json: j, preview: txt.slice(0, 400)};
        }""",
        {"token": token, "body": body},
    )
    return result


def describe(tag, r):
    print(f"\n--- {tag} ---")
    print(f"  status={r['status']} size={r['size']}")
    j = r.get("json") or {}
    if "code" in j and "message" in j:
        print(f"  ERR code={j['code']} msg={j['message'][:200]}")
        return
    print(f"  ul_count={j.get('ul_count')} ip_count={j.get('ip_count')} "
          f"total_count={j.get('total_count')} has_more={j.get('has_more')}")
    res = j.get("result", [])
    print(f"  result: {len(res)} карточек")
    for c in res[:5]:
        print(f"    {c.get('name','')[:40]:40s} | {c.get('region','')[:25]:25s} | {c.get('address','')[:70]}")


async def main():
    captured_token = {"v": None}

    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()

            def on_request(request):
                if "ajax_auth.php" not in request.url:
                    return
                tok = request.headers.get("x-csrf-token")
                if tok and not captured_token["v"]:
                    captured_token["v"] = tok

            page.on("request", on_request)

            for attempt in range(1, 4):
                try:
                    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    break
                except Exception:
                    await page.wait_for_timeout(3000)

            # Ждём первого XHR (с токеном)
            for _ in range(30):
                if captured_token["v"]:
                    break
                await page.wait_for_timeout(500)

            token = captured_token["v"]
            if not token:
                print("CSRF-токен не перехвачен — выходим")
                return

            print(f"Перехвачен x-csrf-token: {token[:40]}...{token[-10:]}")

            # 1) Baseline
            describe("baseline (только state-1)", await fetch_with_token(
                page, token, {"state-1": True, "okved_strict": True}
            ))

            # 2) + регион 63
            describe("+ регион 63", await fetch_with_token(
                page, token, {"state-1": True, "okved_strict": True, "63": True}
            ))

            # 3) + ОКОПФ ООО
            describe("+ ОКОПФ ООО", await fetch_with_token(
                page, token,
                {"state-1": True, "okved_strict": True, "63": True, "12165,12300": True}
            ))

            # 4) page=2
            describe("то же, page=2", await fetch_with_token(
                page, token,
                {"state-1": True, "okved_strict": True, "63": True, "12165,12300": True, "page": "2"}
            ))

            # 5) page=3
            describe("то же, page=3", await fetch_with_token(
                page, token,
                {"state-1": True, "okved_strict": True, "63": True, "12165,12300": True, "page": "3"}
            ))

            # 6) Москва (97,77)
            describe("Москва (97,77) + ООО", await fetch_with_token(
                page, token,
                {"state-1": True, "okved_strict": True, "97,77": True, "12165,12300": True}
            ))

            # 7) ОКВЭД 46.9
            describe("ОКВЭД 46.9 в Самарской", await fetch_with_token(
                page, token,
                {"state-1": True, "okved_strict": True, "63": True, "46.9": True}
            ))

            # 8) Большая страница?
            describe("page=20 (проверка глубины)", await fetch_with_token(
                page, token,
                {"state-1": True, "okved_strict": True, "63": True, "12165,12300": True, "page": "20"}
            ))

        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
