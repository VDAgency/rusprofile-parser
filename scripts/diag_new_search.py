"""Разведка новой вёрстки Rusprofile (этап 1 ТЗ tz_parser_rusprofile.md).

Цель скрипта — точно понять, как сейчас работает выдача Rusprofile, чтобы
переписать `src/rusprofile/parser.py`.

Запускает headed Chromium (видимое окно) и проводит четыре сценария
последовательно, логируя ВСЕ запросы (request/response/framenavigated)
и сохраняя:

* `logs/diag/diag_new_search.har`              — единый HAR-дамп всей сессии
* `logs/diag/diag_new_search.log`              — текстовый лог (то же, что в stdout)
* `logs/diag/diag_new_search_<step>.html/.png` — снимок страницы после каждого шага

Сценарии:

  1) goto /search-advanced — посмотреть, что Rusprofile рендерит «по умолчанию»
     (есть ли карточки сразу, в чём состоит форма).
  2) Прямой GET с фильтрами в URL: ОКВЭД 46.49.3 + регион 63 (Самарская
     область) + state-1=on. Гипотеза: сервер сам отдаёт SSR-HTML с выдачей.
     Считаем число `<div class="list-element">` и читаем заголовок
     `Показаны организации 1 — 50 из N`.
  3) Прямой GET с тем же фильтром, но `&page=2` — проверка пагинации через URL.
  4) Headed-сабмит формы: открываем /search-advanced, через DevTools-эмуляцию
     отмечаем чекбокс «Действующая» и поле query='канцтовары', нажимаем
     любой submit-механизм формы и наблюдаем, что Rusprofile делает: новый
     XHR / переход на новый URL / простой re-render.

Запуск:
    python scripts/diag_new_search.py

Требует валидных cookies или RUSPROFILE_LOGIN/PASSWORD в .env.
"""

import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

# Подгружаем корень проекта для импорта src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright, Request, Response

from src.config import LOG_DIR, RUSPROFILE_BASE_URL
from src.rusprofile.auth import (
    COOKIES_FILE,
    _load_cookies,
    _check_auth,
    _login,
    _save_cookies,
)


DIAG_DIR = LOG_DIR / "diag"
DIAG_DIR.mkdir(parents=True, exist_ok=True)
HAR_PATH = DIAG_DIR / "diag_new_search.har"
LOG_PATH = DIAG_DIR / "diag_new_search.log"


# --- Логирование одновременно в stdout и в файл -----------------------------

# Чистим прошлый лог, чтобы не смешивались прогоны.
LOG_PATH.write_text("", encoding="utf-8")
file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
stdout_handler = logging.StreamHandler(sys.stdout)
fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
file_handler.setFormatter(fmt)
stdout_handler.setFormatter(fmt)
logging.basicConfig(level=logging.INFO, handlers=[file_handler, stdout_handler])
logger = logging.getLogger("diag_new_search")


# --- Сценарии (URL-ы) -------------------------------------------------------

# Параметры фильтров из критериев приёмки ТЗ:
#   ОКВЭД 46.49.3 (Торговля оптовая писчебумажными и канцелярскими товарами)
#   регион 63 (Самарская область)
#   статус «Действующая» (state-1)
SEARCH_BARE_URL = f"{RUSPROFILE_BASE_URL}/search-advanced"
SEARCH_FILTERED_URL = (
    f"{RUSPROFILE_BASE_URL}/search-advanced"
    "?63=on&46.49.3=on&okved_strict=on&state-1=on"
)
SEARCH_FILTERED_PAGE2_URL = SEARCH_FILTERED_URL + "&page=2"


# --- Помощник: запись фрагмента HTML и скриншот -----------------------------

