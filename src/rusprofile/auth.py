"""Авторизация на Rusprofile через Playwright.

Rusprofile использует двухэтапную форму в одном модальном окне:
1. Клик по кнопке «Войти» в шапке → открывается модалка с полем email.
2. Ввод email → клик «Продолжить» → та же модалка показывает поле пароля.
3. Ввод пароля → клик «Войти» → модалка закрывается, сессия активна.

После успешного логина cookies сохраняются в config/cookies.json,
при следующих запусках сначала пробуем восстановить сессию из cookies.
"""

import json
import logging

from playwright.async_api import BrowserContext

from src.config import (
    RUSPROFILE_LOGIN,
    RUSPROFILE_PASSWORD,
    RUSPROFILE_BASE_URL,
    BASE_DIR,
)

logger = logging.getLogger(__name__)

COOKIES_FILE = BASE_DIR / "config" / "cookies.json"


async def _save_cookies(context: BrowserContext) -> None:
    cookies = await context.cookies()
    COOKIES_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
    logger.info("Cookies сохранены (%d шт)", len(cookies))


async def _load_cookies(context: BrowserContext) -> bool:
    if not COOKIES_FILE.exists():
        return False
    try:
        cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        if not isinstance(cookies, list) or not cookies:
            return False
        await context.add_cookies(cookies)
        logger.info("Загружено %d cookies из файла", len(cookies))
        return True
    except Exception as e:
        logger.warning("Не удалось загрузить cookies: %s", e)
        return False


async def _is_authenticated(page) -> bool:
    """Проверяет по главной странице, авторизованы ли мы.

    Для анонимов в шапке виден триггер с текстом «Войти», для авторизованных
    пользователей этот текст исчезает.
    """
    return await page.evaluate("""
        () => {
            const trigger = document.querySelector('#menu-personal-trigger');
            if (!trigger) return true;
            const text = (trigger.textContent || '').trim().toLowerCase();
            return !text.includes('войти');
        }
    """)


async def _goto(page, url: str, timeout: int = 45000, retries: int = 3) -> None:
    """Открывает URL, устойчивый к особенностям Rusprofile.

    У headless-Chromium на сервере подтверждено поведение: первый goto()
    прямо на rusprofile.ru после создания страницы зависает (wait_until
    никогда не срабатывает). Если сначала загрузить ``about:blank``, а
    потом основной URL — всё работает мгновенно. Поэтому делаем warmup.

    Дальше используем ``wait_until='commit'`` (вернуться как только ядро
    получит первый байт) и отдельно ждём ``body``: такое сочетание работает
    надёжно даже когда страница подгружает долгую аналитику.

    Rusprofile иногда отвечает медленно/зависает (rate-limit). Повторяем
    до ``retries`` раз с небольшим бэкоффом, прежде чем пробросить ошибку.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            try:
                await page.goto("about:blank")
            except Exception:
                pass
            await page.goto(url, wait_until="commit", timeout=timeout)
            try:
                await page.wait_for_selector("body", timeout=timeout)
            except Exception:
                pass
            return
        except Exception as e:
            last_exc = e
            logger.warning(
                "goto %s неудачно (попытка %d/%d): %s", url, attempt, retries, e
            )
            await page.wait_for_timeout(2000 * attempt)
    if last_exc:
        raise last_exc


async def _check_auth(context: BrowserContext) -> bool:
    page = await context.new_page()
    try:
        await _goto(page, RUSPROFILE_BASE_URL, timeout=30000)
        await page.wait_for_timeout(1500)
        authed = await _is_authenticated(page)
        logger.info("Проверка сессии: %s", "активна" if authed else "истекла")
        return authed
    except Exception as e:
        logger.warning("Ошибка проверки сессии: %s", e)
        return False
    finally:
        await page.close()


async def _dismiss_cookie_banner(page) -> None:
    """Закрывает баннер о cookies, если он виден."""
    try:
        btn = page.locator('button:has-text("Понятно")').first
        if await btn.is_visible(timeout=1500):
            await btn.click()
            await page.wait_for_timeout(300)
    except Exception:
        pass


async def _login(context: BrowserContext) -> bool:
    """Выполняет вход через прямой POST к /auth.php?action=login.

    Rusprofile в 2026-05 перевёл модалку входа на lazy-loaded Vue-компонент
    с invisible reCAPTCHA, которая не монтируется в headless Chromium.
    Вместо UI-взаимодействия используем прямой API-вызов:
      POST /auth.php?action=login
      FormData: login=EMAIL, password=PASS
      Header: X-Csrf-Token: <значение cookie __Host-csrf-token>
    При success: true браузер получает Set-Cookie с сессией.
    """
    page = await context.new_page()
    try:
        logger.info("Открываем главную rusprofile.ru для получения CSRF-токена...")
        await _goto(page, RUSPROFILE_BASE_URL, timeout=45000)
        await page.wait_for_timeout(2000)

        logger.info("Отправляем POST /auth.php?action=login...")
        result = await page.evaluate(
            """async (creds) => {
                function getCookie(name) {
                    const v = `; ${document.cookie}`;
                    const parts = v.split(`; ${name}=`);
                    return parts.length === 2 ? parts.pop().split(';').shift() : null;
                }
                const csrf = getCookie('__Host-csrf-token') || '';
                const fd = new FormData();
                fd.append('login', creds.login);
                fd.append('password', creds.password);
                try {
                    const resp = await fetch('/auth.php?action=login', {
                        method: 'POST',
                        headers: {'X-Csrf-Token': csrf},
                        body: fd,
                    });
                    const text = await resp.text();
                    return JSON.parse(text);
                } catch (e) {
                    return {success: false, message: e.toString()};
                }
            }""",
            {"login": RUSPROFILE_LOGIN, "password": RUSPROFILE_PASSWORD},
        )

        if result.get("success"):
            logger.info("Авторизация успешна (hasPaidSubscription=%s)",
                        result.get("fields", {}).get("hasPaidSubscription"))
            await _save_cookies(context)
            return True

        code = result.get("code")
        msg = result.get("message", "")
        logger.error("Авторизация не удалась: code=%s message=%s", code, msg)
        return False

    except Exception as e:
        logger.error("Ошибка авторизации: %s", e)
        return False
    finally:
        await page.close()


async def get_authenticated_context(playwright) -> BrowserContext:
    """Возвращает авторизованный контекст браузера.

    Сначала пробует загрузить cookies, если сессия истекла — логинится заново.
    """
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            # Нужен, когда запускаемся под root на Linux (systemd). Без
            # него chromium зависает до таймаута при любом goto().
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
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
    )

    if await _load_cookies(context) and await _check_auth(context):
        return context

    logger.info("Выполняем авторизацию на Rusprofile...")
    if not await _login(context):
        await browser.close()
        raise RuntimeError("Не удалось авторизоваться на Rusprofile")

    return context
