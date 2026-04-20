"""Диагностика разметки страницы компании — для подбора селекторов контактов."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from src.rusprofile.auth import get_authenticated_context

OUT = Path(__file__).resolve().parent.parent / "logs" / "diag"
OUT.mkdir(parents=True, exist_ok=True)


async def dump(ctx, label, url):
    page = await ctx.new_page()
    try:
        await page.goto("about:blank")
        await page.goto(url, wait_until="commit", timeout=30000)
        await page.wait_for_selector("body", timeout=30000)
        await page.wait_for_timeout(3000)
        html = await page.content()
        (OUT / f"{label}.html").write_text(html, encoding="utf-8")

        soup = BeautifulSoup(html, "lxml")
        print(f"\n==== {label}: {page.url}")
        print(f"title: {await page.title()}")

        for sel in [
            "[href^='tel:']",
            "[href^='mailto:']",
            ".company-info__phone",
            ".company-info__email",
            ".company-info__site",
            "[class*='phone']",
            "[class*='email']",
            "[class*='website']",
            "[class*='site']",
            ".fin-page",
            "[class*='finance']",
            "[class*='revenue']",
            ".hidden-phone",
            ".hidden-email",
        ]:
            els = soup.select(sel)
            if els:
                print(f"  {sel:35s} → {len(els)} | {els[0].get_text(strip=True)[:60]!r}")

        # Ищем контактный блок по заголовкам «Контакты», «Связаться»
        contact_h = soup.find(
            lambda t: t.name in ("h2", "h3") and "контакт" in t.get_text("", strip=True).lower()
        )
        if contact_h:
            print("  contact header found:", contact_h.get_text(strip=True))
            # Родитель-секция
            section = contact_h.find_parent(["section", "div"])
            if section:
                for a in section.select("a")[:15]:
                    print(f"   link href={a.get('href')!r} text={a.get_text(strip=True)!r}")

        # Финансовые блоки
        for sel in [
            "#finance", ".fin", "[class*='fin']",
            "[class*='revenue']", "[class*='profit']",
        ]:
            els = soup.select(sel)
            for el in els[:3]:
                t = el.get_text(" ", strip=True)
                if any(k in t.lower() for k in ["выручк", "прибыл", "доход"]):
                    print(f"  fin[{sel}]: {t[:120]}")

    finally:
        await page.close()


async def main():
    async with async_playwright() as pw:
        ctx = await get_authenticated_context(pw)
        try:
            for label, url in [
                ("company_romashka_7823378", "https://www.rusprofile.ru/id/7823378"),
                ("company_yandex", "https://www.rusprofile.ru/id/1049709"),
            ]:
                try:
                    await dump(ctx, label, url)
                except Exception as e:
                    print(f"!! {label}: {e}")
        finally:
            await ctx.browser.close()


if __name__ == "__main__":
    asyncio.run(main())
