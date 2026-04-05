"""Обработчики команд Telegram-бота."""

import asyncio
import json
import logging

from aiogram import Router, F
from aiogram.types import (
    Message,
    WebAppData,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from aiogram.filters import CommandStart, Command
from playwright.async_api import async_playwright

from src.config import TELEGRAM_WEBAPP_URL, GOOGLE_SHEET_ID
from src.rusprofile.auth import get_authenticated_context
from src.rusprofile.parser import parse_search_results, enrich_company_details, Company
from src.rusprofile.filters import SearchFilters
from src.sheets.client import write_companies, get_sheet_url

logger = logging.getLogger(__name__)
router = Router()

# Хранилище активных задач парсинга
_active_tasks: dict[int, asyncio.Task] = {}


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start."""
    kb = _get_main_keyboard()
    await message.answer(
        "Привет! Я бот для парсинга компаний с Rusprofile.\n\n"
        "Нажмите кнопку ниже, чтобы открыть форму поиска, "
        "или используйте команды:\n"
        "/search — быстрый поиск по ИНН/названию\n"
        "/status — статус текущего парсинга\n"
        "/help — помощь",
        reply_markup=kb,
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка по командам."""
    await message.answer(
        "Команды бота:\n\n"
        "/start — главное меню\n"
        "/search <запрос> — быстрый поиск по ИНН или названию\n"
        "/status — статус текущего парсинга\n"
        "/stop — остановить парсинг\n"
        "/sheet — ссылка на Google таблицу\n"
        "/help — эта справка\n\n"
        "Для расширенного поиска с фильтрами нажмите кнопку "
        "«Открыть парсер» в главном меню.",
    )


@router.message(Command("sheet"))
async def cmd_sheet(message: Message):
    """Ссылка на Google таблицу."""
    url = get_sheet_url()
    await message.answer(f"Google таблица с результатами:\n{url}")


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Статус текущего парсинга."""
    user_id = message.from_user.id
    if user_id in _active_tasks and not _active_tasks[user_id].done():
        await message.answer("Парсинг выполняется... Ожидайте результатов.")
    else:
        await message.answer("Нет активных задач парсинга.")


@router.message(Command("stop"))
async def cmd_stop(message: Message):
    """Остановка текущего парсинга."""
    user_id = message.from_user.id
    if user_id in _active_tasks and not _active_tasks[user_id].done():
        _active_tasks[user_id].cancel()
        del _active_tasks[user_id]
        await message.answer("Парсинг остановлен.")
    else:
        await message.answer("Нет активных задач для остановки.")


@router.message(Command("search"))
async def cmd_search(message: Message):
    """Быстрый поиск по ИНН или названию."""
    query = message.text.replace("/search", "").strip()
    if not query:
        await message.answer("Укажите ИНН или название:\n/search 7701234567")
        return

    await message.answer(f"Ищу: {query}...")

    filters = SearchFilters(query=query)
    user_id = message.from_user.id

    task = asyncio.create_task(_run_parsing(message, filters))
    _active_tasks[user_id] = task


@router.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    """Обработка данных из Mini App."""
    try:
        data = json.loads(message.web_app_data.data)
        logger.info("Получены данные из Mini App: %s", data)

        filters = SearchFilters(
            region=data.get("region"),
            okved=data.get("okved"),
            revenue_from=_parse_int(data.get("revenue_from")),
            revenue_to=_parse_int(data.get("revenue_to")),
            org_type=data.get("org_type"),
            has_phone=data.get("has_phone", False),
            has_site=data.get("has_site", False),
            has_email=data.get("has_email", False),
            business_size=data.get("business_size"),
        )

        await message.answer("Запускаю парсинг с заданными фильтрами...")
        user_id = message.from_user.id

        task = asyncio.create_task(_run_parsing(message, filters))
        _active_tasks[user_id] = task

    except json.JSONDecodeError:
        await message.answer("Ошибка: неверный формат данных из Mini App")
    except Exception as e:
        logger.error("Ошибка обработки данных Mini App: %s", e)
        await message.answer(f"Произошла ошибка: {e}")


async def _run_parsing(message: Message, filters: SearchFilters):
    """Запускает процесс парсинга и отправляет результаты."""
    status_msg = await message.answer("Подключаюсь к Rusprofile...")

    try:
        async with async_playwright() as pw:
            context = await get_authenticated_context(pw)

            # Прогресс-коллбэк
            async def on_progress(total: int, processed: int):
                try:
                    await status_msg.edit_text(
                        f"Парсинг...\n"
                        f"Найдено: {total} компаний\n"
                        f"Обработано: {processed}"
                    )
                except Exception:
                    pass  # Telegram может ограничить частоту редактирования

            # Парсим поисковую выдачу
            await status_msg.edit_text("Ищу компании по заданным фильтрам...")
            companies = await parse_search_results(context, filters, on_progress)

            if not companies:
                await status_msg.edit_text(
                    "Компании не найдены. Попробуйте изменить фильтры."
                )
                await context.browser.close()
                return

            # Обогащаем детальными данными
            await status_msg.edit_text(
                f"Найдено {len(companies)} компаний. Собираю контактные данные..."
            )
            companies = await enrich_company_details(context, companies, on_progress)

            await context.browser.close()

        # Выгружаем в Google Sheets
        await status_msg.edit_text("Выгружаю результаты в Google Sheets...")
        sheet_url = write_companies(companies)

        await status_msg.edit_text(
            f"Готово! Найдено {len(companies)} компаний.\n\n"
            f"Результаты в Google Sheets:\n{sheet_url}"
        )

    except asyncio.CancelledError:
        await status_msg.edit_text("Парсинг отменён.")
    except Exception as e:
        logger.error("Ошибка парсинга: %s", e)
        await status_msg.edit_text(f"Ошибка парсинга: {e}")
    finally:
        user_id = message.from_user.id
        _active_tasks.pop(user_id, None)


def _get_main_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура главного меню."""
    buttons = []

    if TELEGRAM_WEBAPP_URL:
        buttons.append([
            InlineKeyboardButton(
                text="Открыть парсер",
                web_app=WebAppInfo(url=TELEGRAM_WEBAPP_URL),
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="Google таблица", callback_data="show_sheet")
    ])
    buttons.append([
        InlineKeyboardButton(text="Помощь", callback_data="show_help")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _parse_int(value) -> int | None:
    """Безопасное преобразование в int."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
