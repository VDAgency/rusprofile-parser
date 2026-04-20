"""Диагностика — какие параметры реально принимает расширенный поиск Rusprofile.

План:
1. Открыть главную страницу и найти ссылку на «Расширенный поиск».
2. Открыть расширенный поиск, дампить форму: action, method, все
   <input>/<select> с name и type.
3. Попробовать пару конкретных сценариев — например «Москва + ОКВЭД 46.90
   + с сайтом» — через JS-сабмит формы, посмотреть, какой итоговый URL
   строит сам Rusprofile.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from src.rusprofile.auth import get_authenticated_context, _goto

OUT = Path(__file__).resolve().parent.parent / "logs" / "diag"
OUT.mkdir(parents=True, exist_ok=True)


ADV_URL_CANDIDATES = [
    "https://www.rusprofile.ru/search-advanced",
    "https://www.rusprofile.ru/advanced-search",
    "https://www.rusprofile.ru/search-in-region",
    "https://www.rusprofile.ru/search/advanced",
]


async def find_advanced_link(ctx):
    """Ищем ссылку на расширенный поиск на главной."""
    page = await ctx.new_page()
    try:
        await _goto(page, "https://www.rusprofile.ru/", timeout=45000)
        await page.wait_for_timeout(2500)
        html = await page.content()
        (OUT / "home.html").write_text(html, encoding="utf-8")

        soup = BeautifulSoup(html, "lxml")

        print("\n==== home: ссылки со словом поиск/фильтр ====")
        seen = set()
        for a in soup.select("a"):
            txt = a.get_text(strip=True).lower()
            href = a.get("href") or ""
            if not href or href.startswith("#"):
                continue
            if any(k in txt for k in ["расширенн", "фильтр", "подобрать", "расшир"]):
                key = (txt, href)
                if key in seen:
                    continue
                seen.add(key)
                print(f"  text={a.get_text(strip=True)!r} href={href}")

        # Плюс попробуем ссылки по известным адресам
        return soup
    finally:
        await page.close()


async def dump_form(ctx, url):
    """Открывает URL с формой поиска и дампит все поля."""
    page = await ctx.new_page()
    try:
        print(f"\n==== advanced form: {url} ====")
        try:
            await _goto(page, url, timeout=45000)
        except Exception as e:
            print(f"  !! goto failed: {e}")
            return

        await page.wait_for_timeout(3000)
        final = page.url
        title = await page.title()
        print(f"  → final: {final}")
        print(f"  → title: {title}")

        html = await page.content()
        safe = url.split("//")[-1].replace("/", "_").replace("?", "_")
        (OUT / f"adv_{safe}.html").write_text(html, encoding="utf-8")

        soup = BeautifulSoup(html, "lxml")

        # Страница расширенного поиска могла сделать редирект на 404 или
        # на главную — проверяем, есть ли формы вообще.
        forms = soup.select("form")
        print(f"  форм на странице: {len(forms)}")

        for i, form in enumerate(forms):
            action = form.get("action") or ""
            method = (form.get("method") or "get").lower()
            cls = " ".join(form.get("class") or [])
            # Пропускаем шапочные формы (авторизация, быстрый поиск в header)
            if "header" in cls.lower() or "modal" in cls.lower():
                continue
            fields = []
            for el in form.select("input, select, textarea"):
                name = el.get("name")
                if not name:
                    continue
                t = el.get("type") or el.name
                val = el.get("value") or ""
                placeholder = el.get("placeholder") or ""
                # У select — собираем значения option
                opts = []
                if el.name == "select":
                    for opt in el.select("option")[:12]:
                        opts.append((opt.get("value", ""), opt.get_text(strip=True)[:40]))
                fields.append({
                    "name": name, "type": t, "value": val,
                    "placeholder": placeholder, "options": opts,
                })
            if not fields:
                continue
            print(f"\n  --- form #{i}: action={action!r} method={method} classes={cls!r}")
            for f in fields:
                opts_str = ""
                if f["options"]:
                    opts_str = " | opts: " + ", ".join(
                        f"{v}={n!r}" for v, n in f["options"][:6]
                    )
                print(
                    f"    {f['type']:10s} name={f['name']:28s}"
                    f" value={f['value']!r:20s} "
                    f"ph={f['placeholder']!r:30s}{opts_str}"
                )

        # Отдельно — все input вне форм (у Vue-приложений часто так)
        standalone_inputs = [
            el for el in soup.select("input[name], select[name]")
            if not el.find_parent("form")
        ]
        if standalone_inputs:
            print("\n  standalone inputs/selects (вне <form>):")
            for el in standalone_inputs[:40]:
                print(
                    f"    {el.name:8s} type={el.get('type') or '-':10s} "
                    f"name={el.get('name'):30s} ph={el.get('placeholder') or '':30s}"
                )

        # Классы/id контейнеров с «filter» в названии
        filter_blocks = set()
        for el in soup.select("[class*='filter'], [class*='Filter'], [id*='filter'], [id*='Filter']"):
            key = el.name + "." + ".".join(el.get("class") or []) + (f"#{el['id']}" if el.get('id') else "")
            filter_blocks.add(key)
        if filter_blocks:
            print("\n  filter-блоки (до 15):")
            for k in list(filter_blocks)[:15]:
                print("   ", k)

    finally:
        await page.close()


async def try_real_submit(ctx):
    """Пробуем зайти на поисковую страницу с разными комбо и посмотреть,
    какой URL Rusprofile реально использует.

    Сценарий: регион Москва, ОКВЭД 46.90, с сайтом.
    """
    page = await ctx.new_page()
    try:
        # Простейший вариант — ввести в строку поиска название и
        # посмотреть, как она формируется.
        combos = [
            "https://www.rusprofile.ru/search?query=ромашка",
            "https://www.rusprofile.ru/search?search=ромашка",
            "https://www.rusprofile.ru/search?query=ромашка&region=77",
            "https://www.rusprofile.ru/search?query=ромашка&r=77",
            # Путь-бэйзд — Rusprofile часто делает /codes/oblast/NN
            "https://www.rusprofile.ru/codes/oblast/77",
            # Выручка/прибыль в публичной разбивке
            "https://www.rusprofile.ru/search?query=ромашка&revenue_from=100000000",
        ]
        print("\n==== проверка URL-параметров ====")
        for url in combos:
            try:
                await _goto(page, url, timeout=30000)
                await page.wait_for_timeout(1500)
                final = page.url
                # Первая цифра «найдено N компаний» — признак того,
                # что фильтр сработал.
                h1 = await page.locator("h1").first.inner_text(timeout=3000)
                print(f"  {url}")
                print(f"     → final: {final}")
                print(f"     → h1:    {h1[:140]!r}")
            except Exception as e:
                print(f"  !! {url}: {e}")
    finally:
        await page.close()


async def main():
    async with async_playwright() as pw:
        ctx = await get_authenticated_context(pw)
        try:
            await find_advanced_link(ctx)

            # Пробуем все кандидатные URL расширенного поиска
            for url in ADV_URL_CANDIDATES:
                try:
                    await dump_form(ctx, url)
                except Exception as e:
                    print(f"!! dump_form({url}): {e}")

            await try_real_submit(ctx)
        finally:
            await ctx.browser.close()


if __name__ == "__main__":
    asyncio.run(main())
