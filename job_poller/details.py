"""Job descriptions: fetched on demand, sanitized, then cached permanently.

Descriptions are deliberately absent from the polling path. Greenhouse's
`content=true` inflates the payload roughly 12x, and a poll reads every board
every few minutes for text nobody has asked to read yet. So a description is
fetched the first time the user opens a posting, and never again.

Everything here treats the fetched HTML as hostile: it is written by employers,
rendered in the user's browser, and is the one genuine security boundary in
this project. Only sanitized HTML is ever persisted.
"""

from __future__ import annotations

import asyncio
import html as html_module
import logging
import re
from collections.abc import Callable

import httpx
import nh3
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .ats import get_adapter
from .ats.base import REQUEST_HEADERS, DetailResult, describe_error
from .models import Posting, PostingDetail, utcnow

log = logging.getLogger("job_poller.details")

# One request while the user watches a skeleton. Long enough for a slow board,
# short enough that the UI can fall back to the ATS link instead of hanging.
DETAIL_TIMEOUT_S = 6.0

# The spec's allowlist: headings, paragraphs, lists, emphasis, links, breaks.
# `b`/`i` are included because job descriptions use them constantly as synonyms
# of strong/em, and dropping them loses emphasis for no security gain.
ALLOWED_TAGS = {
    "p", "br",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "strong", "em", "b", "i",
    "a",
}

# href is the only attribute that survives, on the only tag that may carry one.
ALLOWED_ATTRIBUTES = {"a": {"href"}}

# Dropping the tag but keeping its text is right for layout tags and wrong for
# these: the "text" of a script is the payload.
STRIP_WITH_CONTENT = {"script", "style", "noscript", "template", "svg", "math"}

# Anything else -- notably `javascript:` and `data:` -- loses the attribute.
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}

_BLOCK_END = re.compile(
    r"(?i)</(?:p|div|li|h[1-6]|tr|td|th|dd|dt|ul|ol|blockquote|section)>|<br\s*/?>"
)
_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


def sanitize(raw_html: str) -> str:
    """Return HTML safe to render. The only form we ever store."""
    return nh3.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        clean_content_tags=STRIP_WITH_CONTENT,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
        # An employer's link opening in the same tab would lose the user's place
        # mid-triage; `noopener` keeps the new tab from reaching back into ours.
        set_tag_attribute_values={"a": {"target": "_blank"}},
    )


def to_text(raw_html: str) -> str:
    """Plain text for search. Block boundaries become newlines, so two adjacent
    paragraphs do not fuse into one unsearchable word."""
    spaced = _BLOCK_END.sub("\n", raw_html)
    stripped = nh3.clean(spaced, tags=set(), attributes={}, clean_content_tags=STRIP_WITH_CONTENT)
    # Vendors emit &nbsp; freely; left as U+00A0 a search for "Toronto ON" misses.
    text = html_module.unescape(stripped).replace("\u00a0", " ")
    text = _TRAILING_SPACE.sub("\n", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def get_or_fetch(
    session: Session,
    posting: Posting,
    fetch: Callable[[Posting], DetailResult],
) -> PostingDetail:
    """The cached description, fetching it once if we do not have one.

    A successful fetch is permanent: the poller never refreshes it, so opening a
    posting costs at most one request, ever. A *failed* fetch is retried the next
    time the user opens the posting -- a transient timeout must not mark a role
    permanently unreadable, and since only a human opening a posting triggers
    this, the retry rate is self-limiting.
    """
    cached = session.get(PostingDetail, posting.id)
    if cached is not None and cached.fetch_error is None:
        return cached

    result = fetch(posting)

    # A body that is empty, or that sanitizes away to nothing, is not a success
    # with no text -- it is a description we do not have. Storing it as a success
    # would cache "unavailable, reason unknown" forever and never retry.
    clean = sanitize(result.html) if result.ok else ""

    if clean.strip():
        values = {
            "content_html": clean,
            # Derived from the sanitized HTML, never the raw: whatever the
            # allowlist rejected must not reappear in a second field.
            "content_text": to_text(clean),
            "fetch_error": None,
        }
    else:
        values = {
            "content_html": "",
            "content_text": "",
            "fetch_error": result.error or "no description published",
        }

    detail = cached or PostingDetail(posting_id=posting.id)
    for field, value in values.items():
        setattr(detail, field, value)
    detail.fetched_at = utcnow()

    if _try_cache(session, detail, is_new=cached is None):
        return detail

    # The write was rolled back, so the ORM instance no longer carries what we
    # just fetched -- returning it would serve the stale row the rollback
    # restored. Hand back a detached carrier of the real values instead.
    return PostingDetail(posting_id=posting.id, fetched_at=utcnow(), **values)


def _try_cache(session: Session, detail: PostingDetail, *, is_new: bool) -> bool:
    """Persist the description, or give up quietly. True when it landed.

    Two ways this legitimately fails, and neither is the user's problem: the
    poller holds SQLite's single write lock mid-commit, or two first-opens of the
    same posting race and the loser hits the primary key. In both cases we
    already have the description in hand -- serving it uncached beats failing a
    page that would otherwise have worked.
    """
    # Captured before the flush: afterwards the instance may be expired or
    # detached, and reading an attribute off it raises a different exception
    # than the two caught here -- which is how this guard was escaped before.
    posting_id = detail.posting_id
    try:
        if is_new:
            session.add(detail)
        session.flush()
        return True
    except (OperationalError, IntegrityError) as exc:
        # Roll back first. Touching the session before this raises instead.
        session.rollback()
        log.warning("could not cache description for posting %s: %s", posting_id, exc)
        return False


def fetch_via_adapter(posting: Posting) -> DetailResult:
    """Fetch one description from the live board.

    Synchronous on purpose -- the caller is a request handler with a user
    waiting. Every failure is caught and returned as a `DetailResult`, never
    raised: the same contract the poller already uses for board fetches, for the
    same reason, which is that one unreachable board must not break the page.
    """
    async def run() -> DetailResult:
        adapter = get_adapter(posting.ats)
        async with httpx.AsyncClient(
            timeout=DETAIL_TIMEOUT_S,
            follow_redirects=True,
            headers=REQUEST_HEADERS,
        ) as client:
            return await adapter.fetch_detail(
                client, posting.company.board_token, posting.external_id
            )

    try:
        return asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 -- a dead board must not break the page
        log.warning(
            "detail fetch failed for %s:%s — %s", posting.ats, posting.external_id, exc
        )
        return DetailResult(ok=False, error=describe_error(exc))
