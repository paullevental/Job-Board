"""The triage feed: open, matching, untriaged postings, newest first."""

from __future__ import annotations

from fastapi import APIRouter

from ... import meta, queries
from ...queries import DEFAULT_LIMIT
from ..deps import LimitDep, SessionDep
from ..errors import cursor_errors
from ..schemas import FeedGroupOut, FeedOut

router = APIRouter(prefix="/api/feed", tags=["feed"])


@router.get("", response_model=FeedOut)
def get_feed(
    session: SessionDep,
    cursor: str | None = None,
    limit: LimitDep = DEFAULT_LIMIT,
    canada_only: bool = False,
    engineering_only: bool = False,
) -> FeedOut:
    # The marker is read, never written, on a plain page load: if opening the
    # dashboard advanced it, the "new since your last visit" count would vanish
    # before the user had read it.
    last_seen_at = meta.get_time(session, meta.LAST_FEED_SEEN_AT)

    with cursor_errors():
        page = queries.feed(
            session,
            limit=limit,
            cursor=cursor,
            canada_only=canada_only,
            engineering_only=engineering_only,
            since=last_seen_at,
        )

    return FeedOut(
        groups=[FeedGroupOut.from_group(g) for g in page.groups],
        next_cursor=page.next_cursor,
        fresh_count=page.fresh_count,
        last_seen_at=last_seen_at,
    )
