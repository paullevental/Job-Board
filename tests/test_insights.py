"""Stats, timing and health queries — the read side the dashboard header and
the /insights route need, and which until now lived inline in `cli.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from job_poller import queries
from job_poller.models import Base, Company, PollRun, Posting

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture
def company(session):
    c = Company(name="Acme", ats="greenhouse", board_token="acme", tags="canada,fintech")
    session.add(c)
    session.flush()
    return c


def add(
    session,
    company,
    title="Software Engineer Intern",
    *,
    minutes=0,
    category="intern",
    canada=True,
    open_days=None,
):
    seen = T0 + timedelta(minutes=minutes)
    p = Posting(
        company_id=company.id,
        ats="greenhouse",
        external_id=f"{title}-{minutes}",
        title=title,
        url="https://example.com/x",
        location="Toronto, ON",
        category=category,
        canada_eligible=canada,
        first_seen_at=seen,
        last_seen_at=seen,
        closed_at=seen + timedelta(days=open_days) if open_days is not None else None,
        fingerprint="f",
        dedupe_key="d",
    )
    session.add(p)
    session.flush()
    return p


def run(session, *, minutes_ago=1, attempted=10, ok=10, failed=0, finished=True):
    started = queries.utcnow() - timedelta(minutes=minutes_ago)
    r = PollRun(
        started_at=started,
        finished_at=started + timedelta(seconds=4) if finished else None,
        boards_attempted=attempted,
        boards_ok=ok,
        boards_failed=failed,
    )
    session.add(r)
    session.flush()
    return r


# --- stats ---------------------------------------------------------------


def test_stats_counts_coverage(session, company):
    add(session, company, "Intern A", minutes=1)
    add(session, company, "Intern B", minutes=2, open_days=3)
    add(session, company, "Staff Engineer", minutes=3, category="other")
    add(session, company, "Intern US", minutes=4, canada=False)

    s = queries.stats(session)

    assert s.active_boards == 1
    assert s.postings_total == 4
    assert s.postings_open == 3
    assert s.matches_total == 3
    assert s.matches_canada == 2


def test_stats_lifespan_is_measured_over_closed_postings_only(session, company):
    add(session, company, "A", minutes=1, open_days=2)
    add(session, company, "B", minutes=2, open_days=6)
    add(session, company, "C", minutes=3, open_days=10)
    add(session, company, "Still open", minutes=4)

    life = queries.stats(session).lifespan

    assert life.closed_count == 3
    assert life.median_days == pytest.approx(6, abs=0.01)
    assert life.shortest_days == pytest.approx(2, abs=0.01)
    assert life.longest_days == pytest.approx(10, abs=0.01)


def test_stats_lifespan_is_empty_with_no_closed_postings(session, company):
    add(session, company, "Still open")

    life = queries.stats(session).lifespan

    assert life.closed_count == 0
    assert life.median_days is None


def test_stats_returns_recent_runs_newest_first(session, company):
    run(session, minutes_ago=30)
    run(session, minutes_ago=5)

    recent = queries.stats(session).recent_runs

    assert len(recent) == 2
    assert recent[0].started_at > recent[1].started_at


# --- timing --------------------------------------------------------------


def test_timing_summarizes_intern_postings_per_company(session, company):
    add(session, company, "A", minutes=1, open_days=4)
    add(session, company, "B", minutes=2, open_days=8)
    add(session, company, "C", minutes=3)

    rows = queries.timing(session)

    assert len(rows) == 1
    row = rows[0]
    assert row.company_name == "Acme"
    assert row.posting_count == 3
    assert row.first_seen_at == T0 + timedelta(minutes=1)
    assert row.latest_seen_at == T0 + timedelta(minutes=3)
    assert row.median_days_open == pytest.approx(8, abs=0.01)


def test_timing_ignores_non_intern_categories(session, company):
    add(session, company, "New grad role", category="new_grad")
    add(session, company, "Staff", minutes=1, category="other")

    assert queries.timing(session) == []


def test_timing_orders_companies_by_posting_count(session, company):
    other = Company(name="Beta", ats="lever", board_token="beta")
    session.add(other)
    session.flush()
    add(session, company, "A", minutes=1)
    add(session, other, "B", minutes=2)
    add(session, other, "C", minutes=3)

    assert [r.company_name for r in queries.timing(session)] == ["Beta", "Acme"]


# --- company health -----------------------------------------------------


def test_company_health_reports_a_never_polled_board(session, company):
    (health,) = queries.company_health(session)

    assert health.company_id == company.id
    assert health.state == "never_polled"
    assert health.tags == ["canada", "fintech"]
    assert health.posting_count == 0


def test_company_health_counts_open_postings(session, company):
    company.last_ok_at = queries.utcnow()
    add(session, company, "A", minutes=1)
    add(session, company, "B", minutes=2, open_days=1)

    (health,) = queries.company_health(session)

    assert health.state == "ok"
    assert health.posting_count == 2
    assert health.open_count == 1


def test_company_health_flags_repeated_failures(session, company):
    company.consecutive_failures = 3
    company.last_error = "404 Not Found"

    (health,) = queries.company_health(session)

    assert health.state == "failing"
    assert health.last_error == "404 Not Found"


def test_company_health_marks_one_off_failures_degraded(session, company):
    company.last_ok_at = queries.utcnow()
    company.consecutive_failures = 1

    assert queries.company_health(session)[0].state == "degraded"


def test_company_health_sorts_broken_boards_first(session, company):
    healthy = Company(name="Zeta", ats="lever", board_token="zeta", last_ok_at=queries.utcnow())
    broken = Company(name="Beta", ats="ashby", board_token="beta", consecutive_failures=9)
    session.add_all([healthy, broken])
    session.flush()

    assert [h.name for h in queries.company_health(session)][0] == "Beta"


# --- system health ------------------------------------------------------


def test_system_health_is_unknown_before_the_first_poll(session):
    health = queries.system_health(session)

    assert health.state == "unknown"
    assert health.last_run is None


def test_system_health_is_green_right_after_a_run(session):
    run(session, minutes_ago=1)

    health = queries.system_health(session)

    assert health.state == "green"
    assert health.stale_seconds < 120


def test_system_health_is_amber_past_two_poll_intervals(session):
    run(session, minutes_ago=20)

    assert queries.system_health(session, expected_interval_s=300).state == "amber"


def test_system_health_is_red_when_no_run_within_an_hour(session):
    run(session, minutes_ago=90)

    assert queries.system_health(session).state == "red"


def test_system_health_is_red_when_most_boards_failed(session):
    run(session, minutes_ago=1, attempted=10, ok=4, failed=6)

    assert queries.system_health(session).state == "red"


def test_system_health_counts_failing_boards(session, company):
    company.consecutive_failures = 4
    run(session, minutes_ago=1)

    assert queries.system_health(session).failing_boards == 1


def test_system_health_treats_an_unfinished_run_by_its_start(session):
    run(session, minutes_ago=90, finished=False)

    assert queries.system_health(session).state == "red"


# --- single posting ------------------------------------------------------


def test_get_posting_returns_the_posting_with_its_company(session, company):
    p = add(session, company)

    found = queries.get_posting(session, p.id)

    assert found.id == p.id
    assert found.company.name == "Acme"


def test_get_posting_returns_none_for_an_unknown_id(session):
    assert queries.get_posting(session, 999) is None
