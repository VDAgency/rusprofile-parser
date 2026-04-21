"""Прямой вызов /ajax_auth.php?action=search_advanced.

Гипотеза: сервер принимает JSON-body, где ключи — это имена input'ов
(name атрибут чекбокса) со значением true. По первому прогону мы знаем:
- "state-1": true — «Действующая»
- "okved_strict": true — строгое соответствие ОКВЭД
- "page": "2" — страница 2

Попробуем добавить:
- "63": true (регион Самарская)
- "12165,12300": true (ОКОПФ ООО)

И посмотрим, какие регионы окажутся в ответе. Если все из Самарской —
победа, и парсер можно полностью переписать на прямой вызов API.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context


URL = "https://www.rusprofile.ru/search-advanced"


async def fetch_via_page(page, body: dict) -> dict:
    """Вызывает /ajax_auth.php из контекста страницы — cookies и CSRF
    уходят автоматически, Vue-ничего не нужно."""
    result = await page.evaluate(
        """async (body) => {
            const resp = await fetch('/ajax_auth.php?action=search_advanced', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: JSON.stringify(body),
                credentials: 'same-origin',
            });
            const txt = await resp.text();
            let j = null;
            try { j = JSON.parse(txt); } catch {}
            return {status: resp.status, ok: resp.ok, size: txt.length, json: j, preview: txt.slice(0, 400)};
        }""",
        body,
    )
    return result


def describe_result(tag: str, r: dict):
    print(f"\n--- {tag} ---")
    print(f"  status={r['status']} size={r['size']}")
    print(f"  preview: {r.get('preview','')[:300]}")
    j = r.get("json")
    if not j:
        return
    print(f"  ul_count={j.get('ul_count')} ip_count={j.get('ip_count')} "
          f"total_count={j.get('total_count')} has_more={j.get('has_more')}")
    # Покажем первые 5 компаний: имя и регион
    res = j.get("result", [])
    print(f"  result: {len(res)} карточек")
    for c in res[:5]:
        print(f"    {c.get('name','')[:40]:40s} | {c.get('region','')[:25]:25s} | {c.get('address','')[:70]}")


async def main():
    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()

            # Заходим на страницу поиска, чтобы получить cookies/CSRF
            for attempt in range(1, 4):
                try:
                    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    break
                except Exception:
                    await page.wait_for_timeout(3000)

            await page.wait_for_timeout(2000)

            # Эксперимент 1 — как baseline, тот же body что Vue отсылает сам.
            r1 = await fetch_via_page(page, {"state-1": True, "okved_strict": True})
            describe_result("baseline (как у Vue)", r1)

            # Эксперимент 2 — добавили фильтр по региону
            r2 = await fetch_via_page(page, {
                "state-1": True, "okved_strict": True,
                "63": True,  # Самарская
            })
            describe_result("baseline + регион 63", r2)

            # Эксперимент 3 — + ОКОПФ ООО
            r3 = await fetch_via_page(page, {
                "state-1": True, "okved_strict": True,
                "63": True, "12165,12300": True,
            })
            describe_result("+ ОКОПФ 12165,12300 (ООО)", r3)

            # Эксперимент 4 — то же с page=2
            r4 = await fetch_via_page(page, {
                "state-1": True, "okved_strict": True,
                "63": True, "12165,12300": True,
                "page": "2",
            })
            describe_result("стр.2 с теми же фильтрами", r4)

            # Эксперимент 5 — Москва (97,77) + ОКОПФ ООО
            r5 = await fetch_via_page(page, {
                "state-1": True, "okved_strict": True,
                "97,77": True, "12165,12300": True,
            })
            describe_result("Москва (97,77) + ООО", r5)

            # Эксперимент 6 — регион 63 без ОКОПФ, стр.3
            r6 = await fetch_via_page(page, {
                "state-1": True, "okved_strict": True,
                "63": True, "page": "3",
            })
            describe_result("регион 63, стр.3", r6)

            # Эксперимент 7 — только query
            r7 = await fetch_via_page(page, {
                "state-1": True, "okved_strict": True,
                "query": "ромашка",
            })
            describe_result("query=ромашка", r7)

            # Эксперимент 8 — ОКВЭД 46.9
            r8 = await fetch_via_page(page, {
                "state-1": True, "okved_strict": True,
                "46.9": True,
            })
            describe_result("ОКВЭД 46.9", r8)

            # Эксперимент 9 — комбо: Самара + ОКОПФ + has_phones
            r9 = await fetch_via_page(page, {
                "state-1": True, "okved_strict": True,
                "63": True, "12165,12300": True,
                "has_phones": True, "has_sites": True,
            })
            describe_result("+ has_phones + has_sites", r9)

            # Эксперимент 10 — finance_revenue_from
            r10 = await fetch_via_page(page, {
                "state-1": True, "okved_strict": True,
                "63": True,
                "finance_revenue_from": "1000000",
                "finance_revenue_to": "100000000",
            })
            describe_result("финансы 1M–100M", r10)

        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
