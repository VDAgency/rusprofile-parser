"""Тесты Stage 1 — извлечение текста с сайта.

Без реальных HTTP-запросов: патчим _fetch_via_httpx.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.ai.website_extractor import (
    WebsiteExtractResult,
    _extract_text,
    _find_secondary_links,
    _normalize_url,
    _validate_text,
    extract_website_text,
)


# ─── Утилиты ─────────────────────────────────────────────────────────────


def test_normalize_url_adds_https():
    assert _normalize_url("example.com") == "https://example.com"
    assert _normalize_url("https://example.com/") == "https://example.com"
    assert _normalize_url("http://x.ru/") == "http://x.ru"
    assert _normalize_url("") == ""


def test_validate_too_short():
    assert _validate_text("коротко") == "too_short"
    assert _validate_text(None) == "too_short"


def test_validate_stub_detected():
    text = (
        "Site coming soon — мы работаем над запуском нового проекта. " * 20
    )
    assert _validate_text(text) == "stub"


def test_validate_ok():
    text = "Полезный длинный контент сайта. " * 40
    assert _validate_text(text) is None


# ─── _extract_text (через trafilatura или fallback bs4) ──────────────────


def test_extract_text_strips_html():
    html = """
    <html><head><title>X</title></head>
    <body>
    <header>Меню</header>
    <main>
    <h1>Заголовок</h1>
    <p>Текст компании. Описание услуг и продуктов.</p>
    <p>Контакты: +7 495 123-45-67.</p>
    </main>
    <footer>Футер</footer>
    <script>console.log('x')</script>
    </body></html>
    """
    text = _extract_text(html)
    assert "Заголовок" in text
    assert "Текст компании" in text
    assert "console.log" not in text


def test_extract_text_empty_html():
    assert _extract_text("") == ""


# ─── _find_secondary_links ───────────────────────────────────────────────


def test_find_secondary_links_filters_paths():
    html = """
    <a href="/about">О нас</a>
    <a href="/services">Услуги</a>
    <a href="/blog">Блог</a>
    <a href="https://example.com/uslugi">Услуги</a>
    <a href="/contacts/office">Офис</a>
    <a href="https://other-site.com/about">Внешний</a>
    """
    base = "https://example.com"
    links = _find_secondary_links(html, base)
    assert "https://example.com/about" in links
    assert "https://example.com/services" in links
    assert "https://example.com/uslugi" in links
    assert "https://example.com/contacts/office" in links
    assert all("other-site.com" not in u for u in links)
    assert all("/blog" not in u for u in links)


# ─── extract_website_text — интеграция (с моком httpx) ──────────────────


SAMPLE_MAIN_HTML = """
<html><body>
<h1>Оптовая компания канцтоваров</h1>
<p>Мы оптовый поставщик канцелярских товаров для бизнеса. Работаем со
складом в Подмосковье, доставляем по всей России. Партнёрская программа
для дилеров.</p>
<a href="/about">О нас</a>
</body></html>
""" + ("<p>Дополнительный текст для длины.</p>" * 10)


@pytest.mark.anyio
async def test_extract_website_text_main_only():
    with patch("src.ai.website_extractor._fetch_via_httpx",
               new=AsyncMock(return_value=(200, SAMPLE_MAIN_HTML))):
        result = await extract_website_text(
            "https://example.com", max_pages=1,
        )
    assert isinstance(result, WebsiteExtractResult)
    assert result.text is not None
    # trafilatura выкидывает «декоративные» H1 — проверяем основной контент <p>.
    assert "оптовый поставщик" in result.text.lower()
    assert result.validation_issue is None
    assert result.used_playwright is False


@pytest.mark.anyio
async def test_extract_website_text_fetch_failed():
    with patch("src.ai.website_extractor._fetch_via_httpx",
               new=AsyncMock(return_value=(0, None))):
        result = await extract_website_text("https://example.com")
    assert result.text is None
    assert result.validation_issue == "fetch_failed"


@pytest.mark.anyio
async def test_extract_website_text_too_short():
    with patch("src.ai.website_extractor._fetch_via_httpx",
               new=AsyncMock(return_value=(200, "<html><body><p>tiny</p></body></html>"))):
        result = await extract_website_text("https://example.com", max_pages=1)
    assert result.validation_issue == "too_short"


@pytest.mark.anyio
async def test_extract_website_text_max_chars_truncates():
    big_html = "<html><body>" + "<p>текст</p>" * 5000 + "</body></html>"
    with patch("src.ai.website_extractor._fetch_via_httpx",
               new=AsyncMock(return_value=(200, big_html))):
        result = await extract_website_text(
            "https://example.com", max_pages=1, max_chars=500,
        )
    assert result.text is not None
    assert len(result.text) <= 500


@pytest.mark.anyio
async def test_extract_website_text_empty_url():
    result = await extract_website_text("")
    assert result.text is None
    assert result.validation_issue == "fetch_failed"
