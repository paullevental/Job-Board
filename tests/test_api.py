"""HTTP contract for the read endpoints.

Driven through FastAPI's TestClient against an in-memory SQLite database, so the
whole API is verifiable with no network and no server process.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from job_poller import db, meta, queries
from job_poller.api.app import create_app
from job_poller.api.deps import get_detail_fetcher, get_session
from job_poller.api.schemas import _description
from job_poller.ats.base import DetailResult
from job_poller.models import Base, Company, PollRun, Posting, PostingDetail, utcnow
from job_poller.tracking import Status, set_status

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    return create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def session(engine):
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s


def _no_network(posting):
    raise AssertionError(
        f"the test suite tried to fetch a live description for posting {posting.id}; "
        "override get_detail_fetcher in the test instead"
    )


@pytest.fixture
def app(session):
    """The app, wired to the test session instead of the real database.

    The description fetcher is stubbed out by default: a test that reaches the
    network is a test that is slow, flaky offline, and rude to a free endpoint,
    so an unintended fetch must fail loudly rather than quietly dial out.
    """
    application = create_app()
    application.dependency_overrides[get_session] = lambda: session
    application.dependency_overrides[get_detail_fetcher] = lambda: _no_network
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def company(session):
    c = Company(name="Acme", ats="greenhouse", board_token="acme", tags="canada")
    session.add(c)
    session.flush()
    return c


def add(
    session,
    company,
    title="Software Engineer Intern",
    location="Toronto, ON",
    *,
    minutes=0,
    category="intern",
    canada=True,
    engineering=True,
    closed=False,
):
    seen = T0 + timedelta(minutes=minutes)
    p = Posting(
        company_id=company.id,
        ats="greenhouse",
        external_id=f"{title}-{location}-{minutes}",
        title=title,
        url="https://boards.example.com/acme/1",
        location=location,
        department="Engineering",
        category=category,
        canada_eligible=canada,
        engineering=engineering,
        season="Summer",
        year=2027,
        first_seen_at=seen,
        last_seen_at=seen,
        closed_at=seen if closed else None,
        fingerprint="f",
        dedupe_key="d",
    )
    session.add(p)
    session.flush()
    return p


# --- skeleton ------------------------------------------------------------


def test_health_endpoint_reports_unknown_on_a_fresh_database(client):
    body = client.get("/api/system/health").json()

    assert body["state"] == "unknown"
    assert body["last_run"] is None


def test_health_endpoint_reports_the_last_run(client, session):
    session.add(
        PollRun(
            started_at=utcnow() - timedelta(seconds=10),
            finished_at=utcnow(),
            boards_attempted=20,
            boards_ok=20,
            matches_new=3,
        )
    )
    session.flush()

    body = client.get("/api/system/health").json()

    assert body["state"] == "green"
    assert body["last_run"]["boards_ok"] == 20
    assert body["last_run"]["matches_new"] == 3


def test_unknown_route_returns_the_error_envelope(client):
    r = client.get("/api/nope")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert "message" in r.json()["error"]


# --- feed ----------------------------------------------------------------


def test_feed_returns_grouped_open_untracked_matches(client, session, company):
    add(session, company, "SWE Intern", "Toronto, ON", minutes=1)
    add(session, company, "SWE Intern", "Vancouver, BC", minutes=2)
    add(session, company, "Data Intern", "Remote", minutes=3)

    body = client.get("/api/feed").json()

    assert [g["title"] for g in body["groups"]] == ["Data Intern", "SWE Intern"]
    swe = next(g for g in body["groups"] if g["title"] == "SWE Intern")
    assert swe["posting_count"] == 2
    assert sorted(swe["locations"]) == ["Toronto, ON", "Vancouver, BC"]
    assert swe["company_name"] == "Acme"


def test_feed_omits_tracked_postings(client, session, company):
    p = add(session, company, "Tracked Intern")
    set_status(session, p.id, Status.SAVED)

    assert client.get("/api/feed").json()["groups"] == []


def test_feed_paginates_by_cursor_without_repeating_a_posting(client, session, company):
    for i in range(5):
        add(session, company, f"Role {i}", minutes=i)

    first = client.get("/api/feed", params={"limit": 2}).json()
    second = client.get("/api/feed", params={"limit": 2, "cursor": first["next_cursor"]}).json()

    assert first["next_cursor"]
    seen_first = {p["id"] for g in first["groups"] for p in g["postings"]}
    seen_second = {p["id"] for g in second["groups"] for p in g["postings"]}
    assert seen_first.isdisjoint(seen_second)


def test_feed_rejects_a_malformed_cursor(client):
    r = client.get("/api/feed", params={"cursor": "not-base64!!"})

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_cursor"


def test_feed_counts_fresh_postings_against_the_seen_marker(client, session, company):
    add(session, company, "Old", minutes=1)
    meta.set_time(session, meta.LAST_FEED_SEEN_AT, T0 + timedelta(minutes=2))
    add(session, company, "New", minutes=3)

    body = client.get("/api/feed").json()

    assert body["fresh_count"] == 1
    assert body["last_seen_at"] is not None


def test_feed_fresh_count_covers_everything_before_the_first_visit(client, session, company):
    add(session, company, "A", minutes=1)
    add(session, company, "B", minutes=2)

    body = client.get("/api/feed").json()

    assert body["fresh_count"] == 2
    assert body["last_seen_at"] is None


def test_feed_honors_the_canada_filter(client, session, company):
    add(session, company, "Canadian", minutes=1, canada=True)
    add(session, company, "American", minutes=2, canada=False)

    body = client.get("/api/feed", params={"canada_only": True}).json()

    assert [g["title"] for g in body["groups"]] == ["Canadian"]


# --- browse --------------------------------------------------------------


def test_browse_includes_closed_and_tracked_postings(client, session, company):
    add(session, company, "Closed Role", minutes=1, closed=True)
    tracked = add(session, company, "Tracked Role", minutes=2)
    set_status(session, tracked.id, Status.APPLIED)

    body = client.get("/api/postings").json()

    assert {p["title"] for p in body["postings"]} == {"Closed Role", "Tracked Role"}


def test_browse_reports_tracked_status_on_each_posting(client, session, company):
    p = add(session, company, "Applied Role")
    set_status(session, p.id, Status.APPLIED)

    (row,) = client.get("/api/postings").json()["postings"]

    assert row["status"] == "applied"
    assert row["application_id"] is not None


def test_browse_reports_untracked_postings_with_a_null_status(client, session, company):
    add(session, company, "Fresh Role")

    (row,) = client.get("/api/postings").json()["postings"]

    assert row["status"] is None
    assert row["application_id"] is None


def test_browse_filters_by_search_term(client, session, company):
    add(session, company, "Backend Intern", minutes=1)
    add(session, company, "Design Intern", minutes=2)

    body = client.get("/api/postings", params={"q": "backend"}).json()

    assert [p["title"] for p in body["postings"]] == ["Backend Intern"]


def test_browse_filters_by_status(client, session, company):
    applied = add(session, company, "Applied", minutes=1)
    add(session, company, "Untouched", minutes=2)
    set_status(session, applied.id, Status.APPLIED)

    body = client.get("/api/postings", params={"status": "applied"}).json()

    assert [p["title"] for p in body["postings"]] == ["Applied"]


def test_browse_filters_for_untracked_postings(client, session, company):
    applied = add(session, company, "Applied", minutes=1)
    add(session, company, "Untouched", minutes=2)
    set_status(session, applied.id, Status.APPLIED)

    body = client.get("/api/postings", params={"status": "untracked"}).json()

    assert [p["title"] for p in body["postings"]] == ["Untouched"]


def test_browse_rejects_an_unknown_status_filter(client):
    r = client.get("/api/postings", params={"status": "banana"})

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_browse_rejects_an_oversized_limit(client):
    r = client.get("/api/postings", params={"limit": 5000})

    assert r.status_code == 422


# --- posting detail ------------------------------------------------------


def test_posting_detail_returns_the_posting_and_its_company(
    client, app, session, company, stub_fetch
):
    app.dependency_overrides[get_detail_fetcher] = lambda: stub_fetch
    p = add(session, company)

    body = client.get(f"/api/postings/{p.id}").json()

    assert body["id"] == p.id
    assert body["company_name"] == "Acme"
    assert body["url"] == "https://boards.example.com/acme/1"
    assert body["season"] == "Summer"
    assert body["year"] == 2027
    assert body["days_open"] >= 0


def test_posting_detail_serves_a_cached_description(client, session, company):
    p = add(session, company)
    session.add(
        PostingDetail(posting_id=p.id, content_html="<p>Build things</p>", content_text="Build things")
    )
    session.flush()

    body = client.get(f"/api/postings/{p.id}").json()

    assert body["description"]["state"] == "ready"
    assert body["description"]["html"] == "<p>Build things</p>"


def test_a_stored_fetch_failure_is_not_permanent(client, app, session, company, stub_fetch):
    """One timeout must not mark a posting unreadable forever: opening it again
    retries, because only a human opening a posting triggers the fetch."""
    app.dependency_overrides[get_detail_fetcher] = lambda: stub_fetch
    p = add(session, company)
    session.add(PostingDetail(posting_id=p.id, fetch_error="timed out"))
    session.flush()

    description = client.get(f"/api/postings/{p.id}").json()["description"]

    assert stub_fetch.calls == [p.id]
    assert description["state"] == "ready"
    assert description["error"] is None


def test_posting_detail_404s_on_an_unknown_id(client):
    r = client.get("/api/postings/4242")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# --- pipeline ------------------------------------------------------------


def test_pipeline_returns_every_status_column_even_when_empty(client):
    body = client.get("/api/applications").json()

    assert [c["status"] for c in body["columns"]] == [s.value for s in Status]
    assert all(c["count"] == 0 for c in body["columns"])


def test_pipeline_places_an_application_in_its_column(client, session, company):
    p = add(session, company, "Applied Role")
    set_status(session, p.id, Status.APPLIED)

    body = client.get("/api/applications").json()
    applied = next(c for c in body["columns"] if c["status"] == "applied")

    assert applied["count"] == 1
    assert applied["applications"][0]["posting"]["title"] == "Applied Role"
    assert applied["applications"][0]["applied_at"] is not None


def test_pipeline_marks_an_application_whose_posting_closed(client, session, company):
    p = add(session, company, "Gone", closed=True)
    set_status(session, p.id, Status.APPLIED)

    body = client.get("/api/applications").json()
    applied = next(c for c in body["columns"] if c["status"] == "applied")

    assert applied["applications"][0]["posting"]["is_open"] is False


def test_pipeline_can_be_filtered_to_one_status(client, session, company):
    saved = add(session, company, "Saved", minutes=1)
    applied = add(session, company, "Applied", minutes=2)
    set_status(session, saved.id, Status.SAVED)
    set_status(session, applied.id, Status.APPLIED)

    body = client.get("/api/applications", params={"status": "saved"}).json()

    assert [c["status"] for c in body["columns"]] == ["saved"]
    assert body["columns"][0]["count"] == 1


# --- application events --------------------------------------------------


def test_application_events_return_the_transition_timeline(client, session, company):
    p = add(session, company)
    app = set_status(session, p.id, Status.SAVED)
    set_status(session, p.id, Status.APPLIED, note="sent resume")

    body = client.get(f"/api/applications/{app.id}/events").json()

    assert [(e["from_status"], e["to_status"]) for e in body["events"]] == [
        (None, "saved"),
        ("saved", "applied"),
    ]
    assert body["events"][1]["note"] == "sent resume"


def test_application_events_404_on_an_unknown_application(client):
    assert client.get("/api/applications/999/events").status_code == 404


# --- companies -----------------------------------------------------------


def test_companies_list_board_health(client, session, company):
    company.consecutive_failures = 4
    company.last_error = "404 Not Found"
    add(session, company)

    (row,) = client.get("/api/companies").json()["companies"]

    assert row["name"] == "Acme"
    assert row["state"] == "failing"
    assert row["last_error"] == "404 Not Found"
    assert row["tags"] == ["canada"]
    assert row["posting_count"] == 1


# --- insights ------------------------------------------------------------


def test_insights_stats_report_coverage(client, session, company):
    add(session, company, "Intern", minutes=1)
    add(session, company, "Staff", minutes=2, category="other")

    body = client.get("/api/insights/stats").json()

    assert body["active_boards"] == 1
    assert body["postings_total"] == 2
    assert body["matches_total"] == 1
    assert body["lifespan"]["closed_count"] == 0
    assert body["lifespan"]["median_days"] is None


def test_insights_timing_is_honest_about_thin_history(client, session, company):
    add(session, company, "Intern")

    body = client.get("/api/insights/timing").json()

    assert body["sufficient_data"] is False
    assert body["companies"][0]["company_name"] == "Acme"
    assert body["companies"][0]["posting_count"] == 1


def test_openapi_schema_is_served(client):
    r = client.get("/openapi.json")

    assert r.status_code == 200
    assert "/api/feed" in r.json()["paths"]


# --- wiring --------------------------------------------------------------


@pytest.fixture
def isolated_engine():
    """init_engine sets module globals; put them back so later tests are unaffected."""
    saved = (db._engine, db._Session)
    yield
    db._engine, db._Session = saved


def test_asgi_module_exposes_a_ready_app(isolated_engine, tmp_path, monkeypatch):
    """`uvicorn job_poller.api.asgi:app` is the documented way to run this.

    Importing the module must leave a working app behind: engine initialized,
    schema created, routes mounted.
    """
    monkeypatch.setenv("JOB_POLLER_DB", str(tmp_path / "wiring.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    asgi = importlib.reload(importlib.import_module("job_poller.api.asgi"))

    with TestClient(asgi.app) as c:
        assert c.get("/api/system/health").json()["state"] == "unknown"


def test_default_session_dependency_reads_the_real_database(isolated_engine, tmp_path):
    """The un-overridden dependency must work, or only the tests would pass."""
    db.init_engine(f"sqlite:///{tmp_path / 'real.db'}")
    with db.session_scope() as s:
        s.add(Company(name="Live", ats="greenhouse", board_token="live", last_ok_at=utcnow()))

    with TestClient(create_app()) as client:
        (row,) = client.get("/api/companies").json()["companies"]

    assert row["name"] == "Live"
    assert row["state"] == "ok"


def test_the_api_default_page_size_follows_the_query_layer(client, session, company):
    """Guards against two page-size constants drifting apart. If the API declared
    its own default, changing the query layer's would silently not take effect."""
    for i in range(queries.DEFAULT_LIMIT + 5):
        add(session, company, f"Role {i}", minutes=i)

    body = client.get("/api/postings").json()

    assert len(body["postings"]) == queries.DEFAULT_LIMIT


