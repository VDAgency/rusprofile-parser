"""Обработчики команд Telegram-бота."""

import asyncio
import json
import logging

from aiogram import Router, F
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
)
from aiogram.filters import CommandStart, Command
from playwright.async_api import async_playwright

from src.config import (
    TELEGRAM_WEBAPP_URL,
    GOOGLE_SHEET_ID,
    YANDEX_SHEET_HEADERS,
    YANDEX_SHEET_NAME,
    YANDEX_MAX_PLACES,
)
from src.rusprofile.auth import get_authenticated_context
from src.rusprofile.parser import parse_search_results, enrich_company_details, Company
from src.rusprofile.filters import SearchFilters
from src.sheets.client import write_companies, get_sheet_url
from src.yandex_maps.runner import parse_yandex

logger = logging.getLogger(__name__)
router = Router()

# Хранилище активных задач парсинга
_active_tasks: dict[int, asyncio.Task] = {}

# Тексты кнопок постоянного меню (reply keyboard)
BTN_PARSE = "🚀 Открыть парсер"
BTN_SHEET = "📊 Таблица"
BTN_STATUS = "⚙️ Статус"
BTN_HELP = "ℹ️ Помощь"


def _get_main_keyboard() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу экрана."""
    buttons = []

    if TELEGRAM_WEBAPP_URL:
        buttons.append([
            KeyboardButton(text=BTN_PARSE, web_app=WebAppInfo(url=TELEGRAM_WEBAPP_URL)),
        ])

    buttons.append([
        KeyboardButton(text=BTN_SHEET),
        KeyboardButton(text=BTN_STATUS),
    ])
    buttons.append([
        KeyboardButton(text=BTN_HELP),
    ])

    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        is_persistent=True,
    )


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start."""
    kb = _get_main_keyboard()
    text = (
        "Привет! Я бот для парсинга компаний с Rusprofile.\n\n"
        "Нажмите «🚀 Открыть парсер», чтобы задать фильтры и запустить поиск.\n\n"
        "Доступные команды:\n"
        "/search &lt;запрос&gt; — быстрый поиск по ИНН или названию\n"
        "/status — статус текущего парсинга\n"
        "/stop — остановить парсинг\n"
        "/sheet — ссылка на Google таблицу\n"
        "/help — эта справка"
    )
    await message.answer(text, reply_markup=kb)


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message):
    """Справка по командам."""
    await message.answer(
        "Команды бота:\n\n"
        "/start — главное меню\n"
        "/search &lt;запрос&gt; — быстрый поиск по ИНН или названию\n"
        "/status — статус текущего парсинга\n"
        "/stop — остановить парсинг\n"
        "/sheet — ссылка на Google таблицу\n"
        "/help — эта справка\n\n"
        "Для расширенного поиска с фильтрами нажмите "
        "«🚀 Открыть парсер» в меню.",
    )


@router.message(Command("sheet"))
@router.message(F.text == BTN_SHEET)
async def cmd_sheet(message: Message):
    """Ссылка на Google таблицу."""
    url = get_sheet_url()
    await message.answer(f"Google таблица с результатами:\n{url}")


@router.message(Command("status"))
@router.message(F.text == BTN_STATUS)
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


def _has_active_task(user_id: int) -> bool:
    task = _active_tasks.get(user_id)
    return task is not None and not task.done()


@router.message(Command("search"))
async def cmd_search(message: Message):
    """Быстрый поиск по ИНН или названию."""
    query = message.text.replace("/search", "").strip()
    if not query:
        await message.answer("Укажите ИНН или название:\n/search 7701234567")
        return

    user_id = message.from_user.id
    if _has_active_task(user_id):
        await message.answer(
            "У вас уже выполняется парсинг. Дождитесь его завершения или /stop."
        )
        return

    await message.answer(f"Ищу: {query}...")
    filters = SearchFilters(query=query)
    task = asyncio.create_task(_run_parsing(message, filters))
    _active_tasks[user_id] = task


