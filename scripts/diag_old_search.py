"""Проверка старого /search — умеет ли он SSR-фильтровать по региону и пагинировать.

Если да — откажемся от Vue-формы /search-advanced целиком и будем
работать со старым /search: GET-запросы по региону/ОКОПФ/ОКВЭД,
естественная пагинация через &page=N.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context


BASE = "https://www.rusprofile.ru"


def parse_regions_from_html(html: str) -> list[tuple[str, str, str]]:
    """Возвращает [(name, region, address)] из карточек поиска."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for c in soup.select(".list-element"):
        nm = c.select_one("a.list-element__title")
        addr = c.select_one(".list-element__address")
        name = (nm.get_text(strip=True) if nm else "")[:40]
        addr_text = (addr.get_text(strip=True) if addr else "")
        # Регион часто первый сегмент адреса после индекса
        reg = ""
        parts = [p.strip() for p in addr_text.split(",") if p.strip()]
        if len(parts) >= 2:
            reg = parts[1] if parts[0].replace(" ", "").isdigit() else parts[0]
        out.append((name, reg, addr_text[:80]))
    return out


async def check(page, tag: str, url: str):
    print(f"\n=== {tag} ===")
    print(f"URL: {url}")
    for attempt in range(1, 4):
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            print(f"  goto status={resp.status if resp else '?'}")
            break
        except Exception as e:
            print(f"  goto fail: {e}")
            await page.wait_for_timeout(2000)
    await page.wait_for_timeout(2500)
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
    except Exception:
        pass

    # H1
    try:
        h1 = (await page.locator("h1").first.inner_text(timeout=3000))[:200]
        print(f"  H1: {h1!r}")
    except Exception:
        pass
    # Финальный URL (редирект?)
    print(f"  final URL: {page.url}")

    html = await page.content()
    cards = parse_regions_from_html(html)
    print(f"  карточек: {len(cards)}")
    for i, (name, reg, addr) in enumerate(cards[:6], 1):
        print(f"    {i}. {name:38s} | {reg[:25]:25s} | {addr}")

    # Пагинация — ссылки, содержащие page=
    soup = BeautifulSoup(html, "lxml")
    pag = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "page=" in h or "/page/" in h:
            pag.append((a.get_text(strip=True)[:20], h))
    if pag:
        print(f"  пагинация-ссылки ({len(pag)}):")
        for t, h in pag[:8]:
            print(f"    '{t}' -> {h}")
    else:
        print("  пагинация-ссылок нет")


async def main():
    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()

            # 1) Только регион
            await check(page, "/search?region=63", f"{BASE}/search?region=63")

            # 2) Регион + page=2
            await check(page, "/search?region=63&page=2", f"{BASE}/search?region=63&page=2")

            # 3) Регион + ОКВЭД (числовой код)
            await check(
                page, "/search?region=63&okved=47.9",
                f"{BASE}/search?region=63&okved=47.9",
            )

            # 4) region как код 63-77 (Москва?) — проверим параметр region по-другому
            await check(page, "/search?region=77", f"{BASE}/search?region=77")

            # 5) Как построен URL для "Самарская область" через ссылку на сайте —
            # на главной должна быть ссылка «по регионам». Проверим другой формат.
            await check(
                page, "/search?search_from_advanced=1&region=63",
                f"{BASE}/search?search_from_advanced=1&region=63",
            )

            # 6) query + region
            await check(
                page, "/search?query=ромашка&region=63",
                f"{BASE}/search?query=ромашка&region=63",
            )

        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