# --- description fetch on cache miss (phase 3) ---------------------------


@pytest.fixture
def stub_fetch():
    """Stands in for the live ATS call so the API is testable with no network."""
    calls = []

    def fetch(posting):
        calls.append(posting.id)
        return DetailResult(ok=True, html="<p>Build <strong>things</strong></p>")

    fetch.calls = calls
    return fetch


def test_posting_detail_fetches_the_description_on_a_cache_miss(
    client, app, session, company, stub_fetch
):
    app.dependency_overrides[get_detail_fetcher] = lambda: stub_fetch
    p = add(session, company)

    body = client.get(f"/api/postings/{p.id}").json()

    assert stub_fetch.calls == [p.id]
    assert body["description"]["state"] == "ready"
    assert "<strong>" in body["description"]["html"]


def test_a_fetched_description_is_sanitized_before_it_is_returned(
    client, app, session, company
):
    def hostile(_posting):
        return DetailResult(ok=True, html="<p>Hi</p><script>alert('xss')</script>")

    app.dependency_overrides[get_detail_fetcher] = lambda: hostile
    p = add(session, company)

    description = client.get(f"/api/postings/{p.id}").json()["description"]

    assert "script" not in description["html"]
    assert "alert" not in description["html"]


def test_a_failed_description_fetch_still_returns_the_posting(
    client, app, session, company
):
    """A missing description must never block applying."""
    def broken(_posting):
        return DetailResult(ok=False, error="timed out")

    app.dependency_overrides[get_detail_fetcher] = lambda: broken
    p = add(session, company)

    r = client.get(f"/api/postings/{p.id}")

    assert r.status_code == 200
    assert r.json()["url"] == "https://boards.example.com/acme/1"
    assert r.json()["description"]["state"] == "unavailable"
    assert r.json()["description"]["error"] == "timed out"


