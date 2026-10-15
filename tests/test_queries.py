from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from job_poller import queries
from job_poller.models import Base, Company, Posting
from job_poller.tracking import Status, set_status

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture
def company(session):
    c = Company(name="Acme", ats="greenhouse", board_token="acme", tags="canada,toronto")
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
        url="https://example.com/x",
        location=location,
        category=category,
        canada_eligible=canada,
        engineering=engineering,
        first_seen_at=seen,
        last_seen_at=seen,
        closed_at=seen if closed else None,
        fingerprint="f",
        dedupe_key="d",
    )
    session.add(p)
    session.flush()
    return p


def titles(page):
    return [g.title for g in page.groups]


def test_feed_returns_open_matching_untracked_postings(session, company):
    add(session, company, "Software Engineer Intern")

    page = queries.feed(session)

    assert titles(page) == ["Software Engineer Intern"]


def test_feed_excludes_closed_postings(session, company):
    add(session, company, "Closed Role", closed=True)

    assert queries.feed(session).groups == []


def test_feed_excludes_non_matching_categories(session, company):
    add(session, company, "Staff Engineer", category="other")

    assert queries.feed(session).groups == []


def test_feed_excludes_a_posting_once_it_is_tracked(session, company):
    p = add(session, company, "Software Engineer Intern")
    set_status(session, p.id, Status.SAVED)

    assert queries.feed(session).groups == []


def test_feed_groups_one_title_across_locations(session, company):
    add(session, company, "SWE Intern", "Toronto, ON", minutes=1)
    add(session, company, "SWE Intern", "Vancouver, BC", minutes=2)
    add(session, company, "SWE Intern", "Montreal, QC", minutes=3)

    page = queries.feed(session)

    assert len(page.groups) == 1
    assert page.groups[0].posting_count == 3
    assert sorted(page.groups[0].locations) == ["Montreal, QC", "Toronto, ON", "Vancouver, BC"]


def test_feed_keeps_different_titles_apart(session, company):
    add(session, company, "Backend Intern", minutes=1)
    add(session, company, "Frontend Intern", minutes=2)

    assert len(queries.feed(session).groups) == 2


def test_feed_orders_newest_first(session, company):
    add(session, company, "Older", minutes=1)
    add(session, company, "Newer", minutes=9)

    assert titles(queries.feed(session)) == ["Newer", "Older"]


def test_feed_respects_canada_only(session, company):
    add(session, company, "Canadian", canada=True, minutes=1)
    add(session, company, "Elsewhere", canada=False, minutes=2)

    assert titles(queries.feed(session, canada_only=True)) == ["Canadian"]


def test_feed_respects_engineering_only(session, company):
    add(session, company, "Engineer", engineering=True, minutes=1)
    add(session, company, "Recruiter", engineering=False, minutes=2)

    assert titles(queries.feed(session, engineering_only=True)) == ["Engineer"]


def test_feed_paginates_with_a_cursor(session, company):
    for i in range(1, 6):
        add(session, company, f"Role {i}", minutes=i)

    first = queries.feed(session, limit=2)
    second = queries.feed(session, limit=2, cursor=first.next_cursor)

    assert titles(first) == ["Role 5", "Role 4"]
    assert titles(second) == ["Role 3", "Role 2"]


def test_the_last_page_has_no_cursor(session, company):
    add(session, company, "Only", minutes=1)

    assert queries.feed(session, limit=2).next_cursor is None


def test_a_posting_arriving_mid_scroll_does_not_shift_later_pages(session, company):
    for i in range(1, 6):
        add(session, company, f"Role {i}", minutes=i)
    first = queries.feed(session, limit=2)

    add(session, company, "Just Landed", minutes=99)
    second = queries.feed(session, limit=2, cursor=first.next_cursor)

    assert titles(second) == ["Role 3", "Role 2"]


def test_fresh_count_counts_only_postings_newer_than_the_marker(session, company):
    add(session, company, "Old", minutes=1)
    add(session, company, "New A", minutes=10)
    add(session, company, "New B", minutes=11)

    page = queries.feed(session, since=T0 + timedelta(minutes=5))

    assert page.fresh_count == 2


def test_fresh_count_is_everything_when_there_is_no_marker(session, company):
    add(session, company, "A", minutes=1)
    add(session, company, "B", minutes=2)

    assert queries.feed(session, since=None).fresh_count == 2


def test_browse_can_include_closed_and_tracked_postings(session, company):
    p = add(session, company, "Tracked", minutes=1)
    set_status(session, p.id, Status.APPLIED)
    add(session, company, "Closed", minutes=2, closed=True)

    found = {x.title for x in queries.browse(session).postings}

    assert found == {"Tracked", "Closed"}


def test_browse_can_restrict_to_open_postings(session, company):
    add(session, company, "Open", minutes=1)
    add(session, company, "Closed", minutes=2, closed=True)

    page = queries.browse(session, open_only=True)

    assert [p.title for p in page.postings] == ["Open"]


def test_browse_searches_titles(session, company):
    add(session, company, "Machine Learning Intern", minutes=1)
    add(session, company, "Backend Intern", minutes=2)

    page = queries.browse(session, q="machine")

    assert [p.title for p in page.postings] == ["Machine Learning Intern"]


