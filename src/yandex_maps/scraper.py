"""Playwright-скрапер Яндекс Карт — поиск, скролл списка, сбор сниппетов."""

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import quote

from playwright.async_api import BrowserContext, Page, TimeoutError as PWTimeout

from src.config import YANDEX_MAPS_BASE_URL, YANDEX_MAX_PLACES
from src.yandex_maps.parser import YandexPlace

logger = logging.getLogger(__name__)


# --- детекция капчи --------------------------------------------------------

async def is_captcha(page: Page) -> bool:
    """True, если на странице появилась Yandex SmartCaptcha."""
    # SmartCaptcha грузится в iframe или как отдельный блок .AdvancedCaptcha
    selectors = [
        'iframe[src*="smartcaptcha"]',
        'iframe[src*="captcha"]',
        '.AdvancedCaptcha',
        '.CheckboxCaptcha',
        'form[action*="checkcaptcha"]',
    ]
    for sel in selectors:
        if await page.query_selector(sel):
            return True
    # Текстовый триггер: страница с сообщением «Подтвердите, что запросы отправляли вы»
    try:
        title = (await page.title()).lower()
        if "подтвердите" in title or "captcha" in title:
            return True
    except Exception:
        pass
    return False


async def dump_captcha(page: Page, logs_dir) -> None:
    """Сохраняет HTML + скриншот страницы с капчей для отладки."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        html = await page.content()
        (logs_dir / f"yandex_captcha_{stamp}.html").write_text(html, encoding="utf-8")
        await page.screenshot(path=str(logs_dir / f"yandex_captcha_{stamp}.png"))
        logger.warning("Сохранены артефакты капчи: yandex_captcha_%s.{html,png}", stamp)
    except Exception as e:
        logger.warning("Не удалось сохранить артефакты капчи: %s", e)


# --- открытие поиска -------------------------------------------------------

async def open_search(page: Page, region: str, category: str) -> None:
    """Открывает страницу поиска Яндекс Карт.

    Формат запроса — «<рубрика> <регион>», Яндекс сам понимает геоконтекст.
    Примеры: "стоматологии Москва", "автосервисы Республика Татарстан".
    """
    query = f"{category} {region}".strip()
    url = f"{YANDEX_MAPS_BASE_URL}?text={quote(query)}"
    logger.info("Открываю Яндекс Карты: %s", url)

    # domcontentloaded — быстрее чем networkidle; Vue-рендер ждём отдельно.
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    # Дать Vue отрисовать левую панель
    await page.wait_for_timeout(3000)


# --- скролл результатов ----------------------------------------------------

SNIPPET_SELECTORS = [
    '[class*="search-snippet-view"]',
    '[class*="search-business-snippet"]',
    '[class*="SearchSnippet"]',
    'li[class*="search-list-view__item"]',
]

LIST_CONTAINER_SELECTORS = [
    '.scroll__container',
    '[class*="search-list-view__list"]',
    '[class*="SearchList"]',
]


async def _count_snippets(page: Page) -> int:
    """Считает уникальные карточки организаций, видимые сейчас в DOM.

    Считаем по ссылкам вида ``/maps/.../org/<slug>/<id>/`` — это и есть
    настоящие карточки организаций в левой панели. Широкий селектор
    ``[class*="search-snippet-view"]`` даёт много ложных срабатываний
    (вложенные элементы, кнопки действий), поэтому для логики
    остановки скролла нужен точный счёт.
    """
    return await page.evaluate(
        """
        () => {
          const anchors = document.querySelectorAll('a[href*="/org/"]');
          const ids = new Set();
          for (const a of anchors) {
            const m = (a.getAttribute('href') || '').match(/org\\/[^/?#]+\\/\\d+/);
            if (m) ids.add(m[0]);
          }
          return ids.size;
        }
        """
    )


async def scroll_results(page: Page, max_items: int = YANDEX_MAX_PLACES) -> int:
    """Скроллит левую панель до конца или до лимита.

    Возвращает финальное число обнаруженных сниппетов.
    """
    # Ждём появления хотя бы одного сниппета
    try:
        await page.wait_for_selector(
            ", ".join(SNIPPET_SELECTORS),
            timeout=15000,
        )
    except PWTimeout:
        logger.warning("Сниппеты не появились — возможно капча или нулевой результат")
        return 0

    stable_iterations = 0
    previous_count = 0
    max_stable = 3  # если 3 скролла подряд без прироста — считаем что дошли до конца

    # Определяем контейнер скролла
    js_scroll = f"""
    (selectors) => {{
      for (const sel of selectors) {{
        const el = document.querySelector(sel);
        if (el) {{ el.scrollBy(0, el.clientHeight * 0.9); return true; }}
      }}
      // Fallback — скролл окна
      window.scrollBy(0, window.innerHeight * 0.9);
      return false;
    }}
    """

    for iteration in range(200):  # жёсткий потолок 200 итераций
        count = await _count_snippets(page)
        if count >= max_items:
            logger.info("Достигнут лимит %d карточек", max_items)
            break

        if count == previous_count:
            stable_iterations += 1
            if stable_iterations >= max_stable:
                logger.info("Скролл стабилизирован на %d карточках", count)
                break
        else:
            stable_iterations = 0

        previous_count = count
        await page.evaluate(js_scroll, LIST_CONTAINER_SELECTORS)
        await page.wait_for_timeout(1200)

    return await _count_snippets(page)


# --- извлечение данных из сниппета -----------------------------------------

# JS, читающий данные из каждой карточки по устойчивым паттернам классов.
# Имена классов Яндекса обфусцированы, но в них сохраняются префиксы
# «search-snippet-view__», «search-business-snippet-view__» и т.п.
_JS_EXTRACT = r"""
(snippetSelectors) => {
  const root = Array.from(
    document.querySelectorAll(snippetSelectors.join(','))
  );

  const text = (el) => (el ? el.innerText.trim() : '');

  // Ищем ближайшего потомка по подстроке класса (первый матч из списка).
  const q = (el, substrs) => {
    for (const sub of substrs) {
      const child = el.querySelector(`[class*="${sub}"]`);
      if (child) return child;
    }
    return null;
  };

  // Нормализуем ссылку на карточку. Яндекс может редиректить на yandex.com
  // и подставлять город/регион в путь (/maps/213/moscow/org/...). Матчим
  // весь путь до `/org/<slug>/<id>/` независимо от количества сегментов.
  const normalizeOrgUrl = (href) => {
    if (!href) return '';
    const m = href.match(/\/maps\/[^?#]*?org\/[^/?#]+\/\d+/);
    if (!m) return '';
    return `https://yandex.ru${m[0]}/`;
  };

  const seenUrls = new Set();
  const result = [];

  for (const el of root) {
    // Берём ссылку на карточку организации. Без неё это не настоящий сниппет
    // (а, например, кнопка «Предложить правку» или reviews-ссылка).
    const anchor = el.querySelector('a[href*="/org/"]');
    const orgUrl = normalizeOrgUrl(anchor ? anchor.getAttribute('href') || anchor.href : '');
    if (!orgUrl) continue;
    if (seenUrls.has(orgUrl)) continue;
    seenUrls.add(orgUrl);

    const titleEl = q(el, ['title-link', 'title', 'snippet-title']);
    const name = text(titleEl);
    if (!name) continue;

    const addrEl = q(el, ['address', 'subtitle-item']);
    const address = text(addrEl);

    const categoryEl = q(el, ['category']);
    const categories = text(categoryEl);

    // Рейтинг: ищем число вида 4,7 или 4.7 в блоке rating.
    const ratingBlock = q(el, ['rating-badge', 'business-rating-badge', 'rating']);
    let rating = '';
    if (ratingBlock) {
      const rm = ratingBlock.innerText.match(/(\d+[.,]\d+|\d+)/);
      if (rm) rating = rm[1];
    }

    // Количество отзывов ищем по всему тексту сниппета — на карточке это
    // чаще отдельная ссылка «692 отзыва», которая не входит в ratingBlock.
    let reviewsCount = '';
    const fullText = el.innerText || '';
    const cm = fullText.match(/(\d+)\s*(?:отзыв|оцен)/i);
    if (cm) reviewsCount = cm[1];

    const hoursEl = q(el, ['working-status', 'hours', 'business-working-status']);

    result.push({
      name: name,
      categories: categories,
      address: address,
      rating: rating,
      reviews_count: reviewsCount,
      hours: text(hoursEl),
      yandex_url: orgUrl,
    });
  }
  return result;
}
"""


async def extract_snippets(page: Page) -> list[dict]:
    """Вытаскивает данные всех видимых сниппетов из левой панели."""
    return await page.evaluate(_JS_EXTRACT, SNIPPET_SELECTORS)


# --- верхний уровень -------------------------------------------------------

async def scrape_list(
    context: BrowserContext,
    region: str,
    category: str,
    max_places: int = YANDEX_MAX_PLACES,
    progress_callback=None,
    logs_dir=None,
) -> list[YandexPlace]:
    """Открывает Яндекс Карты, собирает список сниппетов (без детальной панели).

    MVP: без открытия правой карточки (phone/site придётся собирать отдельным шагом).
    """
    page = await context.new_page()
    places: list[YandexPlace] = []
    today = datetime.now().strftime("%d.%m.%Y")

    try:
        await open_search(page, region, category)

        if await is_captcha(page):
            if logs_dir:
                await dump_captcha(page, logs_dir)
            raise RuntimeError(
                "Яндекс запросил капчу (SmartCaptcha). "
                "Попробуйте позже или подключите прокси в .env (PROXY_SERVER)."
            )

        total = await scroll_results(page, max_items=max_places)
        logger.info("Найдено сниппетов: %d", total)

        if total == 0:
            return []

        if progress_callback:
            try:
                await progress_callback(total, 0)
            except Exception:
                pass

        raw = await extract_snippets(page)
        logger.info("Извлечено из DOM: %d записей", len(raw))

        for item in raw:
            places.append(YandexPlace(
                name=item.get("name", ""),
                categories=item.get("categories", ""),
                region=region,
                address=item.get("address", ""),
                rating=item.get("rating", ""),
                reviews_count=item.get("reviews_count", ""),
                hours=item.get("hours", ""),
                yandex_url=item.get("yandex_url", ""),
                parse_date=today,
            ))

        if progress_callback:
            try:
                await progress_callback(total, len(places))
            except Exception:
                pass

    finally:
        await page.close()

    return places


# --- детализация карточки (phone/site/coordinates) --------------------------

_JS_EXTRACT_DETAIL = r"""
() => {
  const text = (el) => (el ? el.innerText.trim() : '');

  // Правая панель карточки — контейнер с классом *card-view* или *business-card*
  const card = document.querySelector(
    '[class*="card-view"], [class*="business-card"], [class*="org-page-view"]'
  );
  if (!card) return {};

  // Телефон — ссылка tel: или блок с классом *phones*.
  // В innerText кнопки Яндекс часто вставляет вторую строку «Показать телефон» —
  // берём только первую строку, если её нет — падаем в атрибут href.
  let phone = '';
  const phoneLink = card.querySelector('a[href^="tel:"]');
  if (phoneLink) {
    const raw = (phoneLink.innerText || '').split('\n')[0].trim();
    phone = raw || phoneLink.getAttribute('href').replace('tel:', '');
  } else {
    const phoneEl = card.querySelector('[class*="phones-view__phone"], [class*="phone"]');
    if (phoneEl) phone = (phoneEl.innerText || '').split('\n')[0].trim();
  }

  // Сайт — внешняя ссылка в блоке *links* или *actions*, не yandex.*
  let site = '';
  const linkEls = card.querySelectorAll(
    '[class*="business-urls-view"] a, [class*="links"] a, a[class*="link-overflow"]'
  );
  for (const a of linkEls) {
    const href = a.getAttribute('href') || '';
    if (href.startsWith('http') && !/yandex\.(ru|com)/.test(href)) {
      site = href;
      break;
    }
  }

  // Координаты — в URL страницы (?ll=lon,lat) или в data-coordinates
  let coordinates = '';
  const m = location.href.match(/[?&]ll=([-\d.]+)%2C([-\d.]+)/);
  if (m) coordinates = m[2] + ',' + m[1];  // lat,lon — нормализуем порядок

  return { phone, site, coordinates };
}
"""


async def enrich_place_details(
    context: BrowserContext,
    places: list[YandexPlace],
    progress_callback=None,
    max_details: int | None = None,
) -> list[YandexPlace]:
    """Для каждой карточки открывает прямую ссылку и тянет phone/site/coordinates.

    Может работать долго (3–5 сек на карточку). Используется после scrape_list.
    """
    if not places:
        return places

    limit = max_details if max_details is not None else len(places)
    page = await context.new_page()

    try:
        for i, place in enumerate(places[:limit]):
            if not place.yandex_url:
                continue

            try:
                await page.goto(place.yandex_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)

                if await is_captcha(page):
                    logger.warning("Капча при обогащении карточки #%d — прерываю детализацию", i)
                    break

                details = await page.evaluate(_JS_EXTRACT_DETAIL)

                if details.get("phone") and not place.phone:
                    place.phone = details["phone"]
                if details.get("site") and not place.site:
                    place.site = details["site"]
                if details.get("coordinates") and not place.coordinates:
                    place.coordinates = details["coordinates"]

                if progress_callback:
                    try:
                        await progress_callback(limit, i + 1)
                    except Exception:
                        pass

                await asyncio.sleep(3.0)

            except Exception as e:
                logger.warning("Ошибка детализации '%s': %s", place.name, e)
                continue

    finally:
        await page.close()

    return places
