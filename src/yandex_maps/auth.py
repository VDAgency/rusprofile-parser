"""Авторизация на Яндекс.Картах через Playwright.

Сохраняет storage_state в `config/yandex_session.json` (см. `.env` →
`YANDEX_SESSION_PATH`). При следующем запуске сессия восстанавливается из
файла, и логин не нужен.

Если сессия невалидна (Я.Карты редиректят на logout / просят пароль) —
пробуется повторный логин. Капча и 2FA — fail-safe: возвращаем False,
парсинг продолжается без авторизации.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import BrowserContext, Page

logger = logging.getLogger(__name__)

YANDEX_LOGIN_URL = "https://passport.yandex.ru/auth"
YANDEX_PROFILE_URL = "https://yandex.ru/profile"
YANDEX_AUTH_CHECK_TIMEOUT_MS = 8000


def session_state_path() -> Path | None:
    """Путь к файлу сессии. None, если переменная не задана."""
    import os
    raw = os.getenv("YANDEX_SESSION_PATH", "config/yandex_session.json").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        # Считаем относительно корня проекта (где запускается код).
        from src.config import BASE_DIR
        p = BASE_DIR / p
    return p


def has_valid_session_file() -> bool:
    """Файл сессии существует и не пустой."""
    p = session_state_path()
    return bool(p and p.exists() and p.stat().st_size > 50)


async def is_logged_in(page: "Page") -> bool:
    """True, если cookie сессии валиден (страница профиля открывается без редиректа)."""
    try:
        await page.goto(
            YANDEX_PROFILE_URL,
            wait_until="domcontentloaded",
            timeout=YANDEX_AUTH_CHECK_TIMEOUT_MS,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось проверить сессию Я.Паспорта: %s", e)
        return False

    url = page.url
    if "passport.yandex.ru" in url and "auth" in url:
        return False
    if "yandex.ru/profile" in url:
        return True
    # Иные случаи — не уверены, считаем не залогиненным.
    return False


async def perform_login(page: "Page", login: str, password: str) -> bool:
    """Логинится в Я.Паспорт через UI.

    Возвращает True при успехе. Капча/2FA → False, лог warning.
    """
    if not login or not password:
        logger.info("YANDEX_LOGIN/YANDEX_PASSWORD не заданы — пропускаю логин")
        return False

    try:
        await page.goto(YANDEX_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)

        # Ввод логина
        await page.fill('input[name="login"]', login)
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(1500)

        # Капча на этапе логина — отбой.
        if await page.query_selector('iframe[src*="captcha"], .CheckboxCaptcha'):
            logger.warning("Я.Паспорт показал капчу при логине — пропускаю авторизацию")
            return False

        # Ввод пароля
        try:
            await page.wait_for_selector('input[name="passwd"]', timeout=8000)
        except Exception:
            logger.warning("Поле пароля не появилось — возможно, требуется 2FA или код по SMS")
            return False
        await page.fill('input[name="passwd"]', password)
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(3000)

        # Проверка, что мы залогинены
        if await is_logged_in(page):
            logger.info("Я.Паспорт: вход выполнен (login=%s)", login)
            return True

        logger.warning("Я.Паспорт: логин не подтверждён (возможно 2FA)")
        return False

    except Exception as e:  # noqa: BLE001
        logger.warning("Ошибка логина в Я.Паспорт: %s", e)
        return False


async def save_storage_state(context: "BrowserContext") -> None:
    """Сохраняет cookies и localStorage в файл сессии."""
    p = session_state_path()
    if not p:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        await context.storage_state(path=str(p))
        logger.info("Я.Карты session state сохранён: %s", p)
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось сохранить session state: %s", e)


async def ensure_authenticated(
    context: "BrowserContext",
    login: str | None = None,
    password: str | None = None,
) -> bool:
    """Проверить сессию, при необходимости — залогиниться.

    Возвращает True, если в итоге сессия валидна (cookies подходят).
    False — если логин не удался; парсинг должен продолжиться без auth.

    Storage state будет загружен/сохранён через
    ``YANDEX_SESSION_PATH``. При первом запуске контекста рекомендуется
    создавать его с ``storage_state=session_state_path()`` если файл
    есть — тогда cookies подгрузятся автоматически.
    """
    page = await context.new_page()
    try:
        if await is_logged_in(page):
            logger.info("Я.Карты: сессия из storage_state валидна")
            return True

        # Сессия мертва — пробуем логин.
        import os
        login = login or os.getenv("YANDEX_LOGIN", "").strip()
        password = password or os.getenv("YANDEX_PASSWORD", "").strip()

        if not login or not password:
            logger.info("Я.Карты: логин не настроен в .env, продолжаю без auth")
            return False

        if await perform_login(page, login, password):
            await save_storage_state(context)
            return True
        return False
    finally:
        await page.close()
