"""Diff and lifecycle tests.

`apply_results` is deliberately pure database work with no network, so these
simulate multi-cycle polling by handing it crafted FetchResults.

The phantom-closure test is the one that matters most. Without the rule it
guards, a single network blip would close every posting at a company and then
"reopen" them on the next run, firing a storm of false alerts.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from job_poller.ats.base import FetchResult, RawPosting
from job_poller.models import Base, Company, Posting, PollRun


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture
def company(session):
    c = Company(name="Acme", ats="greenhouse", board_token="acme")
    session.add(c)
    session.flush()
    return c


def posting(ext_id: str, title: str = "Software Engineer Intern", location: str = "Toronto, ON"):
    return RawPosting(
        ats="greenhouse", board_token="acme", external_id=ext_id,
        title=title, url=f"https://example.com/{ext_id}", location=location,
        department="Engineering",
    )


def run_cycle(session, company, postings, ok=True, error=None):
    """Simulate one poll cycle and return the outcome."""
    from job_poller.poller import apply_results

    run = PollRun()
    session.add(run)
    session.flush()
    result = FetchResult("acme", "greenhouse", ok, list(postings), error=error)
    outcome = apply_results(session, [company], [result], run)
    session.flush()
    return outcome


class TestNewPostings:
    def test_first_cycle_records_everything_as_new(self, session, company):
        out = run_cycle(session, company, [posting("1"), posting("2")])
        assert len(out.new_postings) == 2
        assert session.scalar(select(Posting).where(Posting.external_id == "1")) is not None

    def test_second_cycle_finds_nothing_new(self, session, company):
        run_cycle(session, company, [posting("1")])
        out = run_cycle(session, company, [posting("1")])
        assert out.new_postings == []

    def test_only_the_genuinely_new_posting_is_reported(self, session, company):
        run_cycle(session, company, [posting("1")])
        out = run_cycle(session, company, [posting("1"), posting("2")])
        assert len(out.new_postings) == 1
        assert out.new_postings[0].external_id == "2"

    def test_classification_is_stored(self, session, company):
        run_cycle(session, company, [posting("1", "Co-op Developer, Summer 2027")])
        p = session.scalar(select(Posting).where(Posting.external_id == "1"))
        assert p.category == "intern"
        assert p.canada_eligible
        assert p.season == "Summer"
        assert p.year == 2027

    def test_new_matches_excludes_irrelevant_roles(self, session, company):
        out = run_cycle(session, company, [
            posting("1", "Software Engineer Intern"),
            posting("2", "Senior Staff Engineer"),
        ])
        assert len(out.new_postings) == 2
        assert len(out.new_matches) == 1


class TestClosure:
    def test_missing_posting_is_closed(self, session, company):
        run_cycle(session, company, [posting("1"), posting("2")])
        out = run_cycle(session, company, [posting("1")])
        assert len(out.closed_postings) == 1
        p = session.scalar(select(Posting).where(Posting.external_id == "2"))
        assert p.closed_at is not None
        assert not p.is_open

    def test_closed_posting_is_not_closed_twice(self, session, company):
        run_cycle(session, company, [posting("1")])
        run_cycle(session, company, [])
        out = run_cycle(session, company, [])
        assert out.closed_postings == []

    def test_history_is_retained_not_deleted(self, session, company):
        run_cycle(session, company, [posting("1")])
        run_cycle(session, company, [])
        assert session.scalar(select(Posting).where(Posting.external_id == "1")) is not None


class TestPhantomClosureGuard:
    """A failed fetch must never be read as 'this board is now empty'."""

    def test_failed_fetch_does_not_close_postings(self, session, company):
        run_cycle(session, company, [posting("1"), posting("2")])
        out = run_cycle(session, company, [], ok=False, error="ConnectTimeout")

        assert out.closed_postings == []
        assert len(out.failures) == 1
        for ext in ("1", "2"):
            p = session.scalar(select(Posting).where(Posting.external_id == ext))
            assert p.closed_at is None, "a network failure closed a posting"

    def test_failure_increments_board_health_counter(self, session, company):
        run_cycle(session, company, [], ok=False, error="ConnectTimeout")
        run_cycle(session, company, [], ok=False, error="ConnectTimeout")
        assert company.consecutive_failures == 2
        assert company.last_error == "ConnectTimeout"

    def test_success_resets_health_counter(self, session, company):
        run_cycle(session, company, [], ok=False, error="boom")
        assert company.consecutive_failures == 1
        run_cycle(session, company, [posting("1")])
        assert company.consecutive_failures == 0
        assert company.last_error is None
        assert company.last_ok_at is not None

    def test_no_alert_storm_after_recovery(self, session, company):
        """The whole point: a blip must not produce a close-then-reopen cycle."""
        run_cycle(session, company, [posting("1"), posting("2")])
        run_cycle(session, company, [], ok=False, error="ConnectTimeout")
        out = run_cycle(session, company, [posting("1"), posting("2")])
        assert out.new_postings == [], "recovery after a failure re-alerted existing postings"


class TestReposts:
    def test_reopened_posting_counts_as_new(self, session, company):
        run_cycle(session, company, [posting("1")])
        run_cycle(session, company, [])  # closed
        out = run_cycle(session, company, [posting("1")])  # back
        assert len(out.new_postings) == 1
        p = session.scalar(select(Posting).where(Posting.external_id == "1"))
        assert p.closed_at is None
        assert p.notified_at is None, "repost must be eligible for a fresh alert"


class TestEdits:
    def test_title_change_is_detected(self, session, company):
        run_cycle(session, company, [posting("1", "SWE Intern")])
        out = run_cycle(session, company, [posting("1", "SWE Intern, Summer 2027")])
        assert len(out.changed_postings) == 1
        p = session.scalar(select(Posting).where(Posting.external_id == "1"))
        assert p.title == "SWE Intern, Summer 2027"
        assert p.year == 2027

    def test_unchanged_posting_is_not_flagged(self, session, company):
        run_cycle(session, company, [posting("1")])
        out = run_cycle(session, company, [posting("1")])
        assert out.changed_postings == []

    def test_last_seen_advances(self, session, company):
        run_cycle(session, company, [posting("1")])
        p = session.scalar(select(Posting).where(Posting.external_id == "1"))
        first = p.last_seen_at
        run_cycle(session, company, [posting("1")])
        session.refresh(p)
        assert p.last_seen_at >= first


class TestRunAudit:
    def test_run_counters_are_recorded(self, session, company):
        from job_poller.poller import apply_results

        run = PollRun()
        session.add(run)
        session.flush()
        apply_results(session, [company], [
            FetchResult("acme", "greenhouse", True, [posting("1"), posting("2", "Senior Engineer")])
        ], run)

        assert run.boards_attempted == 1
        assert run.boards_ok == 1
        assert run.boards_failed == 0
        assert run.postings_seen == 2
        assert run.postings_new == 2
        assert run.matches_new == 1
        assert run.finished_at is not None
