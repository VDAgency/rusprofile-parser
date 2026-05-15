"""Stage 1 — извлечение текста с сайта компании.

Загружает несколько страниц (главная + /about + /services), извлекает
основной контент через ``trafilatura`` (быстро, без JS) и склеивает.
При ошибке/блокировке (HTTP 403/429) можно сделать fallback на Playwright,
если передан существующий ``BrowserContext``.

Возвращает ``WebsiteExtractResult``:
- ``text`` — чистый текст (≤ ``max_chars``).
- ``pages_fetched`` — реально загруженные URL.
- ``used_playwright`` — был ли fallback.
- ``validation_issue`` — `None` / `"too_short"` / `"stub"` / `"fetch_failed"`.
- ``fetch_duration_ms``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)

# Дополнительные страницы, которые часто содержат описание бизнеса.
SECONDARY_PATHS = (
    "/about", "/o-kompanii", "/o-nas", "/about-us",
    "/services", "/uslugi", "/products", "/produkciya",
    "/contacts", "/kontakty",
)

# Поведенчески «сайт-заглушка» — встречаются ключевые фразы.
_STUB_PATTERNS = [
    r"\bsite\s+coming\s+soon\b",
    r"\bdomain\s+(is\s+)?(parked|for\s+sale)\b",
    r"\bunder\s+construction\b",
    r"\bсайт\s+скоро\s+откроется\b",
    r"\bдомен\s+припаркован\b",
    r"\bстраница\s+не\s+найдена\b",
    r"\b404\s+(not\s+found|страница)\b",
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


@dataclass
class WebsiteExtractResult:
    text: str | None
    pages_fetched: list[str] = field(default_factory=list)
    used_playwright: bool = False
    validation_issue: str | None = None
    fetch_duration_ms: int = 0


# ---------------------------------------------------------------------------
# Загрузка одной страницы
# ---------------------------------------------------------------------------


async def _fetch_via_httpx(
    url: str, timeout_s: int, *, user_agent: str = DEFAULT_USER_AGENT
) -> tuple[int, str | None]:
    """(status_code, html). status=0 при сетевой ошибке."""
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers=headers
        ) as client:
            resp = await client.get(url)
            return resp.status_code, resp.text
    except Exception as e:  # noqa: BLE001
        logger.info("httpx fetch failed for %s: %s", url, e)
        return 0, None


async def _fetch_via_playwright(
    url: str, context: "BrowserContext", timeout_s: int
) -> tuple[int, str | None]:
    """Headless-fallback. Возвращает (200, html) при успехе, иначе (0, None)."""
    page = await context.new_page()
    try:
        resp = await page.goto(
            url, wait_until="domcontentloaded", timeout=timeout_s * 1000
        )
        await page.wait_for_timeout(1500)
        html = await page.content()
        status = resp.status if resp else 0
        return status, html
    except Exception as e:  # noqa: BLE001
        logger.info("playwright fetch failed for %s: %s", url, e)
        return 0, None
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# Извлечение чистого текста через trafilatura
# ---------------------------------------------------------------------------


def _extract_text(html: str) -> str:
    if not html:
        return ""
    try:
        import trafilatura
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if text:
            return text.strip()
    except Exception as e:  # noqa: BLE001
        logger.info("trafilatura extraction failed: %s", e)

    # Fallback на BeautifulSoup, если trafilatura не справилась.
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text
    except Exception as e:  # noqa: BLE001
        logger.info("bs4 fallback extraction failed: %s", e)
        return ""


def _normalize_url(base: str) -> str:
    """Гарантирует, что URL начинается с http(s) и оканчивается без trailing /."""
    if not base:
        return ""
    base = base.strip()
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base.rstrip("/")


def _validate_text(text: str | None) -> str | None:
    """Возвращает причину 'плохого' контента или None если ОК."""
    if not text or len(text) < 300:
        return "too_short"
    lo = text.lower()
    for pat in _STUB_PATTERNS:
        if re.search(pat, lo):
            return "stub"
    return None


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------


async def extract_website_text(
    base_url: str,
    *,
    timeout_s: int = 15,
    max_pages: int = 3,
    max_chars: int = 12_000,
    playwright_context: "BrowserContext | None" = None,
) -> WebsiteExtractResult:
    """Извлекает чистый текст с сайта компании.

    Алгоритм:
    1. Грузим главную через httpx.
    2. Если 403/429 или пустой HTML и есть ``playwright_context`` —
       fallback на Playwright.
    3. Парсим главную, ищем на ней ссылки на вторичные страницы из
       ``SECONDARY_PATHS``, грузим их (тоже httpx → playwright).
    4. Склеиваем тексты, обрезаем до ``max_chars``.
    5. Валидируем (заглушка / слишком мало контента).
    """
    started = time.monotonic()
    base_url = _normalize_url(base_url)
    if not base_url:
        return WebsiteExtractResult(
            text=None,
            validation_issue="fetch_failed",
            fetch_duration_ms=0,
        )

    pages_fetched: list[str] = []
    used_playwright = False

    # ─── 1. Главная ────────────────────────────────────────────────────
    status, html = await _fetch_via_httpx(base_url, timeout_s)
    if (status in (403, 429) or not html) and playwright_context is not None:
        used_playwright = True
        status, html = await _fetch_via_playwright(
            base_url, playwright_context, timeout_s
        )

    if not html:
        return WebsiteExtractResult(
            text=None,
            validation_issue="fetch_failed",
            fetch_duration_ms=int((time.monotonic() - started) * 1000),
            used_playwright=used_playwright,
        )

    pages_fetched.append(base_url)
    main_text = _extract_text(html)
    text_chunks: list[str] = [main_text] if main_text else []

    # ─── 2. Дополнительные страницы ────────────────────────────────────
    if max_pages > 1:
        # Собираем кандидатов: ссылки в HTML главной, начинающиеся
        # с одного из SECONDARY_PATHS.
        candidates = _find_secondary_links(html, base_url)
        wanted = max_pages - 1

        async def _grab(url: str) -> str | None:
            st, h = await _fetch_via_httpx(url, timeout_s)
            if (st in (403, 429) or not h) and playwright_context is not None:
                st, h = await _fetch_via_playwright(url, playwright_context, timeout_s)
            if not h:
                return None
            pages_fetched.append(url)
            return _extract_text(h)

        results = await asyncio.gather(
            *[_grab(u) for u in candidates[:wanted]], return_exceptions=False
        )
        for t in results:
            if t:
                text_chunks.append(t)

    text = "\n\n".join(c for c in text_chunks if c).strip()
    if len(text) > max_chars:
        text = text[:max_chars]

    issue = _validate_text(text)

    return WebsiteExtractResult(
        text=text if text else None,
        pages_fetched=pages_fetched,
        used_playwright=used_playwright,
        validation_issue=issue,
        fetch_duration_ms=int((time.monotonic() - started) * 1000),
    )


def _find_secondary_links(html: str, base_url: str) -> list[str]:
    """Извлекает абсолютные URL вторичных страниц из HTML главной.

    Простой regex — мы не парсим всё DOM, нам нужен только список
    ссылок начинающихся с одного из ``SECONDARY_PATHS``.
    """
    links_found: list[str] = []
    base_host = urlparse(base_url).netloc.lower()
    href_pattern = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
    seen = set()

    for raw_href in href_pattern.findall(html):
        href = raw_href.strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        # Делаем абсолютный URL.
        absolute = urljoin(base_url + "/", href)
        parsed = urlparse(absolute)
        if parsed.netloc.lower() != base_host:
            continue
        path = parsed.path.lower().rstrip("/")
        for sp in SECONDARY_PATHS:
            if path == sp or path.startswith(sp + "/"):
                if absolute not in seen:
                    seen.add(absolute)
                    links_found.append(absolute)
                break
    return links_found
