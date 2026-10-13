from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from job_poller import meta
from job_poller.models import Base


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s


def test_a_missing_key_returns_the_default(session):
    assert meta.get(session, "nothing-here", default="fallback") == "fallback"


def test_a_value_round_trips(session):
    meta.set(session, "mood", "optimistic")

    assert meta.get(session, "mood") == "optimistic"


def test_setting_an_existing_key_replaces_it(session):
    meta.set(session, "mood", "optimistic")
    meta.set(session, "mood", "realistic")

    assert meta.get(session, "mood") == "realistic"


def test_a_timestamp_round_trips_as_tz_aware_utc(session):
    when = datetime(2026, 10, 13, 18, 30, tzinfo=timezone.utc)

    meta.set_time(session, "last_feed_seen_at", when)

    assert meta.get_time(session, "last_feed_seen_at") == when


def test_a_missing_timestamp_is_none(session):
    assert meta.get_time(session, "never-set") is None
