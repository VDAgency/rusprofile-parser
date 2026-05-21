"""Точка входа — запуск Telegram-бота."""

import asyncio
import logging
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    MenuButtonWebApp,
    MenuButtonCommands,
    WebAppInfo,
)

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_WEBAPP_URL, LOG_DIR, LOG_FILE
from src.bot.handlers import router
from src.api.server import start_api_server
from src.services.billing_scheduler import register_billing_jobs


def setup_logging():
    """Настройка логирования."""
    LOG_DIR.mkdir(exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Убираем спам от библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)


async def setup_bot_commands(bot: Bot):
    """Команды в меню (слева от поля ввода) и Menu Button."""
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="search", description="Быстрый поиск по ИНН или названию"),
        BotCommand(command="status", description="Статус парсинга"),
        BotCommand(command="stop", description="Остановить парсинг"),
        BotCommand(command="sheet", description="Ссылка на Google таблицу"),
        BotCommand(command="help", description="Справка"),
    ]
    await bot.set_my_commands(commands)

    # Кнопка у поля ввода: если есть Mini App URL — открываем его,
    # иначе показываем список команд.
    if TELEGRAM_WEBAPP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Парсер",
                web_app=WebAppInfo(url=TELEGRAM_WEBAPP_URL),
            )
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main():
    """Запуск бота."""
    setup_logging()
    logger = logging.getLogger(__name__)

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан в .env")
        sys.exit(1)

    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    await setup_bot_commands(bot)

    logger.info("Бот запускается...")
    me = await bot.get_me()
    logger.info("Бот @%s (ID: %s) запущен", me.username, me.id)

    # HTTP API для Mini App (история, re-export, Excel) живёт в том же
    # процессе. Nginx проксирует /api/ → 127.0.0.1:API_PORT.
    # Передаём bot — для отправки .xlsx прямо в чат пользователя.
    api_runner = await start_api_server(bot=bot)

    # APScheduler с billing-job'ами (trial-expiry, subscription renewal,
    # past_due, reminders). Один scheduler на весь процесс — крутится
    # параллельно с polling'ом.
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler(timezone="UTC")
    register_billing_jobs(scheduler, bot=bot)
    scheduler.start()
    logger.info("Billing scheduler запущен")

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await api_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