@router.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    """Обработка данных из Mini App.

    Mini App отправляет поле ``source`` со значением ``"rusprofile"`` или
    ``"yandex_maps"``. По нему диспатчим в соответствующий парсер.
    """
    try:
        data = json.loads(message.web_app_data.data)
        logger.info("Получены данные из Mini App: %s", data)

        user_id = message.from_user.id
        if _has_active_task(user_id):
            await message.answer(
                "У вас уже выполняется парсинг. Дождитесь завершения или /stop."
            )
            return

        source = data.get("source", "rusprofile")

        if source == "yandex_maps":
            region = (data.get("region") or "").strip()
            category = (data.get("category") or "").strip()
            if not region or not category:
                await message.answer(
                    "Для парсинга Яндекс Карт укажите и регион, и вид деятельности."
                )
                return
            max_places = _parse_int(data.get("max_places")) or YANDEX_MAX_PLACES

            await message.answer(
                f"Ищу «{category}» в регионе «{region}» на Яндекс Картах..."
            )
            task = asyncio.create_task(
                _run_yandex_parsing(message, region, category, max_places)
            )
            _active_tasks[user_id] = task
            return

        filters = SearchFilters(
            query=data.get("query") or None,
            region=_as_list(data.get("region")),
            okved=_as_list(data.get("okved")),
            okopf=_as_list(data.get("okopf")),
            msp=_as_list(data.get("msp")),
            status=_as_list(data.get("status")) or ["1"],  # по умолчанию «Действующая»
            finance_revenue_from=_parse_int(data.get("finance_revenue_from")),
            finance_revenue_to=_parse_int(data.get("finance_revenue_to")),
            finance_profit_from=_parse_int(data.get("finance_profit_from")),
            finance_profit_to=_parse_int(data.get("finance_profit_to")),
            capital_from=_parse_int(data.get("capital_from")),
            capital_to=_parse_int(data.get("capital_to")),
            sshr_from=_parse_int(data.get("sshr_from")),
            sshr_to=_parse_int(data.get("sshr_to")),
            has_phones=bool(data.get("has_phones")),
            has_emails=bool(data.get("has_emails")),
            has_sites=bool(data.get("has_sites")),
            finance_has_actual_year_data=bool(data.get("finance_has_actual_year_data")),
            not_defendant=bool(data.get("not_defendant")),
        )

        await message.answer("Запускаю парсинг с заданными фильтрами...")
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

            async def on_progress(total: int, processed: int):
                try:
                    await status_msg.edit_text(
                        f"Парсинг...\n"
                        f"Найдено: {total} компаний\n"
                        f"Обработано: {processed}"
                    )
                except Exception:
                    pass  # Telegram может ограничить частоту редактирования

            await status_msg.edit_text("Ищу компании по заданным фильтрам...")
            companies = await parse_search_results(context, filters, on_progress)

            if not companies:
                await status_msg.edit_text(
                    "Компании не найдены. Попробуйте изменить фильтры."
                )
                await context.browser.close()
                return

            await status_msg.edit_text(
                f"Найдено {len(companies)} компаний. Собираю контактные данные..."
            )
            companies = await enrich_company_details(context, companies, on_progress)

            await context.browser.close()

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


async def _run_yandex_parsing(
    message: Message,
    region: str,
    category: str,
    max_places: int,
):
    """Запускает парсинг Яндекс Карт и выгружает результат в Google Sheets."""
    status_msg = await message.answer("Подключаюсь к Яндекс Картам...")

    try:
        async def on_progress(total: int, processed: int):
            try:
                await status_msg.edit_text(
                    f"Яндекс Карты: найдено {total}, обработано {processed}..."
                )
            except Exception:
                pass  # Telegram ограничивает частоту edit_text

        await status_msg.edit_text(
            f"Ищу «{category}» в «{region}» на Яндекс Картах..."
        )
        places = await parse_yandex(
            region=region,
            category=category,
            max_places=max_places,
            progress_callback=on_progress,
            with_details=True,
        )

        if not places:
            await status_msg.edit_text(
                "Ничего не найдено. Проверьте регион и рубрику или попробуйте позже."
            )
            return

        await status_msg.edit_text(
            f"Найдено {len(places)} организаций. Выгружаю в Google Sheets..."
        )
        sheet_url = write_companies(
            places,
            sheet_name=YANDEX_SHEET_NAME,
            headers=YANDEX_SHEET_HEADERS,
        )

        await status_msg.edit_text(
            f"Готово! Яндекс Карты — {len(places)} организаций.\n\n"
            f"Лист «{YANDEX_SHEET_NAME}»:\n{sheet_url}"
        )

    except asyncio.CancelledError:
        await status_msg.edit_text("Парсинг отменён.")
    except Exception as e:
        logger.error("Ошибка парсинга Яндекс Карт: %s", e)
        await status_msg.edit_text(f"Ошибка: {e}")
    finally:
        user_id = message.from_user.id
        _active_tasks.pop(user_id, None)


def _parse_int(value) -> int | None:
    """Безопасное преобразование в int."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _as_list(value) -> list[str]:
    """Нормализует значение от Mini App в список строк.

    Mini App может прислать None, одиночное значение или массив —
    внутренне мы всегда работаем со списком (поля-виджеты Rusprofile).
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)]
