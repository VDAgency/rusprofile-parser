"""Экспорт компаний запуска в .xlsx (openpyxl).

Формат повторяет колонки соответствующих листов Sheets, плюс
дополнительные столбцы:
* «Дата первого нахождения»
* «Кол-во запусков, в которых была»
* (Этап 2 v3) Я.Карты-сигналы, кросс-обогащение, ИИ-блок.

Сортировка — `ai_status='hot' first → ai_score desc`. Подсветка строк:
hot → зелёная, quota_exceeded → жёлтая, skip → серая.
"""

from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.config import SHEET_HEADERS, YANDEX_SHEET_HEADERS
from src.db import (
    Company,
    ParseRun,
    RunCompany,
    Source,
    format_phone_for_display,
)
from src.db.models import AIStatus

# Дополнительные колонки Этапа 2 v3, добавляются в КОНЕЦ.
QUALIFY_HEADERS = [
    "ИИ-балл",
    "ИИ-статус",
    "ИИ-комментарий",
    "ИИ-сигналы",
    "Зацепка для звонка",
    "✓ позитивные ключи",
    "✗ негативные ключи",
    "Я.Карты рейтинг",
    "Я.Карты отзывов",
    "Я.Карты посл. отзыв",
    "Я.Карты статус",
    "Я.Карты ссылка",
    "Кросс-источник",
    "Достоверность матча, %",
    "Дата квалификации",
]

# Подсветка по статусу
_FILL_HOT = PatternFill("solid", fgColor="C8E6C9")          # светло-зелёный
_FILL_COLD = PatternFill("solid", fgColor="ECEFF1")         # светло-серый
_FILL_SKIP = PatternFill("solid", fgColor="EEEEEE")         # серый
_FILL_QUOTA = PatternFill("solid", fgColor="FFF9C4")        # светло-жёлтый
_FILL_UNKNOWN = PatternFill("solid", fgColor="FFFFFF")      # белый


def _status_fill(ai_status: str | None) -> PatternFill | None:
    if ai_status == AIStatus.HOT.value:
        return _FILL_HOT
    if ai_status == AIStatus.COLD.value:
        return _FILL_COLD
    if ai_status == AIStatus.SKIP.value:
        return _FILL_SKIP
    if ai_status == AIStatus.QUOTA_EXCEEDED.value:
        return _FILL_QUOTA
    return None


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

    # Фильтруем только новые для этого запуска: дубликаты, попавшие
    # в run_companies с is_new=False (компания уже была в БД от
    # другого запуска), не должны идти в Excel.
    # Сортировка: hot first → ai_score desc → company.id (стабильно).
    hot_first = case(
        (Company.ai_status == AIStatus.HOT.value, 0),
        (Company.ai_status == AIStatus.COLD.value, 2),
        (Company.ai_status == AIStatus.SKIP.value, 3),
        (Company.ai_status == AIStatus.QUOTA_EXCEEDED.value, 4),
        else_=1,
    )
    rows = session.execute(
        select(Company)
        .join(RunCompany, RunCompany.company_id == Company.id)
        .where(RunCompany.run_id == run_id, RunCompany.is_new == True)  # noqa: E712
        .order_by(hot_first, Company.ai_score.desc().nullslast(), Company.id)
    ).scalars().all()

    runs_count = _runs_count_per_company(session, [c.id for c in rows])

    if run.source == Source.YANDEX_MAPS.value:
        headers = YANDEX_SHEET_HEADERS + [
            "Дата первого нахождения", "Запусков, в которых была",
        ] + QUALIFY_HEADERS
    else:
        headers = SHEET_HEADERS + [
            "Дата первого нахождения", "Запусков, в которых была",
        ] + QUALIFY_HEADERS

    wb = Workbook()
    ws = wb.active
    ws.title = "Результаты"
    ws.append(headers)
    _header_style(ws, len(headers))

    for c in rows:
        first_seen = c.first_seen_at.strftime("%d.%m.%Y") if c.first_seen_at else ""
        n_runs = runs_count.get(c.id, 0)
        phone_disp = format_phone_for_display(c.phone) if c.phone else ""

        # Хвост — общий для обоих источников: блок ИИ-квалификации.
        ai_qualified = (
            c.ai_qualified_at.strftime("%d.%m.%Y %H:%M") if c.ai_qualified_at else ""
        )
        yandex_review_str = (
            c.yandex_last_review_date.strftime("%d.%m.%Y")
            if c.yandex_last_review_date else ""
        )
        cross_pct = (
            f"{round((c.cross_match_confidence or 0) * 100)}"
            if c.cross_match_confidence is not None else ""
        )
        qualify_tail = [
            c.ai_score if c.ai_score is not None else "",
            c.ai_status or "",
            c.ai_comment or "",
            ", ".join(c.ai_signals) if c.ai_signals else "",
            c.ai_hook or "",
            ", ".join(c.ai_keyword_matches_positive) if c.ai_keyword_matches_positive else "",
            ", ".join(c.ai_keyword_matches_negative) if c.ai_keyword_matches_negative else "",
            c.yandex_rating if c.yandex_rating is not None else "",
            c.yandex_reviews_count if c.yandex_reviews_count is not None else "",
            yandex_review_str,
            c.yandex_operating_status or "",
            c.yandex_url or "",
            c.cross_enrichment_source or "",
            cross_pct,
            ai_qualified,
        ]

        if run.source == Source.YANDEX_MAPS.value:
            raw = c.raw_json or {}
            last_seen = c.last_seen_at.strftime("%d.%m.%Y") if c.last_seen_at else ""
            row = [
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
            ] + qualify_tail
        else:
            row = [
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
                c.ai_status or "",                  # старая колонка «Статус ИИ»
                c.ai_comment or "",                 # старая колонка «Комментарий ИИ»
                c.first_seen_at.strftime("%d.%m.%Y") if c.first_seen_at else "",
                first_seen,
                n_runs,
            ] + qualify_tail
        ws.append(row[:len(headers)])

        # Подсветка строки по статусу.
        fill = _status_fill(c.ai_status)
        if fill is not None:
            row_idx = ws.max_row
            for col in range(1, len(headers) + 1):
                ws.cell(row=row_idx, column=col).fill = fill

    _autosize(ws)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    date = (run.finished_at or run.started_at or datetime.utcnow()).strftime("%Y-%m-%d")
    filename = f"run_{run_id}_{run.source}_{date}.xlsx"
    return buf.getvalue(), filename
