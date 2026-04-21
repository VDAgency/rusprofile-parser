"""Парсер компаний с Rusprofile — данные из JSON-ответа /ajax_auth.php.

Vue-форма /search-advanced сабмитит POST к ``/ajax_auth.php?action=search_advanced``
с ``application/json`` body и свежим X-CSRF-Token. Мы через ``page.route()``
перехватываем этот POST и подменяем в теле фильтры/номер страницы, а
заголовки (включая CSRF) Vue проставляет сам. Ответ — готовый JSON со
всеми полями карточек (name, inn, ogrn, region, address, finance_revenue,
link), ничего парсить из HTML не нужно.

Подтверждённые ключи body (см. ``scripts/diag_combos.py`` →
``logs/diag_combos.log``):

    {"state-1": true, "okved_strict": true,
     "region": ["63"], "okopf": ["12165","12300"],
     "okved": ["46.9"], "query": "...", "page": "2"}
"""

import asyncio
import json
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
    RUSPROFILE_BASE_URL,
)
from src.rusprofile.auth import _goto
from src.rusprofile.filters import SearchFilters, filters_to_body

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


SEARCH_URL = f"{RUSPROFILE_BASE_URL}/search-advanced"
PAGE_SIZE = 50  # Rusprofile отдаёт страницами по 50 карточек


