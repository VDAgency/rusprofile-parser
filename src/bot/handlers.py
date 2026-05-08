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

from src.config import (
    DEFAULT_MAX_NEW,
    MAX_NEW_HARD_LIMIT,
    TELEGRAM_WEBAPP_URL,
)
from src.rusprofile.filters import SearchFilters
from src.sheets.client import get_sheet_url
from src.okved.search import filter_codes_in_handbook
from src.services.parse_service import run_rusprofile, run_yandex

logger = logging.getLogger(__name__)
router = Router()

# Хранилище активных задач парсинга
_active_tasks: dict[int, asyncio.Task] = {}

# Тексты кнопок постоянного меню (reply keyboard)
BTN_PARSE = "🚀 Открыть парсер"
BTN_SHEET = "📊 Таблица"
BTN_STATUS = "⚙️ Статус"
BTN_HELP = "ℹ️ Помощь"


def _webapp_url_for(user_id: int | None) -> str:
    """Возвращает URL Mini App с подставленным uid в query.

    Если бот не зарегистрирован как Mini App в BotFather (поле
    `has_main_web_app=false`), Telegram не передаёт ни `initData`,
    ни `initDataUnsafe.user`. Без user_id наш бэкенд ничего не может
    идентифицировать и возвращает 401 на /api/.

    Обходной путь: подкладываем user_id прямо в URL — JS Mini App
    прочитает его из window.location.search и передаст в API. Сервер
    приложит whitelist-проверку (`ALLOW_UNSAFE_USER_IDS`).
    """
    base = TELEGRAM_WEBAPP_URL or ""
    if not base or not user_id:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}uid={user_id}"