def test_a_cached_description_is_not_refetched(client, app, session, company, stub_fetch):
    app.dependency_overrides[get_detail_fetcher] = lambda: stub_fetch
    p = add(session, company)
    session.add(PostingDetail(posting_id=p.id, content_html="<p>Already here</p>"))
    session.flush()

    body = client.get(f"/api/postings/{p.id}").json()

    assert stub_fetch.calls == []
    assert body["description"]["html"] == "<p>Already here</p>"


def test_an_unexpected_failure_still_returns_the_error_envelope(session, company):
    """Nothing may reach the client as a bare text/plain 500: the frontend
    branches on `error.code`, and an internal message must not leak."""
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    def explode(_posting):
        raise RuntimeError("board token hunter2 leaked in this message")

    app.dependency_overrides[get_detail_fetcher] = lambda: explode
    p = add(session, company)

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(f"/api/postings/{p.id}")

    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in r.text


def test_a_posting_with_no_detail_row_reports_not_fetched():
    """The third cache state. Unreachable through the detail route, which always
    fetches first, so it is pinned here rather than left to rot."""
    description = _description(None)

    assert description.state == "not_fetched"
    assert description.html is None


# --- bad input is the caller's error, never ours -------------------------


def test_an_oversized_posting_id_is_a_client_error(client):
    """Beyond 2**63 SQLite raises OverflowError. Reported as internal_error the
    frontend reads it as "we have a bug" rather than "your input was wrong"."""
    r = client.get(f"/api/postings/{2**63}")

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_an_oversized_company_filter_is_a_client_error(client):
    assert client.get("/api/postings", params={"company_id": 2**63}).status_code == 422