def _format_money(value) -> str:
    """Форматирует сумму в рублях: 2341987000 → '2 341 987 000 руб.'."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return f"{n:,}".replace(",", " ") + " руб."


def _company_from_json(item: dict) -> Optional["Company"]:
    """Строит Company из одной записи ``result[]`` JSON-ответа API.

    Поля JSON подтверждены дампом rusprofile (2026-04):
    name, raw_name, inn, ogrn, region, address, link, okved_descr,
    main_okved_id, finance_revenue, inactive, status_extended, reg_date.
    """
    name = (item.get("name") or item.get("raw_name") or "").replace("\xa0", " ").strip()
    if not name:
        return None

    inn = str(item.get("inn") or "").strip()
    ogrn = str(item.get("ogrn") or item.get("raw_ogrn") or "").strip()
    region = (item.get("region") or "").strip()
    address = (item.get("address") or "").strip()
    link = (item.get("link") or item.get("url") or "").strip()
    okved = (item.get("okved_descr") or item.get("main_okved_id") or "").strip()
    revenue = _format_money(item.get("finance_revenue"))

    status = "Недействующая" if item.get("inactive") else "Действующая"

    return Company(
        name=name,
        inn=inn,
        ogrn=ogrn,
        region=region,
        address=address,
        okved=okved,
        revenue=revenue,
        status=status,
        parse_date=datetime.now().strftime("%d.%m.%Y"),
        detail_href=link,
    )


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


_SUBMIT_JS = """() => {
    const f = document.getElementById('filter-form') || document.querySelector('form');
    if (!f) return 'no-form';
    const btn = f.querySelector(
        'button[type="submit"], input[type="submit"], .submit-button, .btn-submit'
    );
    if (btn) { btn.click(); return 'btn-click'; }
    if (typeof f.requestSubmit === 'function') { f.requestSubmit(); return 'requestSubmit'; }
    f.submit();
    return 'submit';
}"""


async def _fetch_page_json(
    page: Page,
    overrides: dict,
    responses: list,
    max_wait_sec: int = 20,
) -> Optional[dict]:
    """Открывает /search-advanced, сабмитит форму и возвращает JSON-ответ API.

    ``overrides`` должен содержать полный набор полей body для конкретной
    страницы (включая ``page``). Route-handler в ``parse_search_results``
    читает ``overrides`` через замыкание; здесь мы только обновляем его
    и сабмитим форму.
    """
    try:
        await _goto(page, SEARCH_URL, timeout=45000)
    except Exception as e:
        logger.warning("Не смог открыть форму поиска: %s", e)
        return None

    try:
        await page.wait_for_selector("#state-1", state="attached", timeout=20000)
    except Exception:
        logger.warning("Форма поиска не отрисовалась (нет #state-1)")
        return None

    await page.wait_for_timeout(1500)

    responses.clear()
    try:
        await page.evaluate(_SUBMIT_JS)
    except Exception as e:
        logger.warning("Не смог сабмитить форму: %s", e)
        return None

    # Ждём JSON ответа (опрос с шагом 300мс)
    for _ in range(int(max_wait_sec * 1000 / 300)):
        if responses:
            break
        await page.wait_for_timeout(300)

    return responses[-1] if responses else None


async def parse_search_results(
    context: BrowserContext,
    filters: SearchFilters,
    progress_callback=None,
) -> list[Company]:
    """Парсит результаты поиска компаний с пагинацией.

    Схема:
    1. Открываем /search-advanced, Vue-форма подгружает свежий X-CSRF-Token.
    2. ``page.route()`` перехватывает POST к ``/ajax_auth.php`` и подменяет
       body на нужные фильтры + ``page``, оставляя заголовки (CSRF) Vue.
    3. Сабмитим форму через ``button[type=submit] → requestSubmit``.
    4. JSON-ответ содержит ``result[]`` с полями ``name/inn/ogrn/region/
       address/finance_revenue/link`` — этого хватает для Company без
       дополнительного HTML-парсинга.
    5. Для следующей страницы обновляем ``overrides['page']`` и делаем
       повторный goto + submit (свежий CSRF — надёжнее, чем переиспользование).
    """
    base_body = filters_to_body(filters)
    overrides = {"body": base_body, "page": 1}

    companies: list[Company] = []
    seen_inn: set[str] = set()
    responses: list[dict] = []

    page = await context.new_page()

    async def handle_route(route):
        try:
            existing = json.loads(route.request.post_data or "") if route.request.post_data else {}
        except Exception:
            existing = {}
        # Накладываем наши фильтры поверх того, что сформировала Vue
        existing.update(overrides["body"])
        existing["page"] = str(overrides["page"])
        new_body = json.dumps(existing, ensure_ascii=False)
        try:
            await route.continue_(post_data=new_body)
        except Exception as e:
            logger.warning("route.continue_ упал: %s", e)

    async def on_response(resp):
        if "ajax_auth.php" not in resp.url:
            return
        try:
            body_bytes = await resp.body()
            j = json.loads(body_bytes.decode("utf-8", errors="replace"))
            responses.append(j)
        except Exception as e:
            logger.debug("Не смог разобрать ajax_auth ответ: %s", e)

    await page.route("**/ajax_auth.php*", handle_route)
    response_listener = lambda r: asyncio.create_task(on_response(r))
    page.on("response", response_listener)

    try:
        total_found: Optional[int] = None
        current_page = 1

        while current_page <= MAX_PAGES:
            overrides["page"] = current_page
            logger.info("Парсим страницу %d, фильтры=%s", current_page, base_body)

            j = await _fetch_page_json(page, overrides, responses)
            if not j:
                logger.warning("Нет JSON-ответа на странице %d — стоп", current_page)
                break

            code = j.get("code")
            if code not in (None, 0):
                logger.warning(
                    "API вернул ошибку на странице %d: code=%s msg=%s",
                    current_page, code, j.get("message"),
                )
                break

            if total_found is None:
                total_found = j.get("total_count") or (
                    (j.get("ul_count") or 0) + (j.get("ip_count") or 0)
                )
                logger.info("Всего найдено: %d компаний", total_found)

            result_items = j.get("result") or []
            if not result_items:
                logger.info("Пусто на странице %d — конец выдачи", current_page)
                break

            new_on_page = 0
            for item in result_items:
                company = _company_from_json(item)
                if not company:
                    continue
                if company.inn and company.inn in seen_inn:
                    continue
                if company.inn:
                    seen_inn.add(company.inn)
                companies.append(company)
                new_on_page += 1

            if progress_callback:
                await progress_callback(
                    total_found or len(companies), len(companies)
                )

            logger.info(
                "Стр %d: в ответе %d записей, новых %d, всего собрано %d",
                current_page, len(result_items), new_on_page, len(companies),
            )

            # Стоп-условия
            if new_on_page == 0:
                logger.info("Нет новых карточек — стоп")
                break
            if total_found and len(companies) >= total_found:
                logger.info("Собрано всё найденное (%d) — стоп", total_found)
                break
            if len(result_items) < PAGE_SIZE:
                logger.info(
                    "Получено %d < %d — последняя страница",
                    len(result_items), PAGE_SIZE,
                )
                break

            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            await asyncio.sleep(delay)
            current_page += 1

    except Exception as e:
        logger.error("Ошибка парсинга: %s", e, exc_info=True)
    finally:
        try:
            page.remove_listener("response", response_listener)
        except Exception:
            pass
        try:
            await page.unroute("**/ajax_auth.php*")
        except Exception:
            pass
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
