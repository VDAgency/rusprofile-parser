"""CLI: выровнять существующих dev-tenant'ов на «вечный Pro».

После добавления DEV_USER_IDS / DEV_USERNAMES в .env прогоняем этот
скрипт один раз, чтобы привести уже созданных tenant'ов (которые
зашли в бот раньше, чем мы их добавили в whitelist) к ожидаемому
состоянию:

- ``tariff_plan = pro``
- ``ai_quota_companies_monthly = 0`` (0 = без лимита)
- ``ai_quota_tokens_monthly = 0``
- ``is_blocked = False``, ``blocked_reason = None``
- ``trial_started_at = trial_expires_at = None``, ``trial_parses_left = 0``
- ``quota_period_start = now()`` если был None
- Если у tenant'а активная Subscription — НЕ трогаем; она просто
  становится «не нужной» (dev-check в helpers её перекроет).

Без аргументов — dry-run (показывает что будет изменено).
``--apply`` — действительно записывает в БД и коммитит.

Пример:

    python scripts/sync_dev_tenants.py             # dry-run
    python scripts/sync_dev_tenants.py --apply     # с коммитом
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_, select

from src import config
from src.db import get_session
from src.db.models import TariffPlan, Tenant

logger = logging.getLogger(__name__)


def _find_dev_tenants(session) -> list[Tenant]:
    """Все tenant'ы, чей telegram_user_id или username (case-insensitive)
    есть в dev-whitelist."""
    conds = []
    if config.DEV_USER_IDS:
        conds.append(Tenant.telegram_user_id.in_(config.DEV_USER_IDS))
    if config.DEV_USERNAMES:
        # SQLite — case-insensitive через func.lower(); SQLAlchemy сам
        # сгенерирует. Сравнение делаем после .strip("@") в Python,
        # потому что в БД username хранится как-есть.
        conds.append(Tenant.username.is_not(None))

    if not conds:
        return []

    rows = list(session.scalars(select(Tenant).where(or_(*conds))).all())
    # Дополнительно отфильтруем по username (для второго условия)
    result = []
    for t in rows:
        if t.telegram_user_id in config.DEV_USER_IDS:
            result.append(t)
            continue
        if t.username:
            uname = t.username.lstrip("@").lower()
            if uname in config.DEV_USERNAMES:
                result.append(t)
    return result


def _planned_changes(t: Tenant) -> dict[str, tuple]:
    """Возвращает {field: (old, new)} для полей, которые надо изменить."""
    now = datetime.now(timezone.utc)
    target = {
        "tariff_plan": TariffPlan.PRO.value,
        "ai_quota_companies_monthly": 0,
        "ai_quota_tokens_monthly": 0,
        "is_blocked": False,
        "blocked_reason": None,
        "trial_started_at": None,
        "trial_expires_at": None,
        "trial_parses_left": 0,
    }
    changes = {}
    for field, new_val in target.items():
        old = getattr(t, field)
        if old != new_val:
            changes[field] = (old, new_val)
    if t.quota_period_start is None:
        changes["quota_period_start"] = (None, now)
    return changes


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Реально записать изменения в БД (по умолчанию — dry-run).",
    )
    args = parser.parse_args()

    if not config.DEV_USER_IDS and not config.DEV_USERNAMES:
        print(
            "DEV_USER_IDS и DEV_USERNAMES пусты в .env. Сначала "
            "заполните хотя бы одно из них.",
            file=sys.stderr,
        )
        return 1

    print(f"DEV_USER_IDS:   {sorted(config.DEV_USER_IDS) or '—'}")
    print(f"DEV_USERNAMES:  {sorted(config.DEV_USERNAMES) or '—'}")
    print()

    with get_session() as session:
        tenants = _find_dev_tenants(session)
        if not tenants:
            print(
                "Ни один из dev-whitelist пока не зарегистрирован в боте. "
                "Это нормально: при первом /start они будут созданы с Pro."
            )
            return 0

        any_change = False
        for t in tenants:
            changes = _planned_changes(t)
            label = f"tenant_id={t.id} uid={t.telegram_user_id} username={t.username!r}"
            if not changes:
                print(f"OK  {label} — уже в нужном состоянии, пропускаем.")
                continue
            any_change = True
            print(f"--- {label} ---")
            for field, (old, new) in changes.items():
                print(f"    {field}: {old!r}  →  {new!r}")
            if args.apply:
                for field, (_old, new) in changes.items():
                    setattr(t, field, new)
                session.add(t)

        if args.apply:
            session.commit()
            print()
            print("✓ Изменения зафиксированы в БД.")
        elif any_change:
            print()
            print("(dry-run — повторите с --apply, чтобы записать.)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
