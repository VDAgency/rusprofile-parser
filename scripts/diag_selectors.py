"""Диагностика — какая HTML-разметка у поисковой выдачи Rusprofile.

Запускается на сервере с уже сохранёнными cookies.
Открывает несколько разных поисковых URL и дампит структуру страницы,
чтобы мы могли подобрать правильные CSS-селекторы для карточек.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from src.rusprofile.auth import get_authenticated_context

OUT_DIR = Path(__file__).resolve().parent.parent / "logs" / "diag"
OUT_DIR.mkdir(parents=True, exist_ok=True)


URLS = [
    ("search_text", "https://www.rusprofile.ru/search?query=ромашка"),
    # Проверим поиск по коду региона через путь
    ("by_oblast", "https://www.rusprofile.ru/codes/oblast/77/"),
    # Расширенный поиск — на сайте кнопка ведёт, скорее всего, на /search-in-region
    ("search_in_region", "https://www.rusprofile.ru/search-in-region?region=77"),
    # Разбивка по ОКВЭД
    ("codes_okved", "https://www.rusprofile.ru/codes/okved/46.90/"),
    # Главная — посмотреть, есть ли ссылка «Расширенный поиск»
    ("home", "https://www.rusprofile.ru/"),
]


def climb(soup, href_selector):
    """Возвращает path предка с информативным классом для ссылок."""
    links = soup.select(href_selector)
    ancestors = {}
    for a in links[:10]:
        parent = a
        for depth in range(8):
            parent = parent.parent
            if parent is None:
                break
            cls = parent.get("class")
            tid = parent.get("id")
            if cls:
                key = f"{parent.name}.{'.'.join(cls)}"
                ancestors.setdefault(key, 0)
                ancestors[key] += 1
                break
            if tid:
                key = f"{parent.name}#{tid}"
                ancestors.setdefault(key, 0)
                ancestors[key] += 1
                break
    return ancestors


async def dump_page(context, label, url):
    page = await context.new_page()
    try:
        print(f"\n==== {label}: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)

        final_url = page.url
        title = await page.title()
        print(f"    → final: {final_url}")
        print(f"    → title: {title}")

        html = await page.content()
        (OUT_DIR / f"{label}.html").write_text(html, encoding="utf-8")

        soup = BeautifulSoup(html, "lxml")

        id_links = soup.select("a[href^='/id/']")
        ip_links = soup.select("a[href^='/ip/']")
        print(f"    → /id/ ссылок: {len(id_links)}, /ip/ ссылок: {len(ip_links)}")

        # Ближайший предок с классом/id для /id/ ссылки
        print("    ancestors for /id/:")
        for key, cnt in climb(soup, "a[href^='/id/']").items():
            print(f"       {cnt:3d} × {key}")
        print("    ancestors for /ip/:")
        for key, cnt in climb(soup, "a[href^='/ip/']").items():
            print(f"       {cnt:3d} × {key}")

        # Ищем ссылку на расширенный поиск в меню/шапке
        adv = soup.select("a:-soup-contains('Расширенный'), a:-soup-contains('расширенн'), a:-soup-contains('Фильтры')")
        for a in adv[:5]:
            print(f"    adv link: text={a.get_text(strip=True)!r} href={a.get('href')}")

    finally:
        await page.close()


async def main():
    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            for label, url in URLS:
                try:
                    await dump_page(context, label, url)
                except Exception as e:
                    print(f"!! {label}: {e}")
        finally:
            await context.browser.close()


if __name__ == "__main__":
    asyncio.run(main())
