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
    """Выполняет двухэтапный вход: email → пароль."""
    page = await context.new_page()
    try:
        logger.info("Открываем главную rusprofile.ru...")
        await _goto(page, RUSPROFILE_BASE_URL, timeout=45000)

        # На Rusprofile JS-модалка монтируется не сразу после commit —
        # ждём появления самого триггера, чтобы быть уверенными, что Vue
        # приложение инициализировалось.
        await page.wait_for_selector("#menu-personal-trigger", timeout=20000)
        await page.wait_for_timeout(1500)

        await _dismiss_cookie_banner(page)

        logger.info("Открываем модалку входа...")
        # Кликаем именно по кнопке внутри триггера на случай, если Vue вешает
        # обработчик на вложенный span, а не на контейнер.
        await page.locator("#menu-personal-trigger").first.click()

        # Шаг 1 — email
        logger.info("Вводим email...")
        email_input = page.locator('input[name="email"][type="email"]').first
        await email_input.wait_for(state="visible", timeout=15000)
        await email_input.fill(RUSPROFILE_LOGIN)
        await page.wait_for_timeout(500)

        # Кнопка «Продолжить» — синяя кнопка внутри модалки
        continue_btn = page.locator(
            '.vModal-body button:has-text("Продолжить")'
        ).first
        await continue_btn.click()

        # Шаг 2 — пароль (поле появляется после клика)
        logger.info("Вводим пароль...")
        password_input = page.locator('input[name="current-password"]').first
        await password_input.wait_for(state="visible", timeout=10000)
        await password_input.fill(RUSPROFILE_PASSWORD)
        await page.wait_for_timeout(500)

        # Кнопка «Войти» в модалке; после ввода пароля она становится активной
        submit_btn = page.locator(
            '.vModal-body button.btn-blue:has-text("Войти")'
        ).first
        await submit_btn.wait_for(state="visible", timeout=5000)
        # Ждём, пока снимется disabled
        await page.wait_for_function(
            """() => {
                const btns = document.querySelectorAll('.vModal-body button.btn-blue');
                for (const b of btns) {
                    if ((b.textContent || '').includes('Войти') && !b.disabled) return true;
                }
                return false;
            }""",
            timeout=5000,
        )
        await submit_btn.click()

        # Ждём закрытия модалки / авторизации
        await page.wait_for_timeout(5000)

        # Rusprofile может показать уведомление «Аккаунт используется
        # на нескольких устройствах» — закрываем его кнопкой «Продолжить работу».
        # Пока модалка открыта, #menu-personal-trigger ещё не обновляется на имя
        # пользователя, поэтому _is_authenticated вернёт False, если пропустить
        # этот шаг.
        try:
            continue_link = page.locator(
                '.mw-shared-account a.btn-blue, '
                '.mw-shared-account a:has-text("Продолжить работу")'
            ).first
            if await continue_link.is_visible(timeout=5000):
                logger.warning(
                    "Rusprofile сообщил о входе с нескольких устройств — "
                    "закрываем уведомление. Клиент мог быть выкинут из своего браузера."
                )
                await continue_link.click()
                await page.wait_for_timeout(3000)
        except Exception:
            pass

        if await _is_authenticated(page):
            logger.info("Авторизация успешна")
            await _save_cookies(context)
            return True

        # Могла показаться ошибка в модалке — логируем её текст
        error_text = await page.evaluate("""
            () => {
                const err = document.querySelector('.vModal-body .error, .vModal-body [class*="error"]');
                return err ? (err.textContent || '').trim() : null;
            }
        """)
        logger.error("Авторизация не удалась. Ошибка модалки: %s", error_text)
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
