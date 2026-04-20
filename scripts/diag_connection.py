"""Минимальный тест соединения Playwright."""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
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

        for label, url in [
            ("example.com", "https://example.com/"),
            ("google.com", "https://www.google.com/"),
            ("yandex.ru", "https://ya.ru/"),
            ("rusprofile load", "https://www.rusprofile.ru/"),
            ("rusprofile load (commit)", "https://www.rusprofile.ru/"),
        ]:
            try:
                wait_mode = "load" if "commit" not in label else "commit"
                resp = await page.goto(url, wait_until=wait_mode, timeout=30000)
                status = resp.status if resp else "no-resp"
                title = await page.title()
                print(f"OK  [{wait_mode:6s}] {label:35s} → {status} / {title[:60]}")
            except Exception as e:
                print(f"ERR [{label:35s}] {e}")

        # Явно посмотрим сетевой ответ через expect_response
        print("\n---- raw request/response check ----")
        try:
            async def on_request(req):
                if "rusprofile" in req.url:
                    print(f"  REQ {req.method} {req.url}")
            async def on_response(resp):
                if "rusprofile" in resp.url:
                    print(f"  RES {resp.status} {resp.url[:80]}")

            page.on("request", on_request)
            page.on("response", on_response)

            try:
                await page.goto("https://www.rusprofile.ru/", wait_until="commit", timeout=20000)
            except Exception as e:
                print(f"  goto err: {e}")

            await page.wait_for_timeout(5000)
        except Exception as e:
            print(f"  err: {e}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
