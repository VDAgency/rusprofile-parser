# Локальная БД (SQLite/PostgreSQL)

Файл `parser.db` — SQLite-файл локальной БД. **В git не коммитим**
(в `.gitignore`). На сервере он живёт в том же месте
(`/opt/rusprofile-parser/data/parser.db`).

Подробное ТЗ и архитектурные решения — `docs/tz_dedup_db.md`.

## Схема (вкратце)

```
tenants     → один пользователь Telegram = один tenant
themes      → уникальные комбинации фильтров (filters_hash)
companies   → все когда-либо найденные компании (по tenant)
parse_runs  → история запусков парсинга
run_companies → many-to-many между запуском и компанией
```

## Миграции (Alembic)

```bash
# Создать новую миграцию по изменениям в моделях
alembic revision --autogenerate -m "описание"

# Применить миграции
alembic upgrade head

# Откатиться на одну вниз
alembic downgrade -1
```

Конфиг — `alembic.ini` + `alembic/env.py`. URL берётся из
`DATABASE_URL` (см. `.env`); по умолчанию
`sqlite:///data/parser.db`.

## Переезд на PostgreSQL (когда понадобится)

1. Создать БД и пользователя в Postgres.
2. В `.env` подменить `DATABASE_URL` на
   `postgresql+psycopg://user:pass@host/db`.
3. Установить драйвер: `pip install psycopg[binary]`.
4. Прогнать `alembic upgrade head` — Alembic увидит пустую БД
   и создаст всё с нуля.
5. (Опционально) перенести существующие данные SQLite → Postgres
   через `pgloader` или ручной dump/load.

Схема рассчитана на оба драйвера: используются стандартные SQL
типы, foreign keys включены через PRAGMA для SQLite.

## Бэкап (на сервере)

```bash
# SQLite
cp /opt/rusprofile-parser/data/parser.db \
   /opt/rusprofile-parser/data/parser-$(date +%F).db.bak

# Postgres (когда переедем)
pg_dump -Fc rusprofile_parser > backup-$(date +%F).dump
```

Можно повесить на cron — раз в сутки, хранить 7 копий.
