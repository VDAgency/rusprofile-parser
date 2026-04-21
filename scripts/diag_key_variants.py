"""Перебираем варианты ключей в JSON-body, чтобы найти рабочий формат фильтра.

У чекбокса name="63", data-group="region". Пробуем 10+ вариаций
сериализации — ищем ту, где total_count упадёт с 7 847 177 до ~десятков
тысяч (реальный размер Самарской области + ООО).
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context

URL = "https://www.rusprofile.ru/search-advanced"

# Базовые ключи, которые сервер и так учитывает
BASE = {"state-1": True, "okved_strict": True}

# Каждый вариант — что ДОБАВЛЯЕМ к BASE. Ожидаем, что total_count упадёт.
VARIANTS = [
    ("region_str",        {"region": "63"}),
    ("region_arr",         {"region": ["63"]}),
    ("region_dict_true",   {"region": {"63": True}}),
    ("regions_arr",        {"regions": ["63"]}),
    ("region-63",          {"region-63": True}),
    ("region[63]_true",    {"region[63]": True}),
    ("region_63_flat",     {"region_63": True}),
    ("state1_num",         {"state-1": 1, "region": "63"}),
    ("group_region",       {"group": {"region": ["63"]}}),
    ("filters_region",     {"filters": {"region": ["63"]}}),
    ("data-group",         {"data-group": "region", "63": True}),
    # ОКОПФ
    ("okopf_str",          {"okopf": "12165,12300"}),
    ("okopf_arr",          {"okopf": ["12165,12300"]}),
    ("okopf-key",          {"okopf-12165,12300": True}),
    ("okopf_split",        {"okopf": ["12165", "12300"]}),
]


async def run_case(page, extra: dict, tag: str):
    captured = {"sent": None, "resp": None}

    async def handle_route(route):
        req = route.request
        try:
            body = json.loads(req.post_data or "") if req.post_data else {}
        except Exception:
            body = {}
        body.update(BASE)
        body.update(extra)
        new_post = json.dumps(body, ensure_ascii=False)
        captured["sent"] = new_post
        await route.continue_(post_data=new_post)

    async def on_response(response):
        if "ajax_auth.php" not in response.url or captured["resp"] is not None:
            return
        try:
            body = await response.body()
            captured["resp"] = body.decode("utf-8", errors="replace")
        except Exception as e:
            captured["resp"] = f"<err:{e}>"

    for attempt in range(1, 4):
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    await page.wait_for_selector("#state-1", state="attached", timeout=20000)
    await page.wait_for_timeout(1500)

    await page.route("**/ajax_auth.php*", handle_route)
    handler = lambda r: asyncio.create_task(on_response(r))
    page.on("response", handler)

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
        if captured["resp"] is not None:
            break
        await page.wait_for_timeout(300)

    await page.unroute("**/ajax_auth.php*")
    page.remove_listener("response", handler)

    # Результат
    total = None
    if captured["resp"]:
        try:
            j = json.loads(captured["resp"])
            total = j.get("total_count")
        except Exception:
            pass
    print(f"  {tag:18s} total={total}  sent={captured['sent']}")
    return total


async def main():
    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()

            print("\n=== baseline ===")
            await run_case(page, {}, "baseline")

            print("\n=== варианты ключей ===")
            for tag, extra in VARIANTS:
                await run_case(page, extra, tag)
        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
