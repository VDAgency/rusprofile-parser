"""Парсер компаний с Rusprofile — извлечение данных из HTML."""

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page

from src.config import (
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
    MAX_PAGES,
    RUSPROFILE_LOGIN,
)
from src.rusprofile.auth import _goto
from src.rusprofile.filters import SearchFilters, build_search_url

logger = logging.getLogger(__name__)


@dataclass
class Company:
    """Данные о компании."""

    name: str = ""
    inn: str = ""
    ogrn: str = ""
    region: str = ""
    address: str = ""
    okved: str = ""
    revenue: str = ""
    profit: str = ""
    phone: str = ""
    email: str = ""
    site: str = ""
    status: str = ""
    ai_status: str = ""
    ai_comment: str = ""
    parse_date: str = ""
    # Относительная ссылка на детальную страницу (/id/NNN); не выгружается
    # в Google Sheets, используется только для enrich_company_details.
    detail_href: str = ""

    def to_row(self) -> list[str]:
        """Конвертирует в строку для Google Sheets."""
        return [
            self.name,
            self.inn,
            self.ogrn,
            self.region,
            self.address,
            self.okved,
            self.revenue,
            self.profit,
            self.phone,
            self.email,
            self.site,
            self.status,
            self.ai_status,
            self.ai_comment,
            self.parse_date,
        ]


_DIGITS = str.maketrans("", "", " \u00a0\t")


def _extract_number(text: str) -> str:
    """Возвращает только цифры из строки."""
    return "".join(c for c in text if c.isdigit())


def _parse_company_card(card) -> Optional[tuple["Company", str]]:
    """Извлекает данные компании из одной карточки в поисковой выдаче.

    Разметка Rusprofile (актуальна на 2026-04):

        <div class="list-element">
          <a href="/id/123456" class="list-element__title">ООО "РОМАШКА"</a>
          <div class="list-element__text warning">...признак недостоверности...</div>
          <span class="list-element__text">ОКВЭД / описание</span>
          <span class="list-element__text">доп. текст</span>
          <div class="list-element__address">адрес</div>
          <div class="list-element__row-info">
            <span>ИНН: ...</span>
            <span>ОГРН: ...</span>
            <span>Дата регистрации: ...</span>
          </div>
        </div>
    """
    try:
        company = Company(parse_date=datetime.now().strftime("%d.%m.%Y"))

        title = card.select_one("a.list-element__title")
        if not title:
            return None
        company.name = title.get_text(strip=True).replace("\xa0", " ")
        href = (title.get("href") or "").strip()

        addr_el = card.select_one(".list-element__address")
        if addr_el:
            company.address = addr_el.get_text(strip=True)
            parts = [p.strip() for p in company.address.split(",") if p.strip()]
            # Регион Rusprofile обычно указывает как «Ставропольский край» вторым
            # элементом (первый — индекс). Берём второй, если он явно не индекс.
            if len(parts) >= 2 and not parts[0].isdigit():
                company.region = parts[0]
            elif len(parts) >= 2:
                company.region = parts[1]

        info = card.select(".list-element__row-info span")
        for span in info:
            text = span.get_text(strip=True)
            low = text.lower()
            if "инн" in low:
                company.inn = _extract_number(text)
            elif "огрн" in low:
                company.ogrn = _extract_number(text)

        # Текстовые блоки (без .warning — там статусы/предупреждения)
        texts = [
            el.get_text(strip=True)
            for el in card.select(".list-element__text")
            if "warning" not in (el.get("class") or [])
        ]
        texts = [t for t in texts if t]
        if texts:
            # Обычно первый блок — ОКВЭД/описание деятельности, второй — доп. инфо
            company.okved = texts[0]

        warning_el = card.select_one(".list-element__text.warning")
        if warning_el:
            company.status = warning_el.get_text(strip=True)
        else:
            company.status = "Действующая"

        if not company.name:
            return None
        return company, href

    except Exception as e:
        logger.warning("Ошибка парсинга карточки: %s", e)
        return None


def _parse_company_page(html: str) -> Optional[dict]:
    """Извлекает детальные данные со страницы компании.

    Источники на странице:
    - ``a[href^='tel:']`` — телефон (на странице может быть несколько:
      основной + из публичного справочника). Берём первый.
    - ``a[href^='mailto:']`` — email. Rusprofile дополнительно вставляет
      в шапку адрес ТЕКУЩЕГО авторизованного пользователя, поэтому
      исключаем значение ``RUSPROFILE_LOGIN``.
    - Блок финансов содержит текст «Выручка N руб.», «Прибыль N руб.» —
      разбираем регулярками.
    - Сайт компании — ссылка рядом с «Сайт:» / «Интернет-сайт:».
    """
    soup = BeautifulSoup(html, "lxml")
    data = {}

    tel_links = soup.select("a[href^='tel:']")
    if tel_links:
        phone_text = tel_links[0].get_text(strip=True) or tel_links[0]["href"][4:]
        data["phone"] = phone_text.strip()

    own_email = (RUSPROFILE_LOGIN or "").strip().lower()
    for a in soup.select("a[href^='mailto:']"):
        value = a["href"].replace("mailto:", "").strip()
        if value and value.lower() != own_email:
            data["email"] = value
            break

    # Сайт: пробуем явный блок «Сайт:» с внешней ссылкой
    for label in soup.find_all(string=lambda s: isinstance(s, str) and "сайт" in s.lower()):
        parent = label.parent
        if parent is None:
            continue
        for a in parent.find_all_next("a", limit=3):
            href = (a.get("href") or "").strip()
            if href.startswith(("http://", "https://")) and "rusprofile.ru" not in href:
                data["site"] = href
                break
        if "site" in data:
            break

    # Финансы — блок .finance-columns содержит строку вида:
    # «Выручка 4,5 млрд руб. ↑ +47 % Прибыль 12 млн руб. ↑ +23 % Стоимость ...»
    fin = soup.select_one(".finance-columns, .finance-tile-columns")
    if fin:
        fin_text = fin.get_text(" ", strip=True)
        m = re.search(
            r"Выручка\s+(.+?)(?=\s+(?:Прибыль|Стоимость|Убыток)|$)",
            fin_text,
        )
        if m:
            data["revenue"] = m.group(1).strip()
        m = re.search(
            r"Прибыль\s+(.+?)(?=\s+(?:Стоимость|Выручка|Убыток)|$)",
            fin_text,
        )
        if m:
            data["profit"] = m.group(1).strip()

    return data