async def dump_step(page, step: str) -> None:
    """Сохраняет HTML-снимок и PNG текущей страницы под `diag_new_search_<step>.{html,png}`."""
    try:
        png = DIAG_DIR / f"diag_new_search_{step}.png"
        html = DIAG_DIR / f"diag_new_search_{step}.html"
        try:
            await page.screenshot(path=str(png), full_page=False, timeout=8000)
        except Exception as e:
            logger.warning("screenshot %s упал: %s", step, e)
        try:
            content = await page.content()
            html.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.warning("page.content() %s упал: %s", step, e)
        logger.info("dump-step %s: html=%d КБ", step, html.stat().st_size // 1024)
    except Exception as e:
        logger.warning("dump_step(%s) общий сбой: %s", step, e)


# --- Анализ страницы выдачи (число карточек, заголовок пагинации) -----------

async def report_results(page, step: str) -> None:
    """Логирует ключевые маркеры страницы выдачи: число `.list-element`,
    текст блока пагинации, заголовок «Показаны организации 1 — 50 из N»."""
    try:
        info = await page.evaluate(
            """() => {
                const cards = document.querySelectorAll('div.list-element');
                const desc = document.querySelector('.pager-holder .description');
                const pager = document.querySelector('.pager-holder ul.paging-list');
                const advBody = document.querySelector('#advanced-search-body');
                const formAction = (document.getElementById('filter-form') || {}).action;
                return {
                    url: location.href,
                    list_element_count: cards.length,
                    pager_text: desc ? (desc.textContent || '').trim() : null,
                    pager_html: pager ? pager.innerHTML.slice(0, 500) : null,
                    has_adv_search_body: !!advBody,
                    filter_form_action: formAction || null,
                    title: document.title,
                };
            }"""
        )
        logger.info("results[%s]: %s", step, json.dumps(info, ensure_ascii=False))
    except Exception as e:
        logger.warning("report_results(%s) упал: %s", step, e)


# --- Логирование сетевой активности -----------------------------------------

# Считаем уже подсчитанные «search/api»-запросы — чтобы суммарную сводку
# было удобно вставить в diag_rusprofile_new.md.
NETWORK_LOG: list[dict] = []


def install_network_listeners(page) -> None:
    """Регистрирует слушатели request/response/framenavigated.

    Логируем не всё подряд (страница тянет много третьесторонних трекеров),
    а только запросы к rusprofile.ru, которые могут оказаться submit/AJAX:
    `/search`, `/api/`, `/ajax_*`, JSON или текст form-data.
    """

    def is_interesting(url: str) -> bool:
        if "rusprofile.ru" not in url:
            return False
        skip_substrings = (
            "/assets/", "/images/", "/fonts/", ".svg",
            ".css", ".js", ".png", ".jpg", ".webp", ".woff",
            "clarity.ms", "metrika", "snowplow",
        )
        return not any(s in url for s in skip_substrings)

    async def on_request(req: Request):
        if not is_interesting(req.url):
            return
        try:
            post = req.post_data
        except Exception:
            post = None
        rec = {
            "kind": "request",
            "method": req.method,
            "url": req.url,
            "headers": {k: v for k, v in req.headers.items()
                        if k.lower() in {"content-type", "x-requested-with",
                                          "x-csrf-token", "accept", "referer"}},
            "post": post[:1000] if post else None,
            "post_len": len(post) if post else 0,
        }
        NETWORK_LOG.append(rec)
        logger.info(
            "REQ  %s %s%s",
            req.method, req.url,
            f" [body {len(post)}b: {post[:200]!r}]" if post else "",
        )

    async def on_response(resp: Response):
        if not is_interesting(resp.url):
            return
        try:
            body_bytes = await resp.body()
            ctype = resp.headers.get("content-type", "")
            preview = ""
            if "json" in ctype:
                try:
                    j = json.loads(body_bytes.decode("utf-8", errors="replace"))
                    preview = json.dumps(j, ensure_ascii=False)[:300]
                except Exception:
                    preview = body_bytes[:200].decode("utf-8", errors="replace")
            else:
                preview = body_bytes[:200].decode("utf-8", errors="replace")
        except Exception as e:
            preview = f"<body read failed: {e}>"
            ctype = resp.headers.get("content-type", "")
        rec = {
            "kind": "response",
            "status": resp.status,
            "url": resp.url,
            "content_type": ctype,
            "preview": preview,
        }
        NETWORK_LOG.append(rec)
        logger.info(
            "RESP %d %s ct=%s preview=%s",
            resp.status, resp.url, ctype.split(";")[0], preview[:160].replace("\n", " "),
        )

    async def on_navigated(frame):
        if frame is page.main_frame:
            logger.info("NAV  -> %s", frame.url)

    page.on("request", lambda r: asyncio.create_task(on_request(r)))
    page.on("response", lambda r: asyncio.create_task(on_response(r)))
    page.on("framenavigated", on_navigated)


# --- Основной сценарий ------------------------------------------------------

async def main():
    async with async_playwright() as pw:
        # Для разведки headless достаточно: цель скрипта — собрать HAR и
        # сетевые логи, а не визуально отлаживать Vue. Headed-режим под
        # Hide.me VPN на Windows иногда роняет browser process сразу после
        # старта (TargetClosedError) — в headless поведение стабильнее, и
        # такой же стек живёт на сервере, где парсер потом будет работать.
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
            viewport={"width": 1500, "height": 900},
            locale="ru-RU",
            record_har_path=str(HAR_PATH),
        )

        # 1) Авторизация — через существующий механизм.
        if await _load_cookies(context) and await _check_auth(context):
            logger.info("Авторизация: cookies валидны")
        else:
            logger.info("Авторизация: логинимся заново")
            ok = await _login(context)
            if not ok:
                logger.error("Не удалось залогиниться — останавливаемся")
                await context.close()
                await browser.close()
                return
            await _save_cookies(context)

        page = await context.new_page()
        install_network_listeners(page)

        try:
            # === Шаг 1. /search-advanced без фильтров (что рендерит сервер по умолчанию) ===
            logger.info("=" * 70)
            logger.info("Шаг 1. GET %s (без фильтров)", SEARCH_BARE_URL)
            await page.goto(SEARCH_BARE_URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_selector("#filter-form", timeout=15000)
            except Exception as e:
                logger.warning("filter-form не появился за 15 сек: %s", e)
            await report_results(page, "step1_bare")
            await dump_step(page, "step1_bare")

            # === Шаг 2. /search-advanced с фильтрами в URL (главная гипотеза) ===
            logger.info("=" * 70)
            logger.info("Шаг 2. GET %s", SEARCH_FILTERED_URL)
            # Ключевой момент: ловим, какой запрос Rusprofile сделает САМ при
            # переходе по URL c фильтрами (проверяем гипотезу "выдача через SSR").
            await page.goto(SEARCH_FILTERED_URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(4000)
            await report_results(page, "step2_filtered")
            await dump_step(page, "step2_filtered")

            # === Шаг 3. Та же выдача, страница 2 — проверка пагинации через URL ===
            logger.info("=" * 70)
            logger.info("Шаг 3. GET %s", SEARCH_FILTERED_PAGE2_URL)
            await page.goto(SEARCH_FILTERED_PAGE2_URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(4000)
            await report_results(page, "step3_page2")
            await dump_step(page, "step3_page2")

            # === Шаг 4. Headed-сабмит формы — наблюдаем, что делает Vue ===
            logger.info("=" * 70)
            logger.info("Шаг 4. Headed: открыть форму, отметить виджет, сабмитить")
            await page.goto(SEARCH_BARE_URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_selector("#filter-form", timeout=15000)
            await page.wait_for_timeout(2500)

            # 4.1) Кликаем чекбокс "Действующая" по тексту лейбла. У него нет
            # ни id, ни name, поэтому ищем label по содержимому.
            try:
                clicked = await page.evaluate(
                    """() => {
                        const fs = document.getElementById('additional-search-state');
                        if (!fs) return {ok: false, why: 'no fieldset'};
                        const labels = fs.querySelectorAll('label.checkbox-holder');
                        for (const l of labels) {
                            const txt = (l.textContent || '').trim();
                            if (txt.startsWith('Действующая')) {
                                const cb = l.querySelector('input[type=checkbox]');
                                if (cb) {
                                    cb.checked = true;
                                    cb.dispatchEvent(new Event('change', {bubbles: true}));
                                    cb.dispatchEvent(new Event('input', {bubbles: true}));
                                    l.click();
                                    return {ok: true};
                                }
                            }
                        }
                        return {ok: false, why: 'label not found'};
                    }"""
                )
                logger.info("4.1 'Действующая': %s", clicked)
            except Exception as e:
                logger.warning("4.1 click error: %s", e)

            # 4.2) Заполняем query внутри формы (если поле есть).
            try:
                await page.fill("#filter-form input[name='query']", "канцтовары")
                logger.info("4.2 query=канцтовары — заполнен")
            except Exception as e:
                logger.warning("4.2 query fill error: %s", e)

            # 4.3) Любым способом сабмитим форму. У формы action="#" и
            # внутри нет button[type=submit] — пробуем последовательно
            # все варианты, фиксируем сетевые запросы, которые после этого
            # уйдут.
            try:
                submit_info = await page.evaluate(
                    """() => {
                        const f = document.getElementById('filter-form');
                        if (!f) return {ok: false, why: 'no form'};
                        // Найти любой button/a, который может выглядеть как submit
                        const candidates = [
                            ...document.querySelectorAll('button'),
                            ...document.querySelectorAll('a'),
                        ].filter(el => {
                            const t = (el.textContent || '').trim().toLowerCase();
                            return t === 'найти' || t === 'применить'
                                || t === 'показать' || t === 'поиск';
                        });
                        if (candidates.length) {
                            candidates[0].click();
                            return {ok: true, via: 'button-text', text: candidates[0].textContent.trim()};
                        }
                        if (typeof f.requestSubmit === 'function') {
                            f.requestSubmit();
                            return {ok: true, via: 'requestSubmit'};
                        }
                        f.submit();
                        return {ok: true, via: 'form.submit()'};
                    }"""
                )
                logger.info("4.3 submit: %s", submit_info)
            except Exception as e:
                logger.warning("4.3 submit error: %s", e)

            # Ждём 8 сек — пусть страница успеет среагировать на submit
            # (URL может смениться, могут уйти XHR).
            await page.wait_for_timeout(8000)
            await report_results(page, "step4_after_submit")
            await dump_step(page, "step4_after_submit")

            # === Сводка по сети ===
            logger.info("=" * 70)
            logger.info("Сводка: всего интересных запросов в логе: %d", len(NETWORK_LOG))
            methods = {}
            for r in NETWORK_LOG:
                if r["kind"] != "request":
                    continue
                key = (r["method"], urlparse(r["url"]).path)
                methods[key] = methods.get(key, 0) + 1
            for (method, path), cnt in sorted(methods.items(), key=lambda kv: -kv[1]):
                logger.info("  %3d × %s %s", cnt, method, path)

            logger.info(
                "HAR сохранён: %s, лог: %s, снимки: %s",
                HAR_PATH, LOG_PATH, DIAG_DIR,
            )

        finally:
            # Закрываем context перед browser, иначе HAR не успеет дописаться.
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
