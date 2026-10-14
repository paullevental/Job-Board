"""Description sanitization.

Descriptions are employer-authored HTML that ends up rendered in the user's
browser, which makes this the one genuine security boundary in the project.
Only the sanitized form is ever stored, so these tests guard what reaches the
database as much as what reaches the page.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from job_poller import details
from job_poller.ats.base import DetailResult
from job_poller.models import Base, Company, Posting, PostingDetail

# --- what must survive ---------------------------------------------------


@pytest.mark.parametrize(
    ("html", "tag"),
    [
        ("<p>We are hiring.</p>", "p"),
        ("<h2>About the role</h2>", "h2"),
        ("<ul><li>Ship features</li></ul>", "ul"),
        ("<ol><li>Apply</li></ol>", "ol"),
        ("<strong>Required</strong>", "strong"),
        ("<em>nice to have</em>", "em"),
        ("line<br>break", "br"),
    ],
)
def test_structural_markup_survives(html, tag):
    """A description with its structure stripped is a wall of text."""
    cleaned = details.sanitize(html)

    assert f"<{tag}" in cleaned


def test_a_link_keeps_its_destination():
    cleaned = details.sanitize('<a href="https://example.com/apply">Apply</a>')

    assert 'href="https://example.com/apply"' in cleaned
    assert "Apply" in cleaned


# --- what must not survive -----------------------------------------------


def test_a_script_tag_and_its_contents_are_removed():
    cleaned = details.sanitize("<p>Before</p><script>alert('xss')</script><p>After</p>")

    assert "script" not in cleaned
    assert "alert" not in cleaned          # the payload itself, not just the tag
    assert "Before" in cleaned and "After" in cleaned


def test_an_event_handler_attribute_is_stripped():
    cleaned = details.sanitize("<p onerror=\"alert('xss')\" onclick=\"steal()\">Hi</p>")

    assert "onerror" not in cleaned
    assert "onclick" not in cleaned
    assert "alert" not in cleaned


def test_a_javascript_href_is_dropped():
    cleaned = details.sanitize("<a href=\"javascript:alert('xss')\">Click</a>")

    assert "javascript" not in cleaned.lower()


def test_an_iframe_is_removed():
    cleaned = details.sanitize('<p>Role</p><iframe src="https://evil.test"></iframe>')

    assert "iframe" not in cleaned
    assert "evil.test" not in cleaned


def test_an_image_payload_is_removed():
    cleaned = details.sanitize("<img src=x onerror=alert('xss')>")

    assert "onerror" not in cleaned
    assert "alert" not in cleaned


def test_styling_attributes_are_stripped():
    """Employer CSS must not leak into our layout, and style is an XSS vector."""
    cleaned = details.sanitize(
        '<p class="jd" id="x" style="position:fixed;top:0">Text</p>'
    )

    assert "class" not in cleaned
    assert "style" not in cleaned
    assert "id=" not in cleaned
    assert "Text" in cleaned


def test_a_form_cannot_phish_the_user():
    cleaned = details.sanitize(
        '<form action="https://evil.test"><input name="password"></form>'
    )

    assert "form" not in cleaned
    assert "input" not in cleaned


# --- link hardening ------------------------------------------------------


def test_links_open_safely_in_a_new_tab():
    cleaned = details.sanitize('<a href="https://example.com">Apply</a>')

    assert 'target="_blank"' in cleaned
    assert "noopener" in cleaned
    assert "noreferrer" in cleaned


# --- plain text ----------------------------------------------------------


def test_to_text_strips_markup_for_search():
    text = details.to_text("<h2>About</h2><p>Build <strong>things</strong>.</p>")

    assert "<" not in text
    assert "About" in text
    assert "Build" in text
    assert "things" in text


def test_to_text_decodes_entities():
    text = details.to_text("<p>R&amp;D &lt;team&gt;</p>")

    assert "R&D" in text
    assert "<team>" in text


def test_to_text_separates_block_elements():
    """Without separation, '<p>A</p><p>B</p>' searches as the single word 'AB'."""
    text = details.to_text("<p>Toronto</p><p>Canada</p>")

    assert "TorontoCanada" not in text
    assert "Toronto" in text and "Canada" in text


# --- idempotence ---------------------------------------------------------


def test_sanitizing_twice_changes_nothing():
    """Stored HTML is already clean; re-cleaning on read must be a no-op."""
    once = details.sanitize('<p>Hi <a href="https://x.test">link</a></p>')

    assert details.sanitize(once) == once


# --- the cache -----------------------------------------------------------


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture
def posting(session):
    company = Company(name="Acme", ats="greenhouse", board_token="acme")
    session.add(company)
    session.flush()
    p = Posting(
        company_id=company.id, ats="greenhouse", external_id="42", title="Intern",
        url="https://x.test/42", category="intern", fingerprint="f", dedupe_key="d",
    )
    session.add(p)
    session.flush()
    return p


def ok(html):
    """A stub adapter fetch that succeeds."""
    calls = []

    def fetch(_posting):
        calls.append(_posting.external_id)
        return DetailResult(ok=True, html=html)

    fetch.calls = calls
    return fetch


def fails(error="timed out"):
    calls = []

    def fetch(_posting):
        calls.append(_posting.external_id)
        return DetailResult(ok=False, error=error)

    fetch.calls = calls
    return fetch


def test_a_cache_hit_does_not_refetch(session, posting):
    session.add(PostingDetail(posting_id=posting.id, content_html="<p>Cached</p>"))
    session.flush()
    fetch = ok("<p>Fresh</p>")

    detail = details.get_or_fetch(session, posting, fetch=fetch)

    assert detail.content_html == "<p>Cached</p>"
    assert fetch.calls == []


def test_a_miss_fetches_and_caches(session, posting):
    fetch = ok("<p>Build <strong>things</strong></p>")

    detail = details.get_or_fetch(session, posting, fetch=fetch)

    assert fetch.calls == ["42"]
    assert "<strong>" in detail.content_html
    assert "Build" in detail.content_text
    assert detail.fetch_error is None
    assert session.get(PostingDetail, posting.id) is not None


def test_only_sanitized_html_is_ever_persisted(session, posting):
    """The invariant: nothing unsanitized reaches the database, so a later bug
    in the rendering layer cannot resurrect a payload."""
    details.get_or_fetch(session, posting, fetch=ok("<p>Hi</p><script>alert('xss')</script>"))

    stored = session.get(PostingDetail, posting.id)
    assert "script" not in stored.content_html
    assert "alert" not in stored.content_html


def test_a_failed_fetch_is_recorded_not_raised(session, posting):
    detail = details.get_or_fetch(session, posting, fetch=fails("404 Not Found"))

    assert detail.fetch_error == "404 Not Found"
    assert detail.content_html == ""


def test_a_failure_is_retried_the_next_time_the_user_opens_it(session, posting):
    """A transient timeout must not mark a posting permanently unreadable. The
    retry is safe because only a human opening a posting triggers it."""
    details.get_or_fetch(session, posting, fetch=fails())
    fetch = ok("<p>Worked this time</p>")

    detail = details.get_or_fetch(session, posting, fetch=fetch)

    assert fetch.calls == ["42"]
    assert "Worked this time" in detail.content_html
    assert detail.fetch_error is None


def test_a_successful_fetch_is_never_refetched(session, posting):
    details.get_or_fetch(session, posting, fetch=ok("<p>Once</p>"))
    fetch = ok("<p>Twice</p>")

    detail = details.get_or_fetch(session, posting, fetch=fetch)

    assert fetch.calls == []
    assert "Once" in detail.content_html


def test_an_empty_description_is_not_cached_as_a_silent_success(session, posting):
    """`ok=True` with no body would otherwise store an empty row with no error:
    permanently 'unavailable' and never retried, with nothing to show the user."""
    detail = details.get_or_fetch(session, posting, fetch=ok(""))

    assert detail.fetch_error


def test_a_body_that_sanitizes_away_counts_as_empty(session, posting):
    """An image-only description survives the fetch but not the allowlist."""
    detail = details.get_or_fetch(session, posting, fetch=ok('<img src="x.png">'))

    assert detail.fetch_error


def test_an_empty_description_is_retried_on_the_next_open(session, posting):
    details.get_or_fetch(session, posting, fetch=ok(""))
    fetch = ok("<p>Published later</p>")

    detail = details.get_or_fetch(session, posting, fetch=fetch)

    assert fetch.calls == ["42"]
    assert "Published later" in detail.content_html


def _fail_only_writes(session, monkeypatch, error):
    """Fail the cache INSERT, not the harmless autoflushes around it.

    A locked database only rejects statements that actually write, so failing
    every flush would simulate a situation that cannot occur and would prove the
    wrong thing.
    """
    real_flush = session.flush

    def flush(*args, **kwargs):
        if session.new:
            raise error
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", flush)


def test_a_locked_database_does_not_break_the_page(session, posting, monkeypatch):
    """SQLite has one writer. While the poller commits a cold start -- thousands
    of rows -- a user opening a posting must still get the description, just
    uncached. The cache is an optimization; it may never fail the read."""
    _fail_only_writes(session, monkeypatch, OperationalError("INSERT", {}, Exception("locked")))

    detail = details.get_or_fetch(session, posting, fetch=ok("<p>Still readable</p>"))

    assert "Still readable" in detail.content_html
    assert detail.fetch_error is None


def test_losing_a_race_to_cache_does_not_break_the_page(session, posting, monkeypatch):
    """Two first-opens of the same posting collide on the primary key. The loser
    still fetched a perfectly good description; it just does not get to store it."""
    _fail_only_writes(session, monkeypatch, IntegrityError("INSERT", {}, Exception("UNIQUE")))

    detail = details.get_or_fetch(session, posting, fetch=ok("<p>Fetched anyway</p>"))

    assert "Fetched anyway" in detail.content_html


def test_an_unknown_ats_is_reported_not_raised(session, posting):
    """`fetch_via_adapter` promises never to raise. A posting left behind by a
    removed adapter must degrade like any other unreachable board."""
    posting.ats = "workday"

    result = details.fetch_via_adapter(posting)

    assert not result.ok
    assert result.error


def test_to_text_separates_table_and_definition_cells():
    """Same reason as paragraphs: fused cells are unsearchable."""
    text = details.to_text(
        "<table><tr><td>Toronto</td><td>Canada</td></tr></table>"
        "<dl><dt>Term</dt><dd>Summer 2027</dd></dl>"
    )

    assert "TorontoCanada" not in text
    assert "TermSummer" not in text


def test_to_text_normalizes_non_breaking_spaces():
    """Vendors emit &nbsp; freely; a search for "Toronto ON" must still match."""
    text = details.to_text("<p>Toronto&nbsp;ON</p>")

    assert " " not in text
    assert "Toronto ON" in text