def test_browse_filters_by_tracked_status(session, company):
    applied = add(session, company, "Applied", minutes=1)
    set_status(session, applied.id, Status.APPLIED)
    add(session, company, "Untouched", minutes=2)

    page = queries.browse(session, status=Status.APPLIED)

    assert [p.title for p in page.postings] == ["Applied"]


def test_pipeline_groups_applications_by_status(session, company):
    a = add(session, company, "A", minutes=1)
    b = add(session, company, "B", minutes=2)
    c = add(session, company, "C", minutes=3)
    set_status(session, a.id, Status.APPLIED)
    set_status(session, b.id, Status.APPLIED)
    set_status(session, c.id, Status.OFFER)

    by_status = queries.pipeline(session)

    assert len(by_status[Status.APPLIED]) == 2
    assert len(by_status[Status.OFFER]) == 1
    assert by_status[Status.REJECTED] == []


def test_cursor_round_trips(session, company):
    p = add(session, company, "Role", minutes=4)
    posted = T0 - timedelta(days=3)

    encoded = queries.encode_cursor(p.first_seen_at, posted, p.id)

    assert queries.decode_cursor(encoded) == (p.first_seen_at, posted, p.id)


def test_a_cursor_from_an_older_sort_key_is_refused(session):
    """A browser left open across a deploy holds a two-part cursor. Reading it as
    if it were the new shape would page from the wrong place in silence."""
    import base64

    stale = base64.urlsafe_b64encode(b"2026-10-01T12:00:00+00:00|7").decode()

    with pytest.raises(ValueError, match="cursor"):
        queries.decode_cursor(stale)


def test_a_malformed_cursor_is_refused(session):
    with pytest.raises(ValueError, match="cursor"):
        queries.decode_cursor("not-a-real-cursor")


def test_browse_filters_by_company_tag(session, company):
    other = Company(name="Beta", ats="lever", board_token="beta", tags="usa")
    session.add(other)
    session.flush()
    add(session, company, "Canadian Role", minutes=1)
    add(session, other, "American Role", minutes=2)

    page = queries.browse(session, tag="canada")

    assert [p.title for p in page.postings] == ["Canadian Role"]


def test_browse_filters_to_several_categories(session, company):
    add(session, company, "Intern Role", category="intern", minutes=1)
    add(session, company, "New Grad Role", category="new_grad", minutes=2)
    add(session, company, "Staff Role", category="other", minutes=3)

    page = queries.browse(session, categories=queries.MATCH_CATEGORIES)

    assert [p.title for p in page.postings] == ["New Grad Role", "Intern Role"]


def test_browse_applies_the_category_filter_before_the_limit(session, company):
    """Filtering after the limit silently drops rows: three newer non-matches
    would consume the whole page and the real match would never appear."""
    add(session, company, "Intern Role", category="intern", minutes=1)
    for i in range(3):
        add(session, company, f"Staff Role {i}", category="other", minutes=10 + i)

    page = queries.browse(session, categories=("intern",), limit=3)

    assert [p.title for p in page.postings] == ["Intern Role"]


def test_feed_breaks_first_seen_ties_by_when_the_employer_posted(session, company):
    """On a cold start every posting shares one first_seen_at, so that column
    carries no information and the employer's own date is the real signal."""
    # Inserted oldest-published FIRST, so insertion order and publication order
    # disagree. Without the tiebreaker the id-descending fallback gets it backwards.
    newer = add(session, company, "Posted in October", minutes=0)
    older = add(session, company, "Posted in March", minutes=0)
    newer.ats_posted_at = T0 - timedelta(days=2)
    older.ats_posted_at = T0 - timedelta(days=200)
    session.flush()

    page = queries.feed(session)

    assert [g.title for g in page.groups] == ["Posted in October", "Posted in March"]


def test_feed_still_puts_newly_seen_postings_first(session, company):
    """The tiebreaker must not override detection order: a posting found just now
    outranks one the employer published later but we saw earlier."""
    old_find = add(session, company, "Seen last week", minutes=0)
    old_find.ats_posted_at = T0 + timedelta(days=5)
    fresh_find = add(session, company, "Seen just now", minutes=60)
    fresh_find.ats_posted_at = T0 - timedelta(days=30)
    session.flush()

    page = queries.feed(session)

    assert [g.title for g in page.groups] == ["Seen just now", "Seen last week"]


def test_feed_handles_a_missing_posted_date(session, company):
    """ats_posted_at is nullable; a null must not sort a posting out of the feed."""
    add(session, company, "No date", minutes=0)

    assert [g.title for g in queries.feed(session).groups] == ["No date"]


def test_the_cursor_survives_the_three_part_sort_key(session, company):
    for i in range(6):
        p = add(session, company, f"Role {i}", minutes=0)
        p.ats_posted_at = T0 - timedelta(days=i)
    session.flush()

    first = queries.feed(session, limit=2)
    second = queries.feed(session, limit=2, cursor=first.next_cursor)

    seen_first = {p.id for g in first.groups for p in g.postings}
    seen_second = {p.id for g in second.groups for p in g.postings}
    assert seen_first and seen_second
    assert seen_first.isdisjoint(seen_second)
