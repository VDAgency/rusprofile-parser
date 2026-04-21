"""Разведка пагинации на /search-advanced.

Открываем поиск с фильтрами, который гарантированно даёт >50 компаний
(Самарская область, ООО, действующие), смотрим ссылки пагинатора
и первый href «Next/→» в HTML. Отдельно проверяем, как сайт
формирует URL для страницы 2.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from src.rusprofile.filters import (
    SearchFilters, build_search_url,
    STATUS_ACTIVE, LEGAL_FORM_OOO,
)


async def main():
    filters = SearchFilters(
        status=[STATUS_ACTIVE],
        region=["63"],            # Самарская
        okopf=[LEGAL_FORM_OOO],
    )
    url = build_search_url(filters)
    print(f"URL стр.1: {url}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = await ctx.new_page()
        try:
            await page.goto("about:blank")
            for attempt in range(1, 4):
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    print(f"goto attempt {attempt}: status={resp.status if resp else '?'}")
                    if page.url.startswith("http"):
                        break
                except Exception as e:
                    print(f"attempt {attempt} fail: {e}")
                    await page.wait_for_timeout(3000)

            # Дадим Vue отрисовать список результатов
            for _ in range(4):
                await page.wait_for_timeout(1500)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)

            # Заголовок с количеством
            try:
                h1 = await page.locator("h1").first.inner_text(timeout=5000)
                print(f"\nH1: {h1!r}")
            except Exception as e:
                print(f"H1 fail: {e}")

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            # Сколько карточек отрисовалось
            cards = soup.select(".list-element")
            print(f"Карточек на стр.1: {len(cards)}")

            # Ищем пагинатор: .pagination, .pager, ссылки с ?page= ...
            print("\n--- ВСЕ ССЫЛКИ, ГДЕ ЕСТЬ page= ---")
            pag_links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "page=" in href or "/page/" in href:
                    text = (a.get_text(strip=True) or "")[:30]
                    pag_links.append((text, href))
            for text, href in pag_links[:30]:
                print(f"  '{text}' -> {href}")

            # Ищем .pagination* блоки
            print("\n--- БЛОКИ С КЛАССОМ pagination/pager ---")
            for cls in ("pagination", "pager", "page-list", "paginator"):
                for el in soup.select(f"[class*='{cls}']"):
                    outer = el.prettify()[:800]
                    print(f"\n<{el.name} class={el.get('class')}>")
                    print(outer[:500])

            # Если есть кнопка «Следующая», клик и смотрим новый URL
            print("\n--- Пробуем кликнуть «Следующая» / «2» ---")
            # Часто это ссылка с текстом «2» в пагинаторе.
            next_link_href = await page.evaluate(
                """() => {
                    // Ищем ссылки с page= в href
                    const as = Array.from(document.querySelectorAll('a[href*="page="], a[href*="/page/"]'));
                    // Предпочитаем страницу "2"
                    const to2 = as.find(a => /^(2|След|Next|»|→)$/i.test((a.textContent||'').trim()));
                    const chosen = to2 || as[0];
                    return chosen ? chosen.href : null;
                }"""
            )
            print(f"Кандидат ссылки на стр.2: {next_link_href}")

            if next_link_href:
                for attempt in range(1, 4):
                    try:
                        resp = await page.goto(next_link_href, wait_until="domcontentloaded", timeout=60000)
                        print(f"goto стр.2 attempt {attempt}: status={resp.status if resp else '?'} url={page.url}")
                        break
                    except Exception as e:
                        print(f"goto стр.2 attempt {attempt} fail: {e}")
                        await page.wait_for_timeout(3000)
                for _ in range(3):
                    await page.wait_for_timeout(1500)
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
                html2 = await page.content()
                soup2 = BeautifulSoup(html2, "lxml")
                cards2 = soup2.select(".list-element")
                # Показываем первые ИНН на стр.1 и стр.2 — убеждаемся, что выдача разная.
                def _inn(c):
                    for sp in c.select(".list-element__row-info span"):
                        t = sp.get_text(strip=True)
                        if "инн" in t.lower():
                            return "".join(ch for ch in t if ch.isdigit())
                    return ""
                inn1 = [_inn(c) for c in cards[:5]]
                inn2 = [_inn(c) for c in cards2[:5]]
                print(f"\nИНН первых 5 на стр.1: {inn1}")
                print(f"ИНН первых 5 на стр.2: {inn2}")
                print(f"Разные? {set(inn1) != set(inn2)}")
                print(f"URL стр.2 итоговый: {page.url}")
                # Параметры
                q2 = parse_qs(urlparse(page.url).query, keep_blank_values=True)
                print("Параметры URL стр.2:")
                for k, v in sorted(q2.items()):
                    print(f"  {k!r} = {v}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
