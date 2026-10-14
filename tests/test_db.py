from __future__ import annotations

import pytest
from sqlalchemy import text

from job_poller import db


@pytest.fixture
def isolated_engine():
    """init_engine sets module globals; put them back afterwards."""
    saved = (db._engine, db._Session)
    yield
    db._engine, db._Session = saved


def pragma(engine, name: str):
    with engine.connect() as conn:
        return conn.execute(text(f"PRAGMA {name}")).scalar()


def test_busy_timeout_exceeds_the_pysqlite_default(isolated_engine, tmp_path):
    engine = db.init_engine(f"sqlite:///{tmp_path}/a.db")

    # pysqlite already supplies 5s, so anything at or below that changes nothing.
    assert pragma(engine, "busy_timeout") > 5000


def test_wal_is_enabled(isolated_engine, tmp_path):
    engine = db.init_engine(f"sqlite:///{tmp_path}/b.db")

    assert pragma(engine, "journal_mode") == "wal"


def test_foreign_keys_are_enforced(isolated_engine, tmp_path):
    engine = db.init_engine(f"sqlite:///{tmp_path}/c.db")

    assert pragma(engine, "foreign_keys") == 1


def test_the_new_tables_are_created(isolated_engine, tmp_path):
    engine = db.init_engine(f"sqlite:///{tmp_path}/d.db")

    with engine.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }

    assert {"applications", "application_events", "posting_details", "app_meta"} <= names


def test_the_db_path_env_var_is_read_when_the_engine_is_created(
    isolated_engine, tmp_path, monkeypatch
):
    """Resolved per call, not at import: a server or test that sets the variable
    after importing `db` must still get the database it asked for."""
    monkeypatch.setenv("JOB_POLLER_DB", str(tmp_path / "chosen.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    engine = db.init_engine()

    assert "chosen.db" in str(engine.url)


def test_the_pre_rename_db_env_var_still_works(isolated_engine, tmp_path, monkeypatch):
    monkeypatch.delenv("JOB_POLLER_DB", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("COOPWATCH_DB", str(tmp_path / "legacy.db"))

    assert "legacy.db" in str(db.init_engine().url)
