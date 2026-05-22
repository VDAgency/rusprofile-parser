"""CLI: дозабить email/site для уже спаршенных компаний (по run_ids).

Для каждой Company из указанных ParseRun'ов:
  1. Если email или site пуст — ищем компанию на Я.Картах по имени+региону.
  2. Открываем найденную карточку (`enrich_place_details`) — достаём
     phone / email / site / координаты, обновляем пустые поля.
  3. Если site уже известен (старый или из Я.Карт) и email всё ещё
     пуст — открываем сайт через website_extractor и пытаемся найти
     email регексом на главной + /contacts /kontakty и т.п.

Обновляются ТОЛЬКО пустые поля Company — никогда не перезаписываем
вручную уточнённые данные.

Использование:

    # Dry-run на первых 10 компаниях из run_id 17:
    python scripts/enrich_yandex_details.py --run-ids 17 --limit 10

    # Реально записать:
    python scripts/enrich_yandex_details.py --run-ids 17 --limit 10 --apply

    # Без скрейпа сайта (только Я.Карты — быстрее):
    python scripts/enrich_yandex_details.py --run-ids 14,15,17,19 --apply --no-website

Безопасность:
- Скрипт работает напрямую с БД и Playwright. Запускать на сервере.
- Я.Карты могут отдать капчу при долгой работе — скрипт прервётся
  с ошибкой и закоммитит уже сделанное.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db import get_session
from src.db.models import Company, ParseRun, RunCompany, Theme
from src.services.matching import name_similarity

logger = logging.getLogger(__name__)

# Регекс email — простой, не строгий по RFC, зато ловит большинство.
EMAIL_RE = re.compile(
    r"\b[\w.+\-]+@[\w\-]+\.[a-z]{2,10}\b",
    re.IGNORECASE,
)

# Email-домены, которые НЕ считаем валидным контактом (sentry, sample и т.п.).
EMAIL_BLACKLIST_DOMAINS = {
    "example.com", "example.ru", "domain.ru", "site.ru",
    "sentry.io", "wixpress.com",
}

# Минимальное сходство имени, при котором считаем что нашли «ту самую»
# организацию на Я.Картах.
MIN_NAME_SIMILARITY = 0.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_real_email(email: str) -> bool:
    """True если email — не из чёрного списка и не явный мусор."""
    if not email or "@" not in email:
        return False
    local, _, domain = email.lower().partition("@")
    if not local or not domain:
        return False
    if domain in EMAIL_BLACKLIST_DOMAINS:
        return False
    # Маркеры сэмплов в локальной части.
    bad_local = {"example", "test", "noreply", "no-reply", "sample"}
    if local in bad_local:
        return False
    return True


def _extract_email_from_text(text: str | None) -> str | None:
    if not text:
        return None
    for m in EMAIL_RE.findall(text):
        if _looks_like_real_email(m):
            return m
    return None


def _theme_region(theme: Theme | None) -> str:
    """Достаём регион из Theme — приоритет filters_json.region, иначе из title."""
    if theme is None:
        return ""
    region = (theme.filters_json or {}).get("region")
    if isinstance(region, list) and region:
        region = region[0]
    if region:
        return str(region)
    # Fallback из title: "Москва · Медицинский центр" → "Москва"
    if theme.title and " · " in theme.title:
        return theme.title.split(" · ", 1)[0].strip()
    return ""


def _company_region(company: Company, fallback: str) -> str:
    """Регион для поиска: сначала из Company.region, иначе fallback (из Theme)."""
    if company.region:
        return company.region
    return fallback


def _load_companies(
    session: Session, run_ids: list[int], limit: int | None,
) -> list[tuple[Company, str]]:
    """Возвращает [(company, region_hint)] из указанных run_id, где
    email ИЛИ site пуст. Region_hint — из Company.region или Theme."""
    rows = session.execute(
        select(Company, ParseRun)
        .join(RunCompany, RunCompany.company_id == Company.id)
        .join(ParseRun, ParseRun.id == RunCompany.run_id)
        .where(RunCompany.run_id.in_(run_ids))
    ).all()

    seen = set()
    result: list[tuple[Company, str]] = []
    for company, run in rows:
        if company.id in seen:
            continue
        if company.email and company.site:
            continue  # нечего дозаливать
        seen.add(company.id)
        theme = session.get(Theme, run.theme_id) if run.theme_id else None
        region = _company_region(company, _theme_region(theme))
        result.append((company, region))
        if limit and len(result) >= limit:
            break
    return result


# ---------------------------------------------------------------------------
# Я.Карты — поиск + извлечение деталей одной компании
# ---------------------------------------------------------------------------


async def _yandex_lookup(context, company_name: str, region: str) -> dict | None:
    """Возвращает {'email': ..., 'site': ..., 'phone': ...} или None."""
    from src.yandex_maps.scraper import enrich_place_details, scrape_list

    try:
        places = await scrape_list(
            context=context, region=region, category=company_name, max_places=3,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("scrape_list failed for %r in %r: %s",
                       company_name, region, e)
        return None

    if not places:
        return None

    # Выбираем самое похожее по имени.
    scored = sorted(
        places,
        key=lambda p: name_similarity(company_name, p.name),
        reverse=True,
    )
    top = scored[0]
    sim = name_similarity(company_name, top.name)
    if sim < MIN_NAME_SIMILARITY:
        logger.info("Низкая похожесть для %r → %r (sim=%.2f), скип",
                    company_name, top.name, sim)
        return None

    # Открываем карточку — phone/email/site/coords.
    try:
        enriched = await enrich_place_details(
            context=context, places=[top], max_details=1,
        )
        top = enriched[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("enrich_place_details failed for %r: %s",
                       company_name, e)
        return None

    return {
        "email": top.email or None,
        "site": top.site or None,
        "phone": top.phone or None,
        "matched_name": top.name,
        "similarity": sim,
    }


# ---------------------------------------------------------------------------
# Email с сайта
# ---------------------------------------------------------------------------


async def _email_from_website(site_url: str, playwright_context) -> str | None:
    """Грузим главную/contacts через website_extractor и ищем email регексом."""
    from src.ai.website_extractor import extract_website_text

    try:
        res = await extract_website_text(
            site_url,
            playwright_context=playwright_context,
            timeout_s=10, max_pages=3, max_chars=20_000,
        )
    except Exception as e:  # noqa: BLE001
        logger.info("website_extract failed for %s: %s", site_url, e)
        return None

    return _extract_email_from_text(res.text)


# ---------------------------------------------------------------------------
# Главный цикл
# ---------------------------------------------------------------------------


async def _process_one(
    company: Company, region: str,
    *, context, scrape_website: bool, apply: bool, session: Session,
) -> dict:
    """Обогащает одну компанию. Возвращает summary-словарь для лога."""
    summary = {
        "company_id": company.id,
        "name": company.name,
        "had_email": bool(company.email),
        "had_site": bool(company.site),
        "yandex_match": None,
        "got_email": False,
        "got_site": False,
        "email_source": None,
    }

    # ── Я.Карты ────────────────────────────────────────────────────────
    yres = await _yandex_lookup(context, company.name, region)
    if yres:
        summary["yandex_match"] = yres["matched_name"]
        if yres["site"] and not company.site:
            company.site = yres["site"]
            summary["got_site"] = True
        if yres["email"] and _looks_like_real_email(yres["email"]) and not company.email:
            company.email = yres["email"]
            summary["got_email"] = True
            summary["email_source"] = "yandex"

    # ── Email с сайта (если site известен и email до сих пор пуст) ────
    if scrape_website and company.site and not company.email:
        email = await _email_from_website(company.site, context)
        if email:
            company.email = email
            summary["got_email"] = True
            summary["email_source"] = "website"

    if apply and (summary["got_email"] or summary["got_site"]):
        session.add(company)
        session.commit()

    return summary


async def _run(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    run_ids = [int(x) for x in args.run_ids.split(",") if x.strip()]
    logger.info("run_ids=%s, limit=%s, apply=%s, website=%s",
                run_ids, args.limit, args.apply, not args.no_website)

    with get_session() as session:
        targets = _load_companies(session, run_ids, args.limit)
        logger.info("К обогащению: %d компаний (email или site пусты)",
                    len(targets))
        if not targets:
            return 0

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1366, "height": 900},
                    locale="ru-RU",
                )

                summaries = []
                for i, (company, region) in enumerate(targets, 1):
                    print(f"[{i}/{len(targets)}] {company.name[:60]} "
                          f"(region={region!r})")
                    s = await _process_one(
                        company, region,
                        context=context,
                        scrape_website=not args.no_website,
                        apply=args.apply,
                        session=session,
                    )
                    summaries.append(s)
                    parts = []
                    if s["yandex_match"]:
                        parts.append(f"matched={s['yandex_match'][:40]!r}")
                    if s["got_site"]:
                        parts.append(f"+site={company.site}")
                    if s["got_email"]:
                        parts.append(f"+email={company.email} ({s['email_source']})")
                    if not parts:
                        parts.append("ничего не нашли")
                    print("   →", " · ".join(parts))

            finally:
                await browser.close()

        # ── Итоги ──────────────────────────────────────────────────────
        print()
        print("=" * 70)
        total = len(summaries)
        with_yandex = sum(1 for s in summaries if s["yandex_match"])
        got_email = sum(1 for s in summaries if s["got_email"])
        got_site = sum(1 for s in summaries if s["got_site"])
        from_yandex = sum(1 for s in summaries
                          if s["email_source"] == "yandex")
        from_site = sum(1 for s in summaries
                        if s["email_source"] == "website")
        print(f"Всего:                       {total}")
        print(f"Нашли компанию на Я.Картах:  {with_yandex}")
        print(f"Дозабили site:               {got_site}")
        print(f"Дозабили email:              {got_email}  "
              f"(Я.Карты: {from_yandex}, сайт: {from_site})")
        if not args.apply:
            print()
            print("(dry-run — повторите с --apply, чтобы реально записать.)")

    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--run-ids", required=True,
        help="ID парс-ранов через запятую, например 14,15,17,19",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Макс. компаний за прогон (для теста). По умолчанию — все.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Реально записать в БД (по умолчанию dry-run).",
    )
    parser.add_argument(
        "--no-website", action="store_true",
        help="Не скрейпить сайт компании для добора email (только Я.Карты).",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
