"""Высокоуровневый entrypoint парсера Яндекс Карт.

Использование из кода бота::

    from src.yandex_maps.runner import parse_yandex
    places = await parse_yandex("Москва", "стоматологии")

CLI-прогон для отладки::

    python -m src.yandex_maps.runner --region "Москва" --category "стоматологии" --max 30
"""

import argparse
import asyncio
import logging
import sys

from playwright.async_api import async_playwright

from src.config import (
    BASE_DIR,
    LOG_DIR,
    PROXY_PASSWORD,
    PROXY_SERVER,
    PROXY_USERNAME,
    YANDEX_MAX_PLACES,
)
from src.yandex_maps.parser import YandexPlace
from src.yandex_maps.scraper import enrich_place_details, scrape_list

logger = logging.getLogger(__name__)


def _proxy_config() -> dict | None:
    """Возвращает конфиг прокси для Playwright или None."""
    if not PROXY_SERVER:
        return None
    cfg = {"server": PROXY_SERVER}
    if PROXY_USERNAME:
        cfg["username"] = PROXY_USERNAME
    if PROXY_PASSWORD:
        cfg["password"] = PROXY_PASSWORD
    return cfg


async def parse_yandex(
    region: str,
    category: str,
    max_places: int = YANDEX_MAX_PLACES,
    progress_callback=None,
    with_details: bool = True,
    headless: bool = True,
) -> list[YandexPlace]:
    """Запускает парсинг Яндекс Карт и возвращает список карточек.

    Args:
        region: регион ("Москва", "Республика Татарстан" и т.п.) — как отобразить
            пользователю; также уходит в поисковый запрос.
        category: вид деятельности ("стоматологии", "кафе").
        max_places: лимит карточек за один запуск.
        progress_callback: async (total, processed) — вызывается при прогрессе.
        with_details: тянуть ли phone/site/coordinates, открывая карточки.
            Отключить для быстрой проверки списка.
        headless: запускать ли браузер без UI.
    """
    proxy = _proxy_config()
    if proxy:
        logger.info("Используется прокси: %s", proxy.get("server"))

    launch_kwargs = {"headless": headless}
    if proxy:
        launch_kwargs["proxy"] = proxy

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch_kwargs)
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 900},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )

            # Простейший антидетект: маскируем navigator.webdriver.
            # Полноценный playwright-stealth можно подключить позже
            # через pip install playwright-stealth.
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            places = await scrape_list(
                context,
                region=region,
                category=category,
                max_places=max_places,
                progress_callback=progress_callback,
                logs_dir=LOG_DIR,
            )

            if with_details and places:
                logger.info("Обогащаю %d карточек деталями (phone/site)", len(places))
                places = await enrich_place_details(
                    context,
                    places,
                    progress_callback=progress_callback,
                )

            await context.close()
            return places
        finally:
            await browser.close()


# --- CLI -------------------------------------------------------------------

def _setup_logging() -> None:
    # На Windows stdout по умолчанию в cp1251 — кириллица превращается в «?».
    # Принудительно переключаем на UTF-8, чтобы отладочный вывод читался.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Парсер Яндекс Карт (отладочный CLI)")
    parser.add_argument("--region", required=True, help='Регион, напр. "Москва"')
    parser.add_argument("--category", required=True, help='Рубрика, напр. "стоматологии"')
    parser.add_argument("--max", type=int, default=30, help="Лимит карточек (по умолчанию 30)")
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Не открывать детальные карточки (быстрее, но без phone/site)",
    )
    parser.add_argument(
        "--head",
        action="store_true",
        help="Показать окно браузера (headless=False) — удобно для отладки",
    )
    args = parser.parse_args()

    _setup_logging()

    places = asyncio.run(parse_yandex(
        region=args.region,
        category=args.category,
        max_places=args.max,
        with_details=not args.no_details,
        headless=not args.head,
    ))

    print(f"\n=== Собрано карточек: {len(places)} ===\n")
    for i, p in enumerate(places[:10], 1):
        print(f"{i}. {p.name}")
        print(f"   Рубрики: {p.categories}")
        print(f"   Адрес:   {p.address}")
        if p.rating:
            print(f"   Рейтинг: {p.rating} ({p.reviews_count} отзывов)")
        if p.phone:
            print(f"   Телефон: {p.phone}")
        if p.site:
            print(f"   Сайт:    {p.site}")
        if p.yandex_url:
            print(f"   URL:     {p.yandex_url}")
        print()

    if len(places) > 10:
        print(f"... и ещё {len(places) - 10}")


if __name__ == "__main__":
    _cli()
