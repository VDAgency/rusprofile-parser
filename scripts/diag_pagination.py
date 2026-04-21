"""Разведка пагинации на /search-advanced.

Предыдущая версия показала: в статическом HTML нет ни пагинатора,
ни ссылок с ?page=. Vue SPA рендерит список клиентским кодом.

Задачи этой версии:
1) Дождаться реального рендера — ловим заголовок «найдено N ...».
2) Перехватить все XHR/fetch запросы к rusprofile.ru/api — чтобы
   понять, какой эндпоинт возвращает список и как просить вторую
   страницу.
3) Дождаться рендера и выдуть DOM с пагинатором (если он вообще
   есть в DOM уже после гидрации).
4) Если пагинации в DOM нет — прокрутить страницу до конца и
   посмотреть, не подгружается ли следующая порция автоматически.
"""
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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

    xhr_log: list[dict] = []

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

        # Перехватываем ВСЕ запросы, идущие на rusprofile.ru — кроме
        # картинок/шрифтов/CSS. XHR/fetch и document нам важны.
        def on_request(request):
            rt = request.resource_type
            if rt in ("image", "font", "stylesheet", "media"):
                return
            u = request.url
            if "rusprofile.ru" not in u:
                return
            xhr_log.append({
                "phase": "req",
                "method": request.method,
                "type": rt,
                "url": u,
                "post": (request.post_data or "")[:400],
            })

        async def on_response(response):
            rt = response.request.resource_type
            if rt in ("image", "font", "stylesheet", "media"):
                return
            u = response.url
            if "rusprofile.ru" not in u:
                return
            headers = await response.all_headers()
            ct = headers.get("content-type", "")
            body_preview = ""
            if "json" in ct or "javascript" in ct or "text" in ct:
                try:
                    body = await response.body()
                    body_preview = body[:400].decode("utf-8", errors="replace")
                except Exception:
                    body_preview = "<no-body>"
            xhr_log.append({
                "phase": "resp",
                "status": response.status,
                "type": rt,
                "ct": ct,
                "url": u,
                "preview": body_preview,
            })

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

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

            # Ждём, пока заголовок начнёт содержать «найдено»/«юридических»
            print("\nЖдём рендера заголовка с количеством...")
            for i in range(30):  # до 30 секунд
                try:
                    h1 = await page.locator("h1").first.inner_text(timeout=1000)
                except Exception:
                    h1 = ""
                if h1 and ("найден" in h1.lower() or "юридичес" in h1.lower() or "организ" in h1.lower()):
                    print(f"  [{i}] H1: {h1!r}")
                    break
                await page.wait_for_timeout(1000)
            else:
                print(f"  H1 так и не обновился; последнее: {h1!r}")

            # Прокручиваем до конца — вдруг это infinite scroll
            print("\nПрокручиваем страницу до конца...")
            prev_card_count = 0
            for i in range(10):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)
                count = await page.evaluate("document.querySelectorAll('.list-element').length")
                print(f"  скролл {i}: карточек в DOM: {count}")
                if count == prev_card_count and i > 1:
                    # 2 раза подряд без изменений — скроллить бесполезно
                    break
                prev_card_count = count

            # Снимок HTML после полного рендера
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            cards = soup.select(".list-element")
            print(f"\nИТОГО карточек после рендера: {len(cards)}")

            # Смотрим все ссылки
            print("\n--- ССЫЛКИ, содержащие page, offset, start, from, paginate, search ---")
            suspicious = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if any(k in href.lower() for k in ("page=", "/page/", "offset=", "start=", "from=", "paginate")):
                    text = (a.get_text(strip=True) or "")[:40]
                    suspicious.append((text, href))
            if not suspicious:
                print("  (нет)")
            for text, href in suspicious[:40]:
                print(f"  '{text}' -> {href}")

            # DOM-селекторы, часто используемые для пагинатора
            print("\n--- DOM-селекторы пагинатора ---")
            for sel in (
                ".pagination", ".pager", ".paginator", ".page-list",
                "[class*=pagin]", "[class*=pager]",
                "[class*=load-more]", "[class*=show-more]", "button:has-text('Показать')",
            ):
                try:
                    cnt = await page.locator(sel).count()
                except Exception:
                    cnt = 0
                if cnt:
                    print(f"  {sel}: {cnt} шт.")

            # Ищем кнопку «Показать ещё» / «Загрузить ещё»
            print("\n--- Текстовый поиск «показать ещё / загрузить ещё / следующ» ---")
            for txt in ("Показать ещё", "Показать еще", "Загрузить ещё", "Загрузить еще",
                        "Следующая", "Далее", "Ещё"):
                try:
                    loc = page.get_by_text(txt, exact=False)
                    c = await loc.count()
                    if c:
                        print(f"  '{txt}': {c} шт.")
                except Exception:
                    pass

            # Лог XHR
            print(f"\n=== Сетевые запросы ({len(xhr_log)}) ===")
            # Фильтруем интересное — XHR/fetch и запросы с path, похожим на API
            for item in xhr_log:
                u = item.get("url", "")
                if item["phase"] != "resp":
                    continue
                # Показываем fetch/xhr и всё, что пахнет API
                t = item.get("type", "")
                path = urlparse(u).path
                if t in ("xhr", "fetch") or "/api" in path or "search" in path:
                    print(
                        f"  [{item.get('status')}] {t} {item.get('ct','')[:40]}\n"
                        f"    {u}\n"
                        f"    preview: {item.get('preview','')[:300]}"
                    )

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
