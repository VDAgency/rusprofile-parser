"""Экспорт компаний запуска в .xlsx (openpyxl).

Формат повторяет колонки соответствующих листов Sheets, плюс
два дополнительных столбца:
* «Дата первого нахождения»
* «Кол-во запусков, в которых была»
"""

from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.config import SHEET_HEADERS, YANDEX_SHEET_HEADERS
from src.db import (
    Company,
    ParseRun,
    RunCompany,
    Source,
    format_phone_for_display,
)


def _header_style(ws, n_cols: int) -> None:
    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor="E1E5F0")
    center = Alignment(horizontal="center", vertical="center")
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = bold
        cell.fill = fill
        cell.alignment = center
    ws.row_dimensions[1].height = 22


def _autosize(ws) -> None:
    """Грубая авто-ширина колонок: max длина данных, ограниченная сверху."""
    for col_cells in ws.columns:
        max_len = 0
        col_letter = col_cells[0].column_letter
        for cell in col_cells:
            v = cell.value
            if v is None:
                continue
            length = len(str(v))
            if length > max_len:
                max_len = length
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 60)


def _runs_count_per_company(session: Session, company_ids: list[int]) -> dict[int, int]:
    if not company_ids:
        return {}
    rows = session.execute(
        select(RunCompany.company_id, func.count(RunCompany.run_id))
        .where(RunCompany.company_id.in_(company_ids))
        .group_by(RunCompany.company_id)
    ).all()
    return {row[0]: row[1] for row in rows}


def export_run_to_xlsx(session: Session, run_id: int) -> tuple[bytes, str]:
    """Возвращает (xlsx_bytes, filename)."""
    run = session.get(ParseRun, run_id)
    if not run:
        raise ValueError(f"ParseRun id={run_id} не найден")

    rows = session.execute(
        select(Company)
        .join(RunCompany, RunCompany.company_id == Company.id)
        .where(RunCompany.run_id == run_id)
        .order_by(Company.id)
    ).scalars().all()

    runs_count = _runs_count_per_company(session, [c.id for c in rows])

    if run.source == Source.YANDEX_MAPS.value:
        headers = YANDEX_SHEET_HEADERS + [
            "Дата первого нахождения", "Запусков, в которых была",
        ]
    else:
        headers = SHEET_HEADERS + [
            "Дата первого нахождения", "Запусков, в которых была",
        ]

    wb = Workbook()
    ws = wb.active
    ws.title = "Результаты"
    ws.append(headers)
    _header_style(ws, len(headers))

    for c in rows:
        first_seen = c.first_seen_at.strftime("%d.%m.%Y") if c.first_seen_at else ""
        n_runs = runs_count.get(c.id, 0)
        phone_disp = format_phone_for_display(c.phone) if c.phone else ""

        if run.source == Source.YANDEX_MAPS.value:
            raw = c.raw_json or {}
            last_seen = c.last_seen_at.strftime("%d.%m.%Y") if c.last_seen_at else ""
            ws.append([
                c.name,
                c.okved or "",       # «Рубрики»
                c.region or "",
                c.address or "",
                phone_disp,
                c.site or "",
                str(raw.get("rating") or ""),
                str(raw.get("reviews_count") or ""),
                raw.get("hours") or "",
                raw.get("coordinates") or "",
                raw.get("yandex_url") or "",
                last_seen,           # «Дата парсинга» — последний апдейт
                first_seen,          # «Дата первого нахождения»
                n_runs,
            ][:len(headers)])
        else:
            ws.append([
                c.name,
                c.inn or "",
                c.ogrn or "",
                c.region or "",
                c.address or "",
                c.okved or "",
                c.revenue or "",
                c.profit or "",
                phone_disp,
                c.email or "",
                c.site or "",
                c.status or "",
                "",   # Статус ИИ
                "",   # Комментарий ИИ
                c.first_seen_at.strftime("%d.%m.%Y") if c.first_seen_at else "",
                first_seen,
                n_runs,
            ][:len(headers)])

    _autosize(ws)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    date = (run.finished_at or run.started_at or datetime.utcnow()).strftime("%Y-%m-%d")
    filename = f"run_{run_id}_{run.source}_{date}.xlsx"
    return buf.getvalue(), filename
