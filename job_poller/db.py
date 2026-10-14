"""Database session management. Set DATABASE_URL to use Postgres instead of SQLite."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DEFAULT_DB_NAME = "job_poller.db"


def default_db_path() -> Path:
    """Resolved on every call, not once at import.

    COOPWATCH_DB is the pre-rename name, still read so an existing local
    database is not orphaned.
    """
    return Path(
        os.environ.get("JOB_POLLER_DB")
        or os.environ.get("COOPWATCH_DB")
        or DEFAULT_DB_NAME
    ).expanduser()

BUSY_TIMEOUT_MS = 15_000

_engine = None
_Session: sessionmaker | None = None


def init_engine(url: str | None = None, echo: bool = False):
    """Create the engine and ensure the schema exists."""
    global _engine, _Session
    url = url or os.environ.get("DATABASE_URL") or f"sqlite:///{default_db_path()}"
    _engine = create_engine(url, echo=echo, future=True)

    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            # WAL lets a reader see stats while the watcher is mid-write.
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            # Raises pysqlite's 5s default. A cold-start poll commits thousands
            # of rows at once and can hold the write lock for longer than that.
            cur.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            cur.close()

    Base.metadata.create_all(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


@contextmanager
def session_scope() -> Session:
    """Transactional session. Commits on success, rolls back on failure."""
    if _Session is None:
        init_engine()
    assert _Session is not None
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