def _get_main_keyboard(user_id: int | None = None) -> ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу экрана.

    Принимает ``user_id`` чтобы подставить его в URL Mini App
    (см. ``_webapp_url_for``). Без user_id — оставляем базовый URL.
    """
    buttons = []

    if TELEGRAM_WEBAPP_URL:
        url = _webapp_url_for(user_id)
        buttons.append([
            KeyboardButton(text=BTN_PARSE, web_app=WebAppInfo(url=url)),
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
    kb = _get_main_keyboard(user_id=message.from_user.id)
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
    raw = {"query": query}
    task = asyncio.create_task(_run_rusprofile(message, filters, raw, max_new=10))
    _active_tasks[user_id] = task


@router.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    """Обработка данных из Mini App."""
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
        max_new = _clamp_max_new(_parse_int(data.get("max_new")))

        if source == "yandex_maps":
            region = (data.get("region") or "").strip()
            category = (data.get("category") or "").strip()
            if not region or not category:
                await message.answer(
                    "Для парсинга Яндекс Карт укажите и регион, и вид деятельности."
                )
                return

            await message.answer(
                f"Ищу «{category}» в регионе «{region}» на Яндекс Картах "
                f"(до {max_new} новых)..."
            )
            task = asyncio.create_task(
                _run_yandex(message, region, category, max_new)
            )
            _active_tasks[user_id] = task
            return

        # Mini App присылает okved_strict явным булевым значением:
        # true (по умолчанию) — искать только по основному ОКВЭД компании,
        # false — также по дополнительным ОКВЭД.
        okved_strict_raw = data.get("okved_strict")
        okved_strict_value: bool | None
        if okved_strict_raw is None:
            okved_strict_value = None
        else:
            okved_strict_value = bool(okved_strict_raw)

        okved_codes = filter_codes_in_handbook(_as_list(data.get("okved")))

        filters = SearchFilters(
            query=data.get("query") or None,
            region=_as_list(data.get("region")),
            okved=okved_codes,
            okved_strict=okved_strict_value,
            okopf=_as_list(data.get("okopf")),
            msp=_as_list(data.get("msp")),
            status=_as_list(data.get("status")) or ["1"],
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

        # Сохраняем «сырые» фильтры для генерации темы — их хеш должен
        # совпадать у одинаковых запусков (Mini App шлёт detrministic).
        raw_filters = {
            "query": data.get("query") or None,
            "region": _as_list(data.get("region")),
            "okved": okved_codes,
            "okved_strict": okved_strict_value,
            "okopf": _as_list(data.get("okopf")),
            "msp": _as_list(data.get("msp")),
            "status": _as_list(data.get("status")) or ["1"],
            "finance_revenue_from": _parse_int(data.get("finance_revenue_from")),
            "finance_revenue_to": _parse_int(data.get("finance_revenue_to")),
            "finance_profit_from": _parse_int(data.get("finance_profit_from")),
            "finance_profit_to": _parse_int(data.get("finance_profit_to")),
            "capital_from": _parse_int(data.get("capital_from")),
            "capital_to": _parse_int(data.get("capital_to")),
            "sshr_from": _parse_int(data.get("sshr_from")),
            "sshr_to": _parse_int(data.get("sshr_to")),
            "has_phones": bool(data.get("has_phones")),
            "has_emails": bool(data.get("has_emails")),
            "has_sites": bool(data.get("has_sites")),
            "finance_has_actual_year_data": bool(data.get("finance_has_actual_year_data")),
            "not_defendant": bool(data.get("not_defendant")),
        }

        await message.answer(
            f"Запускаю парсинг (лимит — {max_new} новых компаний)..."
        )
        task = asyncio.create_task(
            _run_rusprofile(message, filters, raw_filters, max_new)
        )
        _active_tasks[user_id] = task

    except json.JSONDecodeError:
        await message.answer("Ошибка: неверный формат данных из Mini App")
    except Exception as e:
        logger.error("Ошибка обработки данных Mini App: %s", e)
        await message.answer(f"Произошла ошибка: {e}")


async def _run_rusprofile(
    message: Message,
    filters: SearchFilters,
    filters_for_theme: dict,
    max_new: int,
):
    """Запускает Rusprofile-парсинг через сервис."""
    status_msg = await message.answer("Подключаюсь к Rusprofile...")

    async def progress(text: str):
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    try:
        result = await run_rusprofile(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            filters=filters,
            filters_for_theme=filters_for_theme,
            max_new=max_new,
            progress_callback=progress,
        )

        if result.status == "error":
            await status_msg.edit_text(
                f"Ошибка парсинга: {result.error_message}"
            )
            return

        if result.total_new == 0:
            await status_msg.edit_text(
                "Новых компаний не найдено — все совпадения уже были в базе.\n"
                f"Пропущено дубликатов: {result.total_skipped}\n\n"
                "Откройте «История» в Mini App, чтобы заново выгрузить "
                "ранее найденные компании."
            )
            return

        await status_msg.edit_text(
            f"Готово!\n"
            f"• Новых компаний: {result.total_new}\n"
            f"• Пропущено дубликатов: {result.total_skipped}\n\n"
            f"Таблица: {result.sheet_url}"
        )

    except asyncio.CancelledError:
        await status_msg.edit_text("Парсинг отменён.")
    except Exception as e:
        logger.exception("Ошибка _run_rusprofile")
        await status_msg.edit_text(f"Ошибка парсинга: {e}")
    finally:
        user_id = message.from_user.id
        _active_tasks.pop(user_id, None)


async def _run_yandex(
    message: Message,
    region: str,
    category: str,
    max_new: int,
):
    """Запускает Яндекс.Карты-парсинг через сервис."""
    status_msg = await message.answer("Подключаюсь к Яндекс Картам...")

    async def progress(text: str):
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    try:
        result = await run_yandex(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            region=region,
            category=category,
            max_new=max_new,
            progress_callback=progress,
        )

        if result.status == "error":
            await status_msg.edit_text(f"Ошибка: {result.error_message}")
            return

        if result.total_new == 0:
            await status_msg.edit_text(
                "Новых организаций не найдено — все уже были в базе.\n"
                f"Пропущено дубликатов: {result.total_skipped}"
            )
            return

        await status_msg.edit_text(
            f"Готово! Яндекс.Карты:\n"
            f"• Новых: {result.total_new}\n"
            f"• Пропущено дубликатов: {result.total_skipped}\n\n"
            f"Лист: {result.sheet_url}"
        )

    except asyncio.CancelledError:
        await status_msg.edit_text("Парсинг отменён.")
    except Exception as e:
        logger.exception("Ошибка _run_yandex")
        await status_msg.edit_text(f"Ошибка: {e}")
    finally:
        user_id = message.from_user.id
        _active_tasks.pop(user_id, None)


def _clamp_max_new(value: int | None) -> int:
    if not value or value <= 0:
        return DEFAULT_MAX_NEW
    return min(int(value), MAX_NEW_HARD_LIMIT)


def _parse_int(value) -> int | None:
    """Безопасное преобразование в int."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _as_list(value) -> list[str]:
    """Нормализует значение от Mini App в список строк."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)]
