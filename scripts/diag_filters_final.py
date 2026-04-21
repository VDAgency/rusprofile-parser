"""Финальный combo: отмечает виджеты через setChecked + dispatch change,
перехватывает итоговый URL, в который Vue собирает все фильтры.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

URL = "https://www.rusprofile.ru/search-advanced"


async def tick(page, selector, desc):
    try:
        r = await page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return {found: false};
                el.checked = true;
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('input', {bubbles: true}));
                return {found: true, checked: el.checked, name: el.name, value: el.value};
            }""",
            selector,
        )
        print(f"  {desc:42s} → {r}")
    except Exception as e:
        print(f"  {desc:42s} FAIL: {e}")


async def main():
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

        captured = []
        page.on("request", lambda r: (
            captured.append((r.method, r.url))
            if "rusprofile.ru" in r.url and ("search" in r.url or "api" in r.url)
            else None
        ))

        try:
            await page.goto("about:blank")
            for attempt in range(1, 5):
                try:
                    resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    print(f"goto #{attempt}: status={resp.status if resp else '?'}")
                    if page.url.startswith("http"):
                        break
                except Exception as e:
                    print(f"goto #{attempt} fail: {e}")
                    await page.wait_for_timeout(3000)

            # Прогружаем Vue виджеты
            for _ in range(5):
                await page.wait_for_timeout(1500)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)

            # Текст
            await page.fill("input[name='query']", "ромашка")

            # Отмечаем всё по реальным селекторам
            widgets = [
                ("#state-1",                              "Статус: Действующая"),
                ("#region-97\\,77",                       "Регион: Москва (97,77)"),
                ("#okopf-12165\\,12300",                  "ОКОПФ: ООО"),
                ("#MICRO",                                "МСП: Микро"),
                ("#SMALL",                                "МСП: Малое"),
                ("#okved-46\\.9",                         "ОКВЭД: 46.9"),
                ("#has_phones",                           "Контакты: телефон"),
                ("#has_sites",                            "Контакты: сайт"),
                ("#has_emails",                           "Контакты: email"),
                ("#not_defendant",                        "Не был ответчиком"),
                ("#finance_has_actual_year_data",         "Есть отчёт"),
            ]
            for sel, desc in widgets:
                await tick(page, sel, desc)

            # Число
            for name, value in [
                ("finance_revenue_from", "1000000"),
                ("finance_revenue_to", "50000000"),
                ("finance_profit_from", "100000"),
                ("capital_from", "10000"),
                ("sshr_from", "5"),
            ]:
                try:
                    await page.fill(f"input[name='{name}']", value)
                    print(f"  fill {name:35s} = {value}")
                except Exception as e:
                    print(f"  fill {name:35s} FAIL: {e}")

            await page.wait_for_timeout(800)

            # Ищем настоящую кнопку submit. В форме есть js-search-btn / button[type=submit]
            print("\nПоиск кнопки submit...")
            btn_info = await page.evaluate(
                """() => {
                    const form = document.getElementById('filter-form') || document.querySelector('form');
                    if (!form) return 'no-form';
                    const btns = Array.from(form.querySelectorAll('button'));
                    return btns.map(b => ({
                        type: b.type,
                        cls: b.className,
                        text: (b.textContent||'').trim().slice(0,40),
                    }));
                }"""
            )
            print(f"  кнопки формы: {btn_info}")

            # submit — через вызов submit() формы, минуя всякие JS-модалки
            clicked_via = None
            try:
                # Сначала попробуем найти именно кнопку "Найти" по тексту
                ok = await page.evaluate(
                    """() => {
                        const form = document.getElementById('filter-form') || document.querySelector('form');
                        if (!form) return {ok: false};
                        const buttons = Array.from(form.querySelectorAll('button, input[type=submit]'));
                        const btn = buttons.find(b =>
                            /найти|поиск|применить|submit/i.test(b.textContent || b.value || '')
                        );
                        if (btn) {
                            btn.click();
                            return {ok: true, via: 'btn-click', text: (btn.textContent||'').trim()};
                        }
                        if (typeof form.requestSubmit === 'function') {
                            form.requestSubmit();
                            return {ok: true, via: 'requestSubmit'};
                        }
                        form.submit();
                        return {ok: true, via: 'form.submit'};
                    }"""
                )
                clicked_via = ok
                print(f"  submit: {ok}")
            except Exception as e:
                print(f"  submit FAIL: {e}")

            try:
                await page.wait_for_url("**/search?**", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(3000)

            final = page.url
            parsed = urlparse(final)
            qs = parse_qs(parsed.query, keep_blank_values=True)

            print("\n" + "=" * 70)
            print(f"PATH:   {parsed.path}")
            print(f"URL:    {final}")
            print("PARAMS:")
            for k, vs in sorted(qs.items()):
                print(f"  {k:40s} = {vs}")

            print(f"\nВсе запросы к search/api ({len(captured)}):")
            for m, u in captured:
                print(f"  {m} {u}")

            try:
                h1 = (await page.locator("h1").first.inner_text(timeout=3000))[:200]
                print(f"\nH1: {h1!r}")
            except Exception:
                pass
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
