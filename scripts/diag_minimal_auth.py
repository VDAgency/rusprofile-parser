"""Совершенно минимальная попытка логина — для отладки, почему auth.py виснет."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright
from src.config import RUSPROFILE_LOGIN, RUSPROFILE_PASSWORD


async def main():
    print(f"LOGIN={RUSPROFILE_LOGIN!r}")
    print(f"PASS len={len(RUSPROFILE_PASSWORD or '')}")

    async with async_playwright() as pw:
        print("[1] launching chromium ...")
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        print("[2] new context ...")
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
        )
        print("[3] new page ...")
        page = await context.new_page()

        print("[3.5] warmup about:blank ...")
        await page.goto("about:blank")

        print("[4] goto rusprofile (wait=commit) ...")
        try:
            resp = await page.goto("https://www.rusprofile.ru/", wait_until="commit", timeout=30000)
            print(f"[4 OK] status={resp.status if resp else None}")
        except Exception as e:
            print(f"[4 ERR] {e}")

        print("[5] page.title")
        print("    →", await page.title())

        print("[6] wait_for_selector #menu-personal-trigger ...")
        try:
            await page.wait_for_selector("#menu-personal-trigger", timeout=20000)
            await page.wait_for_timeout(2000)  # Vue app init
            print("[6 OK]")
        except Exception as e:
            print(f"[6 ERR] {e}")

        print("[7] click #menu-personal-trigger")
        try:
            await page.locator("#menu-personal-trigger").first.click()
            await page.wait_for_timeout(2000)
            print("[7 OK]")
        except Exception as e:
            print(f"[7 ERR] {e}")

        print("[8] email input ...")
        try:
            email = page.locator('input[name="email"][type="email"]').first
            await email.wait_for(state="visible", timeout=10000)
            await email.fill(RUSPROFILE_LOGIN)
            print("[8 OK]")
        except Exception as e:
            print(f"[8 ERR] {e}")

        print("[9] continue button ...")
        try:
            btn = page.locator('.vModal-body button:has-text("Продолжить")').first
            await btn.click()
            print("[9 OK]")
        except Exception as e:
            print(f"[9 ERR] {e}")

        print("[10] password input ...")
        try:
            pw_input = page.locator('input[name="current-password"]').first
            await pw_input.wait_for(state="visible", timeout=10000)
            await pw_input.fill(RUSPROFILE_PASSWORD)
            print("[10 OK]")
        except Exception as e:
            print(f"[10 ERR] {e}")

        print("[11] submit login ...")
        try:
            submit = page.locator('.vModal-body button.btn-blue:has-text("Войти")').first
            await page.wait_for_function(
                """() => {
                    const btns = document.querySelectorAll('.vModal-body button.btn-blue');
                    for (const b of btns) { if ((b.textContent||'').includes('Войти') && !b.disabled) return true; }
                    return false;
                }""", timeout=5000,
            )
            await submit.click()
            await page.wait_for_timeout(4000)
            print("[11 OK]")
        except Exception as e:
            print(f"[11 ERR] {e}")

        print("[12] waiting 10s after submit ...")
        await page.wait_for_timeout(10000)

        dump = await page.evaluate(
            """() => {
                const t = document.querySelector('#menu-personal-trigger');
                const modalErrors = Array.from(document.querySelectorAll('.vModal-body .error, .vModal-body [class*=error], .vModal-body p')).map(e => (e.textContent||'').trim()).filter(Boolean);
                const sharedAccount = document.querySelector('.mw-shared-account');
                const modalOpen = !!document.querySelector('.vModal, .modal, .v-modal');
                return {
                    triggerText: t ? t.textContent.trim() : null,
                    triggerHTML: t ? t.outerHTML.slice(0, 400) : null,
                    modalOpen,
                    sharedAccount: sharedAccount ? sharedAccount.outerHTML : null,
                    modalText: modalErrors.slice(0, 10),
                    url: location.href,
                };
            }"""
        )
        import json
        print(json.dumps(dump, ensure_ascii=False, indent=2))

        # Скриншот
        try:
            await page.screenshot(path="/opt/rusprofile-parser/logs/diag/after_login.png", full_page=False)
            print("screenshot: /opt/rusprofile-parser/logs/diag/after_login.png")
        except Exception as e:
            print(f"screenshot err: {e}")

        # Шаг 13 — закрыть multi-device модалку
        print("[13] close shared-account modal ...")
        try:
            link = page.locator(
                '.mw-shared-account a.btn-blue, '
                '.mw-shared-account a:has-text("Продолжить работу")'
            ).first
            if await link.is_visible(timeout=3000):
                await link.click()
                await page.wait_for_timeout(3000)
                print("[13 OK]")
            else:
                print("[13] no shared-account modal visible")
        except Exception as e:
            print(f"[13 ERR] {e}")

        after = await page.evaluate(
            """() => {
                const t = document.querySelector('#menu-personal-trigger');
                return { text: t ? t.textContent.trim() : null };
            }"""
        )
        print(f"final trigger: {after!r}")
        cookies = await context.cookies()
        print(f"cookies count: {len(cookies)}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
