"""Browse and posting detail."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ... import details, queries
from ...queries import DEFAULT_LIMIT
from ..deps import MAX_SEARCH_LEN, DbIdPath, DbIdQuery, FetcherDep, LimitDep, SessionDep
from ..errors import cursor_errors, not_found
from ..schemas import Category, PostingDetailOut, PostingOut, PostingsOut, StatusFilter

router = APIRouter(prefix="/api/postings", tags=["postings"])

# SQLite caps LIKE patterns at 50,000 characters and raises past it; 200 is
# far more than a job title needs.
SearchQuery = Annotated[str | None, Query(max_length=MAX_SEARCH_LEN)]


@router.get("", response_model=PostingsOut)
def browse(
    session: SessionDep,
    cursor: str | None = None,
    limit: LimitDep = DEFAULT_LIMIT,
    category: Category | None = None,
    canada_only: bool = False,
    engineering_only: bool = False,
    remote: bool = False,
    open_only: bool = False,
    company_id: DbIdQuery = None,
    season: str | None = None,
    year: DbIdQuery = None,
    q: SearchQuery = None,
    tag: str | None = None,
    status: StatusFilter | None = None,
) -> PostingsOut:
    """Everything, filtered. Unlike the feed this includes tracked and closed roles."""
    with cursor_errors():
        page = queries.browse(
            session,
            limit=limit,
            cursor=cursor,
            categories=(category.value,) if category else None,
            canada_only=canada_only,
            engineering_only=engineering_only,
            remote_only=remote,
            open_only=open_only,
            company_id=company_id,
            tag=tag,
            season=season,
            year=year,
            q=q,
            status=status.value if status else None,
        )

    return PostingsOut(
        postings=[PostingOut.from_posting(p) for p in page.postings],
        next_cursor=page.next_cursor,
    )


@router.get("/{posting_id}", response_model=PostingDetailOut)
def detail(posting_id: DbIdPath, session: SessionDep, fetch: FetcherDep) -> PostingDetailOut:
    """One posting, with its description fetched on the first open and cached.

    The fetch is synchronous: it is one HTTP GET while the user watches a
    skeleton, and a background job plus client polling would add machinery for
    no perceived gain. A failure degrades to metadata plus an ATS link rather
    than failing the request -- a missing description must never block applying.
    """
    posting = queries.get_posting(session, posting_id)
    if posting is None:
        raise not_found("posting", posting_id)

    detail = details.get_or_fetch(session, posting, fetch=fetch)
    return PostingDetailOut.with_description(posting, detail)