async def _get_page_html(page: Page, url: str) -> str:
    """Загружает страницу и возвращает HTML.

    Использует устойчивый ``_goto`` из ``auth.py``: warmup через
    ``about:blank``, ``wait_until='commit'`` и ретраи при таймауте.
    """
    await _goto(page, url, timeout=45000)
    await page.wait_for_timeout(2000)
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        pass
    await page.wait_for_timeout(1000)
    return await page.content()


async def parse_search_results(
    context: BrowserContext,
    filters: SearchFilters,
    progress_callback=None,
) -> list[Company]:
    """Парсит результаты поиска компаний с пагинацией.

    Args:
        context: авторизованный контекст браузера
        filters: фильтры поиска
        progress_callback: async-функция для отправки прогресса (found, processed)

    Returns:
        список компаний
    """
    companies = []
    seen_inn: set[str] = set()
    page = await context.new_page()

    try:
        current_page = 1
        total_found = 0

        while current_page <= MAX_PAGES:
            filters.page = current_page
            url = build_search_url(filters)
            logger.info("Парсим страницу %d: %s", current_page, url)

            html = await _get_page_html(page, url)
            soup = BeautifulSoup(html, "lxml")

            cards = soup.select(".list-element")

            if not cards:
                logger.info("Больше результатов нет (страница %d)", current_page)
                break

            # Общее количество результатов — в заголовке
            # «найдено 1135 юридических лиц, 16 ... и 21 ...»
            if current_page == 1:
                header = soup.select_one(".search-result h1, h1")
                if header:
                    header_text = header.get_text(" ", strip=True)
                    nums = [int(n) for n in re.findall(r"\d+", header_text)]
                    if nums:
                        total_found = sum(nums)
                        logger.info("Всего найдено: %d компаний (из заголовка)", total_found)

            new_on_page = 0
            for card in cards:
                parsed = _parse_company_card(card)
                if not parsed:
                    continue
                company, href = parsed
                # Убираем дубликаты (редко, но бывают «похожие» карточки)
                if company.inn and company.inn in seen_inn:
                    continue
                if company.inn:
                    seen_inn.add(company.inn)
                company.detail_href = href
                companies.append(company)
                new_on_page += 1

            if progress_callback:
                await progress_callback(
                    total_found or len(companies), len(companies)
                )

            logger.info(
                "Страница %d: карточек %d, новых %d, всего собрано %d",
                current_page, len(cards), new_on_page, len(companies),
            )

            # Если на странице не появилось ни одной новой карточки —
            # выдача кончилась или зациклилась, выходим.
            if new_on_page == 0:
                logger.info("На странице %d нет новых карточек — стоп", current_page)
                break

            # Задержка между запросами
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            await asyncio.sleep(delay)

            current_page += 1

    except Exception as e:
        logger.error("Ошибка парсинга: %s", e)
    finally:
        await page.close()

    return companies


async def enrich_company_details(
    context: BrowserContext,
    companies: list[Company],
    progress_callback=None,
) -> list[Company]:
    """Дополняет данные компаний деталями с их страниц (телефон, email, сайт).

    Использует ссылку, уже сохранённую в карточке поисковой выдачи
    (``company._detail_href``). Если её нет — пропускает.
    """
    page = await context.new_page()

    try:
        for i, company in enumerate(companies):
            href = company.detail_href
            if not href:
                continue

            url = href if href.startswith("http") else f"https://www.rusprofile.ru{href}"

            try:
                html = await _get_page_html(page, url)
                details = _parse_company_page(html)

                if details:
                    if details.get("phone") and not company.phone:
                        company.phone = details["phone"]
                    if details.get("email") and not company.email:
                        company.email = details["email"]
                    if details.get("site") and not company.site:
                        company.site = details["site"]
                    if details.get("revenue") and not company.revenue:
                        company.revenue = details["revenue"]
                    if details.get("profit") and not company.profit:
                        company.profit = details["profit"]

                if progress_callback:
                    await progress_callback(len(companies), i + 1)

                delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
                await asyncio.sleep(delay)

            except Exception as e:
                logger.warning("Ошибка обогащения %s: %s", company.name, e)
                continue

    finally:
        await page.close()

    return companies
