"""Дамп формы /search-advanced без авторизации — достаточно HTML,
чтобы прочитать имена полей и вариантов."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

OUT = Path(__file__).resolve().parent.parent / "logs" / "diag"
OUT.mkdir(parents=True, exist_ok=True)

URL = "https://www.rusprofile.ru/search-advanced"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = await ctx.new_page()
        try:
            await page.goto("about:blank")
            await page.goto(URL, wait_until="commit", timeout=45000)
            await page.wait_for_selector("body", timeout=30000)
            await page.wait_for_timeout(4000)

            final = page.url
            title = await page.title()
            print("FINAL:", final)
            print("TITLE:", title)

            html = await page.content()
            (OUT / "search-advanced.html").write_text(html, encoding="utf-8")
            print("SAVED: logs/diag/search-advanced.html")

            soup = BeautifulSoup(html, "lxml")
            forms = soup.find_all("form")
            print(f"FORMS: {len(forms)}")
            for i, f in enumerate(forms):
                print(
                    f"\n--- form #{i} action={f.get('action')!r} "
                    f"method={f.get('method') or 'get'} class={f.get('class')}"
                )
                for el in f.find_all(["input", "select", "textarea"]):
                    name = el.get("name")
                    if not name:
                        continue
                    print(
                        f"  {el.name:8s} type={el.get('type') or '-':10s} "
                        f"name={name:28s} value={(el.get('value') or '')[:24]!r:28s} "
                        f"ph={el.get('placeholder') or ''!r}"
                    )
                    if el.name == "select":
                        for opt in el.find_all("option")[:10]:
                            print(
                                f"      opt value={opt.get('value')!r} "
                                f"text={opt.get_text(strip=True)[:40]!r}"
                            )

            # standalone
            print("\n=== standalone inputs ===")
            for el in soup.select("input[name], select[name]"):
                if el.find_parent("form"):
                    continue
                print(
                    f"  {el.name:8s} type={el.get('type') or '-':10s} "
                    f"name={el.get('name'):28s} ph={el.get('placeholder') or ''!r}"
                )

            # Vue-компоненты — ищем по data-атрибутам и filter-классам
            print("\n=== filter-классы (до 20) ===")
            seen = set()
            for el in soup.select("[class*='filter'], [class*='Filter']"):
                classes = " ".join(el.get("class") or [])
                if classes in seen:
                    continue
                seen.add(classes)
                if len(seen) > 20:
                    break
                print(f"  {el.name}.{classes}")

            # Показываем первые 60 ссылок с /search — подсказка по URL-схеме
            print("\n=== /search* ссылки ===")
            hrefs = set()
            for a in soup.find_all("a", href=True):
                h = a["href"]
                if "/search" in h or "revenue" in h or "region" in h or "okved" in h:
                    hrefs.add(h)
            for h in list(hrefs)[:40]:
                print(" ", h)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
