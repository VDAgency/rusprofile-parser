"""Интеграция с Google Sheets — запись результатов парсинга."""

import logging
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from src.config import GOOGLE_SHEETS_CREDENTIALS_FILE, GOOGLE_SHEET_ID, SHEET_HEADERS, BASE_DIR

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_client() -> gspread.Client:
    """Создаёт авторизованный клиент gspread."""
    creds_path = Path(GOOGLE_SHEETS_CREDENTIALS_FILE)
    if not creds_path.is_absolute():
        creds_path = BASE_DIR / creds_path
    creds = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
    return gspread.authorize(creds)


def _col_letter(n: int) -> str:
    """Преобразует номер колонки (1-based) в букву A1-нотации: 1→A, 27→AA."""
    result = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def _get_or_create_worksheet(
    spreadsheet: gspread.Spreadsheet,
    title: str,
    headers: list[str],
):
    """Получает или создаёт лист с заданными заголовками.

    Заголовки — параметр функции: разные листы могут иметь свою структуру
    (например, «Результаты» — 15 колонок Rusprofile, «Яндекс Карты» — 12 колонок).
    """
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))

    last_col = _col_letter(len(headers))

    # Проверяем заголовки и выставляем при расхождении
    first_row = ws.row_values(1)
    if first_row != headers:
        ws.update("A1", [headers])
        ws.format(f"A1:{last_col}1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.95},
        })
        logger.info("Заголовки листа '%s' обновлены", title)

    return ws


def write_companies(
    companies: list,
    sheet_name: str = "Результаты",
    headers: list[str] | None = None,
) -> str:
    """Записывает список объектов в Google Sheets.

    Args:
        companies: список объектов с методом ``.to_row() -> list[str]``.
        sheet_name: название листа.
        headers: заголовки столбцов; по умолчанию — ``SHEET_HEADERS``
            (формат Rusprofile). Для листа «Яндекс Карты» передаётся
            ``YANDEX_SHEET_HEADERS``.

    Returns:
        URL таблицы.
    """
    if headers is None:
        headers = SHEET_HEADERS

    client = _get_client()
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    ws = _get_or_create_worksheet(spreadsheet, sheet_name, headers)

    rows = [c.to_row() for c in companies]

    if not rows:
        logger.warning("Нет данных для записи")
        return spreadsheet.url

    # Считаем следующую свободную строку по существующим данным,
    # и принудительно расширяем сетку — ws.update() не растит лист сам,
    # а новый лист после clear_sheet может иметь row_count=1.
    existing = ws.get_all_values()
    start_row = len(existing) + 1
    end_row = start_row + len(rows) - 1

    if ws.row_count < end_row:
        ws.add_rows(end_row - ws.row_count)

    last_col = _col_letter(len(headers))
    cell_range = f"A{start_row}:{last_col}{end_row}"
    ws.update(cell_range, rows, value_input_option="USER_ENTERED")

    logger.info("Записано %d строк в лист '%s' (строки %d–%d)",
                len(rows), sheet_name, start_row, end_row)

    return spreadsheet.url


def clear_sheet(sheet_name: str = "Результаты") -> None:
    """Очищает данные на листе (кроме заголовков)."""
    client = _get_client()
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    try:
        ws = spreadsheet.worksheet(sheet_name)
        row_count = ws.row_count
        if row_count > 1:
            ws.delete_rows(2, row_count)
        logger.info("Лист '%s' очищен", sheet_name)
    except gspread.WorksheetNotFound:
        logger.warning("Лист '%s' не найден", sheet_name)


def get_sheet_url() -> str:
    """Возвращает URL таблицы."""
    return f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}"
