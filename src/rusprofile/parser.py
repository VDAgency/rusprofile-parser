"""Парсер компаний с Rusprofile — извлечение данных из HTML."""

import asyncio
import logging
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page

from src.config import REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, MAX_PAGES
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


def _parse_company_card(card) -> Optional[Company]:
    """Извлекает данные компании из карточки в поисковой выдаче."""
    try:
        company = Company(parse_date=datetime.now().strftime("%d.%m.%Y"))

        # Название компании
        name_el = card.select_one(
            ".company-name, .company-item__title a, "
            "[class*='companyName'], h4 a, .search-result__title a"
        )
        if name_el:
            company.name = name_el.get_text(strip=True)

        # ИНН
        inn_el = card.select_one("[class*='inn'], .company-item__info")
        if inn_el:
            text = inn_el.get_text(strip=True)
            # Извлекаем ИНН из текста
            if "ИНН" in text:
                parts = text.split("ИНН")
                if len(parts) > 1:
                    inn_value = "".join(c for c in parts[1][:15] if c.isdigit())
                    company.inn = inn_value
            elif text.isdigit():
                company.inn = text

        # ОГРН
        ogrn_el = card.select_one("[class*='ogrn']")
        if ogrn_el:
            text = ogrn_el.get_text(strip=True)
            if "ОГРН" in text:
                parts = text.split("ОГРН")
                if len(parts) > 1:
                    ogrn_value = "".join(c for c in parts[1][:18] if c.isdigit())
                    company.ogrn = ogrn_value
            elif text.isdigit():
                company.ogrn = text

        # Адрес и регион
        addr_el = card.select_one(
            "[class*='address'], .company-item__text, [class*='region']"
        )
        if addr_el:
            company.address = addr_el.get_text(strip=True)
            # Регион — первая часть адреса
            addr_parts = company.address.split(",")
            if addr_parts:
                company.region = addr_parts[0].strip()

        # ОКВЭД
        okved_el = card.select_one("[class*='okved'], [class*='activity']")
        if okved_el:
            company.okved = okved_el.get_text(strip=True)

        # Выручка
        revenue_el = card.select_one("[class*='revenue'], [class*='finance']")
        if revenue_el:
            company.revenue = revenue_el.get_text(strip=True)

        # Статус компании
        status_el = card.select_one("[class*='status']")
        if status_el:
            company.status = status_el.get_text(strip=True)
        else:
            company.status = "Действующая"

        if not company.name:
            return None

        return company

    except Exception as e:
        logger.warning("Ошибка парсинга карточки: %s", e)
        return None


def _parse_company_page(html: str) -> Optional[dict]:
    """Извлекает детальные данные со страницы компании."""
    soup = BeautifulSoup(html, "lxml")
    data = {}

    # Телефон
    phone_el = soup.select_one(
        "[class*='phone'], [href^='tel:'], .company-info__phone"
    )
    if phone_el:
        href = phone_el.get("href", "")
        if href.startswith("tel:"):
            data["phone"] = href.replace("tel:", "").strip()
        else:
            data["phone"] = phone_el.get_text(strip=True)

    # Email
    email_el = soup.select_one(
        "[class*='email'], [href^='mailto:'], .company-info__email"
    )
    if email_el:
        href = email_el.get("href", "")
        if href.startswith("mailto:"):
            data["email"] = href.replace("mailto:", "").strip()
        else:
            data["email"] = email_el.get_text(strip=True)

    # Сайт
    site_el = soup.select_one(
        "[class*='website'] a, [class*='site'] a, .company-info__site a"
    )
    if site_el:
        data["site"] = site_el.get_text(strip=True)

    # ОГРН (если не было в карточке)
    ogrn_el = soup.select_one("[class*='ogrn']")
    if ogrn_el:
        text = ogrn_el.get_text(strip=True)
        ogrn_value = "".join(c for c in text if c.isdigit())
        if len(ogrn_value) >= 13:
            data["ogrn"] = ogrn_value

    # Выручка — ищем в финансовых данных
    for el in soup.select("[class*='finance'], [class*='revenue'], .finance-item"):
        text = el.get_text(strip=True)
        if "выручка" in text.lower():
            data["revenue"] = text

    # Прибыль
    for el in soup.select("[class*='finance'], [class*='profit'], .finance-item"):
        text = el.get_text(strip=True)
        if "прибыль" in text.lower():
            data["profit"] = text

    return data


async def _get_page_html(page: Page, url: str) -> str:
    """Загружает страницу и возвращает HTML."""
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # Ждём загрузки контента
    await page.wait_for_timeout(2000)
    # Прокрутка для подгрузки динамического контента
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
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

            # Ищем карточки компаний
            cards = soup.select(
                ".company-item, .search-result-item, "
                "[class*='companyItem'], .search-result__item, "
                ".company-row, [data-company-id]"
            )

            if not cards:
                # Пробуем альтернативные селекторы
                cards = soup.select("div[class*='company'], div[class*='search-result']")

            if not cards:
                logger.info("Больше результатов нет (страница %d)", current_page)
                break

            # Общее количество результатов (из заголовка)
            if current_page == 1:
                total_el = soup.select_one(
                    "[class*='total'], [class*='count'], .search-result__count"
                )
                if total_el:
                    total_text = total_el.get_text(strip=True)
                    digits = "".join(c for c in total_text if c.isdigit())
                    if digits:
                        total_found = int(digits)
                        logger.info("Всего найдено: %d компаний", total_found)

            for card in cards:
                company = _parse_company_card(card)
                if company:
                    companies.append(company)

            if progress_callback:
                await progress_callback(
                    total_found or len(companies), len(companies)
                )

            logger.info(
                "Страница %d: найдено %d карточек, всего собрано %d",
                current_page, len(cards), len(companies),
            )

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

    Переходит на страницу каждой компании и собирает контактные данные.
    """
    page = await context.new_page()

    try:
        for i, company in enumerate(companies):
            if not company.inn:
                continue

            try:
                detail_url = f"https://www.rusprofile.ru/search?query={company.inn}"
                html = await _get_page_html(page, detail_url)
                soup = BeautifulSoup(html, "lxml")

                # Ищем ссылку на страницу компании
                link = soup.select_one(
                    ".company-item__title a, .company-name a, "
                    "[class*='companyName'] a, h4 a"
                )
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"https://www.rusprofile.ru{href}"
                    html = await _get_page_html(page, href)
                    details = _parse_company_page(html)

                    if details:
                        if details.get("phone") and not company.phone:
                            company.phone = details["phone"]
                        if details.get("email") and not company.email:
                            company.email = details["email"]
                        if details.get("site") and not company.site:
                            company.site = details["site"]
                        if details.get("ogrn") and not company.ogrn:
                            company.ogrn = details["ogrn"]
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
