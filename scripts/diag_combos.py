"""Финальная проверка: комбинации фильтров + формат ОКВЭД + композитные регионы."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context

URL = "https://www.rusprofile.ru/search-advanced"
BASE = {"state-1": True, "okved_strict": True}

CASES = [
    # Комбинации (main path)
    ("Самар + ООО",          {"region": ["63"], "okopf": ["12165", "12300"]}),
    ("Самар + ИП",           {"region": ["63"], "okopf": ["50102"]}),
    ("Самар + ООО + ОКВЭД 46.9",  {"region": ["63"], "okopf": ["12165","12300"], "okved": ["46.9"]}),
    ("Самар + ОКВЭД 46.9 только", {"region": ["63"], "okved": ["46.9"]}),
    # Москва — композитный код 97,77
    ("Москва как массив строк", {"region": ["97", "77"]}),
    ("Москва как одна строка",  {"region": ["97,77"]}),
    # Пагинация
    ("Самар+ООО page=1",    {"region": ["63"], "okopf": ["12165","12300"], "page": "1"}),
    ("Самар+ООО page=2",    {"region": ["63"], "okopf": ["12165","12300"], "page": "2"}),
    ("Самар+ООО page=20",   {"region": ["63"], "okopf": ["12165","12300"], "page": "20"}),
    # Альтернативные варианты ключа ОКВЭД
    ("okved_code[]",          {"region": ["63"], "okved_code": ["46.9"]}),
    ("okved key=46",          {"region": ["63"], "okved": ["46"]}),
    # query + region
    ("query + регион",        {"region": ["63"], "query": "ромашка"}),
]


async def run_case(page, extra: dict, tag: str):
    cap = {"sent": None, "resp": None}

    async def handle_route(route):
        try:
            body = json.loads(route.request.post_data or "") if route.request.post_data else {}
        except Exception:
            body = {}
        body.update(BASE); body.update(extra)
        new_post = json.dumps(body, ensure_ascii=False)
        cap["sent"] = new_post
        await route.continue_(post_data=new_post)

    async def on_resp(r):
        if "ajax_auth.php" not in r.url or cap["resp"] is not None:
            return
        try:
            cap["resp"] = (await r.body()).decode("utf-8", errors="replace")
        except Exception as e:
            cap["resp"] = f"<err:{e}>"

    for _ in range(3):
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    await page.wait_for_selector("#state-1", state="attached", timeout=20000)
    await page.wait_for_timeout(1200)

    await page.route("**/ajax_auth.php*", handle_route)
    h = lambda r: asyncio.create_task(on_resp(r))
    page.on("response", h)

    await page.evaluate(
        """() => {
            const f = document.getElementById('filter-form') || document.querySelector('form');
            const btn = f && f.querySelector('button[type="submit"], input[type="submit"], .submit-button');
            if (btn) btn.click();
            else if (f && f.requestSubmit) f.requestSubmit();
            else if (f) f.submit();
        }"""
    )
    for _ in range(40):
        if cap["resp"] is not None:
            break
        await page.wait_for_timeout(300)
    await page.unroute("**/ajax_auth.php*")
    page.remove_listener("response", h)

    total = ul = ip = None
    first_name = first_region = ""
    if cap["resp"]:
        try:
            j = json.loads(cap["resp"])
            total = j.get("total_count")
            ul = j.get("ul_count")
            ip = j.get("ip_count")
            res = j.get("result", [])
            if res:
                first_name = (res[0].get("name") or "")[:40]
                first_region = (res[0].get("region") or "")[:25]
        except Exception:
            pass
    print(f"  {tag:32s} total={total} ul={ul} ip={ip}  первая: {first_name} | {first_region}")


async def main():
    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()
            for tag, extra in CASES:
                await run_case(page, extra, tag)
        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
