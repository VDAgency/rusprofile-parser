"""Авторизация на Rusprofile через Playwright."""

import asyncio
import json
import logging
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext

from src.config import (
    RUSPROFILE_LOGIN,
    RUSPROFILE_PASSWORD,
    RUSPROFILE_BASE_URL,
    BASE_DIR,
)

logger = logging.getLogger(__name__)

COOKIES_FILE = BASE_DIR / "config" / "cookies.json"


async def _save_cookies(context: BrowserContext) -> None:
    """Сохраняет cookies в файл для повторного использования."""
    cookies = await context.cookies()
    COOKIES_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
    logger.info("Cookies сохранены в %s", COOKIES_FILE)


async def _load_cookies(context: BrowserContext) -> bool:
    """Загружает cookies из файла. Возвращает True если успешно."""
    if not COOKIES_FILE.exists():
        return False
    try:
        cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        await context.add_cookies(cookies)
        logger.info("Cookies загружены из файла")
        return True
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("Не удалось загрузить cookies: %s", e)
        return False


async def _login(context: BrowserContext) -> bool:
    """Выполняет вход на Rusprofile через форму логина."""
    page = await context.new_page()
    try:
        logger.info("Переходим на страницу входа...")
        await page.goto(
            f"{RUSPROFILE_BASE_URL}/login", wait_until="domcontentloaded", timeout=30000
        )
        await page.wait_for_timeout(2000)

        # Заполняем форму
        email_input = page.locator('input[name="email"], input[type="email"]').first
        await email_input.fill(RUSPROFILE_LOGIN)

        password_input = page.locator('input[name="password"], input[type="password"]').first
        await password_input.fill(RUSPROFILE_PASSWORD)

        # Нажимаем кнопку входа
        submit_btn = page.locator(
            'button[type="submit"], input[type="submit"], .btn-login'
        ).first
        await submit_btn.click()

        # Ждём завершения авторизации
        await page.wait_for_timeout(3000)

        # Проверяем успешность — ищем признаки авторизации
        current_url = page.url
        if "login" not in current_url or "cabinet" in current_url:
            logger.info("Авторизация успешна")
            await _save_cookies(context)
            return True

        # Дополнительная проверка — ищем элемент личного кабинета
        cabinet_link = page.locator('a[href*="cabinet"], .user-menu, .profile-link')
        if await cabinet_link.count() > 0:
            logger.info("Авторизация успешна (найден личный кабинет)")
            await _save_cookies(context)
            return True

        logger.error("Авторизация не удалась — остались на странице логина")
        return False

    except Exception as e:
        logger.error("Ошибка авторизации: %s", e)
        return False
    finally:
        await page.close()


async def _check_auth(context: BrowserContext) -> bool:
    """Проверяет, активна ли сессия (cookies валидны)."""
    page = await context.new_page()
    try:
        await page.goto(
            f"{RUSPROFILE_BASE_URL}/cabinet", wait_until="domcontentloaded", timeout=15000
        )
        await page.wait_for_timeout(1500)

        # Если нас не перекинуло на логин — сессия активна
        if "login" not in page.url:
            logger.info("Сессия активна")
            return True
        logger.info("Сессия истекла")
        return False
    except Exception as e:
        logger.warning("Ошибка проверки сессии: %s", e)
        return False
    finally:
        await page.close()


async def get_authenticated_context(playwright) -> BrowserContext:
    """Возвращает авторизованный контекст браузера.

    Сначала пробует загрузить cookies, если не работают — логинится заново.
    """
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
    )

    # Пробуем загрузить сохранённые cookies
    cookies_loaded = await _load_cookies(context)
    if cookies_loaded:
        if await _check_auth(context):
            return context

    # Логинимся заново
    logger.info("Выполняем авторизацию на Rusprofile...")
    success = await _login(context)
    if not success:
        await browser.close()
        raise RuntimeError("Не удалось авторизоваться на Rusprofile")

    return context
