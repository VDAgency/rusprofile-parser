"""SQLAlchemy engine, sessionmaker и helper-ы.

Используем `DATABASE_URL` из конфигурации. Для SQLite вынуждены
включать `check_same_thread=False`, потому что aiogram/aiohttp
работают в нескольких корутинах; стандартный SQLite-драйвер этого
сам не разрешает.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.config import DATABASE_URL, BASE_DIR

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""


def _make_engine():
    is_sqlite = DATABASE_URL.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}

    if is_sqlite:
        # Создаём каталог data/ при первом обращении, чтобы файл лёг
        # туда без ручных команд `mkdir`.
        path = DATABASE_URL.replace("sqlite:///", "", 1)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    eng = create_engine(
        DATABASE_URL,
        echo=False,
        future=True,
        connect_args=connect_args,
    )

    if is_sqlite:
        # SQLite по умолчанию не включает foreign keys — это критично
        # для нашей схемы (run_companies → parse_runs → themes →
        # tenants). Без этой настройки `ON DELETE CASCADE` молча
        # игнорируется.
        @event.listens_for(eng, "connect")
        def _set_sqlite_fk(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def get_session() -> Session:
    """Контекстный менеджер: сессия с автокоммитом по выходу.

    При исключении — rollback, исключение пробрасывается дальше.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Создаёт таблицы по моделям. Используется только в тестах
    (в проде применяем миграции Alembic).
    """
    # Импорт моделей здесь, чтобы избежать циклической зависимости.
    from src.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    logger.info("DB schema initialized: %s", DATABASE_URL)