def test_an_oversized_year_is_a_client_error(client):
    assert client.get("/api/postings", params={"year": 2**63}).status_code == 422


def test_an_oversized_application_id_is_a_client_error(client):
    assert client.get(f"/api/applications/{2**63}/events").status_code == 422


def test_a_cursor_carrying_an_oversized_id_is_refused_as_a_cursor(client):
    import base64

    raw = f"2|2026-10-13T18:57:35+00:00|2026-10-07T15:23:54+00:00|{2**63}"
    cursor = base64.urlsafe_b64encode(raw.encode()).decode()

    r = client.get("/api/feed", params={"cursor": cursor})

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_cursor"


def test_a_search_wildcard_is_matched_literally(client, session, company):
    """A search box that returns the whole table for "%" is not a search box."""
    add(session, company, "Backend Intern", minutes=1)
    add(session, company, "100% Remote Intern", minutes=2)

    body = client.get("/api/postings", params={"q": "%"}).json()

    assert [p["title"] for p in body["postings"]] == ["100% Remote Intern"]


def test_a_search_underscore_is_matched_literally(client, session, company):
    add(session, company, "Backend Intern", minutes=1)
    add(session, company, "Data_Eng Intern", minutes=2)

    body = client.get("/api/postings", params={"q": "_"}).json()

    assert [p["title"] for p in body["postings"]] == ["Data_Eng Intern"]


def test_an_absurdly_long_search_is_refused_not_crashed(client):
    """SQLite caps LIKE patterns at 50,000 characters and raises past it."""
    r = client.get("/api/postings", params={"q": "x" * 60_000})

    assert r.status_code == 422


def test_the_cursor_error_does_not_echo_the_whole_cursor(client):
    r = client.get("/api/feed", params={"cursor": "z" * 5000})

    assert r.status_code == 422
    assert len(r.json()["error"]["message"]) < 200


def test_a_negative_poll_interval_is_refused(client):
    assert client.get("/api/system/health", params={"expected_interval_s": -1}).status_code == 422


def test_a_posting_exposes_the_date_the_employer_published_it(client, session, company):
    """It is the middle term of the documented sort key; without it a client
    cannot reproduce or verify the order it was handed."""
    p = add(session, company)
    p.ats_posted_at = T0
    session.flush()

    (row,) = client.get("/api/postings").json()["postings"]

    assert row["ats_posted_at"] is not None
