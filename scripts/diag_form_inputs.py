"""Разведка формы /search-advanced.

Выводит список всех <input name=...> с типами и выбранными значениями,
плюс перехватывает все сетевые запросы при клике «Найти», чтобы узнать,
как именно Rusprofile отправляет поиск (GET /search?... или JSON POST API).
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

URL = "https://www.rusprofile.ru/search-advanced"


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

        # Перехват запросов
        captured = []

        def on_request(req):
            if "rusprofile.ru" in req.url and req.method in ("GET", "POST"):
                # Нам интересны именно /search... запросы.
                if "/search" in req.url or "api" in req.url:
                    captured.append({
                        "method": req.method,
                        "url": req.url,
                        "post_data": req.post_data,
                    })

        page.on("request", on_request)

        try:
            await page.goto("about:blank")
            for attempt in range(1, 5):
                try:
                    resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    print(f"attempt {attempt}: status={resp.status if resp else '?'}")
                    if page.url.startswith("http"):
                        break
                except Exception as e:
                    print(f"attempt {attempt} goto failed: {e}")
                    await page.wait_for_timeout(3000)

            # Прогрузка виджетов
            for _ in range(5):
                await page.wait_for_timeout(1500)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)

            # Дампим все inputs
            inputs_info = await page.evaluate(
                """() => {
                    const all = Array.from(document.querySelectorAll('input, select, textarea'));
                    return all.map(el => {
                        const info = {
                            tag: el.tagName,
                            type: el.type,
                            name: el.getAttribute('name'),
                            id: el.id,
                            value: el.value,
                            placeholder: el.getAttribute('placeholder'),
                            checked: el.checked,
                        };
                        // Для select — выбранные опции
                        if (el.tagName === 'SELECT') {
                            info.options = Array.from(el.options).slice(0, 5).map(o => ({
                                value: o.value, text: (o.text || '').trim().slice(0, 40)
                            }));
                        }
                        // Ищем ближайший label
                        const lab = el.closest('label') || document.querySelector(`label[for="${el.id}"]`);
                        if (lab) info.label = (lab.textContent || '').trim().slice(0, 60);
                        return info;
                    });
                }"""
            )

            print("\n" + "=" * 80)
            print(f"Всего input/select/textarea: {len(inputs_info)}")
            print("=" * 80)
            # Фильтруем — показываем только те, что имеют name или id
            for i, info in enumerate(inputs_info):
                if not (info.get('name') or info.get('id')):
                    continue
                print(
                    f"[{i:3d}] {info.get('tag'):8s} type={str(info.get('type')):12s} "
                    f"name={str(info.get('name')):35s} id={str(info.get('id') or ''):25s} "
                    f"value={str(info.get('value') or '')[:30]:30s} "
                    f"label={str(info.get('label') or '')[:40]}"
                )

            # Форма поиска — есть ли кнопка и action?
            form_info = await page.evaluate(
                """() => {
                    const form = document.querySelector('form');
                    if (!form) return null;
                    const btn = form.querySelector('button[type=submit], button.search-btn, button');
                    return {
                        action: form.action,
                        method: form.method,
                        id: form.id,
                        cls: form.className,
                        btn: btn ? {
                            type: btn.type,
                            cls: btn.className,
                            text: (btn.textContent || '').trim().slice(0, 30),
                        } : null,
                    };
                }"""
            )
            print(f"\nFORM: {form_info}")

            # Наполняем что-то и жмём submit — смотрим реальный запрос
            try:
                await page.locator("input[name='query']").first.fill("ромашка")
            except Exception:
                pass

            # Клик по кнопке
            btn = page.locator("button.search-btn").first
            await btn.wait_for(state="visible", timeout=5000)
            await btn.click()
            try:
                await page.wait_for_url("**/search?**", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            print("\n" + "=" * 80)
            print(f"ЗАПРОСЫ ({len(captured)}):")
            print("=" * 80)
            for c in captured[:20]:
                print(f"  {c['method']:5s} {c['url']}")
                if c['post_data']:
                    print(f"         POST: {c['post_data'][:200]}")

            print(f"\nИТОГ URL: {page.url}")

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
