"""Pytest fixtures: изолированная in-memory SQLite БД для тестов БД."""

import os
import pytest


@pytest.fixture(scope="function")
def db_session(monkeypatch, tmp_path):
    """Свежая БД в файле tmp на каждый тест.

    SQLite in-memory не годится: SessionLocal/engine глобальные, и
    разные сессии видят разные «in-memory». Файл во временном
    каталоге pytest безопасен и удаляется автоматически.
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")

    # Перезагружаем модули, чтобы они подхватили подменённый URL.
    import importlib
    import src.config as cfg
    import src.db.session as ses
    importlib.reload(cfg)
    importlib.reload(ses)
    import src.db.models as mdl
    importlib.reload(mdl)
    import src.db.dedup as ddp
    importlib.reload(ddp)

    ses.Base.metadata.create_all(bind=ses.engine)

    sess = ses.SessionLocal()
    try:
        yield sess
    finally:
        sess.close()
        ses.engine.dispose()
