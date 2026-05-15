"""Парсинг даты последнего отзыва на карточке Я.Карт + поддержка форматов дат.

Без авторизации Я.Карты часто отдают только относительные даты («3 месяца
назад»). С авторизацией бывают точные даты («23 апреля 2024 г.»). Поддерживаем
оба варианта.

Функции:
- ``parse_relative_date(text, today)`` — синхронная, без I/O. Тестируется
  изолированно.
- ``get_last_review_date(yandex_url, page)`` — открывает карточку и
  вкладку «Отзывы», парсит дату самого свежего отзыва.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Парсинг текстовых форматов дат
# ---------------------------------------------------------------------------

# Словарь месяцев. Порядок важен: длинные префиксы ПЕРЕД короткими, чтобы
# «март/марта» не путалось с короткой формой «ма» (для «мая»).
_MONTHS: tuple[tuple[str, int], ...] = (
    ("январ", 1),
    ("феврал", 2),
    ("март", 3),
    ("апрел", 4),
    ("май", 5),     # «май», «мае»
    ("мая", 5),     # «мая»
    ("июн", 6),
    ("июл", 7),
    ("август", 8),
    ("сентябр", 9),
    ("октябр", 10),
    ("ноябр", 11),
    ("декабр", 12),
)


def _normalize(text: str) -> str:
    return text.strip().lower().replace("ё", "е")


def parse_relative_date(text: str | None, today: date | None = None) -> date | None:
    """Превращает строку с датой в `date`.

    Поддерживаемые форматы:
    * "сегодня" / "сегодня в 14:32"
    * "вчера" / "вчера в 18:00"
    * "N дней назад" / "день назад"
    * "N недель назад" / "неделю назад"
    * "N месяцев назад" / "месяц назад"
    * "N лет назад" / "год назад"
    * "23 апреля" — текущий или предыдущий год (если месяц > текущего → пред.)
    * "23 апреля 2024 г." / "23.04.2024" / "23/04/2024" / "2024-04-23"

    Возвращает `None`, если разобрать не удалось.
    """
    if not text:
        return None
    today = today or date.today()
    s = _normalize(text)

    # ─── ISO-формат YYYY-MM-DD ────────────────────────────────────────────
    # Без trailing \b: после "23" может идти "T" (ISO-datetime), и \b
    # не сработает на стыке цифры и буквы — оба word-char.
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    # ─── DD.MM.YYYY или DD/MM/YYYY ────────────────────────────────────────
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    # ─── «сегодня» / «вчера» ──────────────────────────────────────────────
    # Порядок важен: «позавчера» содержит подстроку «вчера», проверяем
    # длинное слово первым.
    if "позавчера" in s:
        return today - timedelta(days=2)
    if "сегодня" in s:
        return today
    if "вчера" in s:
        return today - timedelta(days=1)

    # ─── «N дней/недель/месяцев/лет назад» ───────────────────────────────
    m = re.search(
        r"(?:(\d+)\s+)?(день|дня|дней|неделю|недели|недель|месяц|месяца|месяцев|год|года|лет)\s+назад",
        s,
    )
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        unit = m.group(2)
        # Сравнение по полным словам — `startswith("ден")` не ловит «дней»
        # (д-н-е-й, а не д-е-н-…).
        if unit in ("день", "дня", "дней"):
            return today - timedelta(days=n)
        if unit in ("неделю", "недели", "недель"):
            return today - timedelta(weeks=n)
        if unit in ("месяц", "месяца", "месяцев"):
            return today - timedelta(days=30 * n)
        if unit in ("год", "года", "лет"):
            try:
                return today.replace(year=today.year - n)
            except ValueError:
                # 29 февраля високосного года → fallback на 28 фев.
                return today - timedelta(days=365 * n)

    # ─── «23 апреля» / «23 апреля 2024 г.» ───────────────────────────────
    m = re.search(
        r"\b(\d{1,2})\s+([а-я]+)(?:\s+(\d{4}))?\b",
        s,
    )
    if m:
        d_num = int(m.group(1))
        month_word = m.group(2)
        year = int(m.group(3)) if m.group(3) else None

        month_num = None
        for prefix, num in _MONTHS:
            if month_word.startswith(prefix):
                month_num = num
                break
        if month_num is None:
            return None

        if year is None:
            year = today.year
            try:
                cand = date(year, month_num, d_num)
            except ValueError:
                return None
            # Если дата в будущем (>сегодня + 7 дней) — берём прошлый год.
            if cand > today + timedelta(days=7):
                year -= 1
        try:
            return date(year, month_num, d_num)
        except ValueError:
            return None

    return None


# ---------------------------------------------------------------------------
# Парсинг даты последнего отзыва из карточки Я.Карт через Playwright
# ---------------------------------------------------------------------------

# Селекторы для вкладки «Отзывы» и для самого верхнего отзыва.
_REVIEWS_TAB_SELECTORS = [
    'a[href*="/reviews"]',
    'a[role="tab"][aria-controls*="reviews"]',
    'div[role="tab"]:has-text("Отзывы")',
]

_REVIEW_DATE_SELECTORS = [
    '[class*="business-review-view__date"]',
    '[class*="review-card-view__date"]',
    'meta[itemprop="datePublished"]',
]


async def get_last_review_date(
    yandex_url: str,
    page: "Page",
    timeout_ms: int = 15000,
) -> date | None:
    """Открывает карточку, переходит на «Отзывы», парсит самую свежую дату.

    Возвращает None если:
    * карточка не открылась;
    * вкладка «Отзывы» не найдена;
    * у компании 0 отзывов;
    * дата не распарсилась.

    НИКОГДА не роняет вызывающий код — все ошибки логируются warning.
    """
    if not yandex_url:
        return None

    try:
        await page.goto(yandex_url, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(1500)

        # Кликаем на вкладку «Отзывы» (если ещё не там).
        if "/reviews" not in page.url:
            for sel in _REVIEWS_TAB_SELECTORS:
                tab = await page.query_selector(sel)
                if tab:
                    try:
                        await tab.click()
                        await page.wait_for_timeout(2000)
                        break
                    except Exception:  # noqa: BLE001
                        continue

        # 1) Сначала пытаемся вытащить из meta[itemprop="datePublished"]
        meta = await page.query_selector('meta[itemprop="datePublished"]')
        if meta:
            content = await meta.get_attribute("content")
            if content:
                d = parse_relative_date(content)
                if d:
                    return d

        # 2) Иначе — текстовая дата самого первого отзыва
        for sel in _REVIEW_DATE_SELECTORS:
            el = await page.query_selector(sel)
            if not el:
                continue
            txt = (await el.inner_text()).strip()
            if not txt:
                continue
            d = parse_relative_date(txt)
            if d:
                return d

        return None

    except Exception as e:  # noqa: BLE001
        logger.warning("get_last_review_date(%s) failed: %s", yandex_url, e)
        return None
