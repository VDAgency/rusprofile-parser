"""Ищем структуру дропдауна регионов — DOM-иерархия вверх от #region-63."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.rusprofile.auth import get_authenticated_context


URL = "https://www.rusprofile.ru/search-advanced"


async def main():
    async with async_playwright() as pw:
        context = await get_authenticated_context(pw)
        try:
            page = await context.new_page()

            for attempt in range(1, 4):
                try:
                    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                    break
                except Exception:
                    await page.wait_for_timeout(3000)

            await page.wait_for_selector("#region-63", timeout=30000)
            await page.wait_for_timeout(2000)

            # DOM-предки #region-63: tag, id, class, стиль display/visibility
            tree = await page.evaluate(
                """() => {
                    const out = [];
                    let el = document.getElementById('region-63');
                    while (el && el !== document.body) {
                        const s = getComputedStyle(el);
                        out.push({
                            tag: el.tagName,
                            id: el.id || null,
                            cls: el.className || null,
                            display: s.display,
                            visibility: s.visibility,
                            height: s.height,
                            maxH: s.maxHeight,
                            overflow: s.overflow,
                            offsetH: el.offsetHeight,
                        });
                        el = el.parentElement;
                    }
                    return out;
                }"""
            )
            print("DOM-цепочка от #region-63 вверх:")
            for i, n in enumerate(tree):
                print(f"  [{i}] <{n['tag']} id={n['id']!s} class={n['cls']!s}>")
                print(f"       display={n['display']} vis={n['visibility']} "
                      f"h={n['height']} maxH={n['maxH']} offsetH={n['offsetH']}")

            # Смотрим соседей секции (может, заголовок над чекбоксами)
            print("\nСоседи секции регионов — что идёт до неё в DOM:")
            siblings = await page.evaluate(
                """() => {
                    let el = document.getElementById('region-63');
                    // Поднимаемся до контейнера (обычно .filter или .dropdown)
                    while (el && el.offsetHeight === 0 && el !== document.body) {
                        el = el.parentElement;
                    }
                    // el.offsetHeight=0 у скрытого контейнера; поднимемся ещё
                    // до ближайшего элемента с ненулевой высотой.
                    const section = el;
                    const out = [];
                    let s = section.previousElementSibling;
                    let c = 0;
                    while (s && c < 6) {
                        out.push({
                            tag: s.tagName, cls: s.className,
                            text: (s.innerText||'').trim().slice(0, 80),
                            offsetH: s.offsetHeight,
                        });
                        s = s.previousElementSibling;
                        c++;
                    }
                    return {sectionTag: section.tagName, sectionCls: section.className, siblings: out};
                }"""
            )
            print(f"  {siblings}")

            # Попробуем найти ближайший «filter-title»/«dropdown-toggle» перед #region-63
            print("\nБлижайший '.filter-title' перед регионами:")
            trigger = await page.evaluate(
                """() => {
                    const el = document.getElementById('region-63');
                    // Ищем секцию — контейнер с классом filter/dropdown
                    let container = el;
                    while (container && !(
                        (container.className || '').match(/filter-section|dropdown|filter-group|widget/i)
                    )) {
                        container = container.parentElement;
                        if (!container) break;
                    }
                    if (!container) return {found: false};
                    const title = container.querySelector('.filter-title, .dropdown-toggle, .filter-header, h2, h3, button');
                    if (title) {
                        return {
                            found: true,
                            containerCls: container.className,
                            titleTag: title.tagName,
                            titleCls: title.className,
                            titleText: (title.innerText||'').trim().slice(0, 80),
                        };
                    }
                    return {found: false, containerCls: container.className};
                }"""
            )
            print(f"  {trigger}")

            # Поищем видимые «Регион» в Vue-компонентах на странице
            print("\nВидимые элементы с текстом 'Регион' (limit 10):")
            vis = await page.evaluate(
                """() => Array.from(document.querySelectorAll('*'))
                    .filter(el => {
                        if (!el.offsetParent) return false;
                        const t = (el.innerText||'').trim();
                        return t.toLowerCase().startsWith('регион') && t.length < 30;
                    })
                    .slice(0, 10)
                    .map(el => ({
                        tag: el.tagName, cls: el.className,
                        text: (el.innerText||'').trim().slice(0, 80),
                    }))"""
            )
            for v in vis:
                print(f"  {v}")

        finally:
            ctx_browser = context.browser
            if ctx_browser:
                await ctx_browser.close()


if __name__ == "__main__":
    asyncio.run(main())
