"""Тест: помогает ли playwright-stealth обойти блокировку rusprofile."""
import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth


async def main():
    async with Stealth().use_async(async_playwright()) as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
        )
        page = await context.new_page()

        for label, url, wait in [
            ("warmup", "about:blank", "load"),
            ("rusprofile load", "https://www.rusprofile.ru/", "load"),
            ("rusprofile commit", "https://www.rusprofile.ru/", "commit"),
            ("search", "https://www.rusprofile.ru/search?query=ромашка", "load"),
        ]:
            try:
                resp = await page.goto(url, wait_until=wait, timeout=30000)
                title = await page.title()
                print(f"OK  [{wait:6s}] {label:25s} → {resp.status if resp else '-'} / {title[:60]}")
            except Exception as e:
                print(f"ERR [{label:25s}] {e}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
