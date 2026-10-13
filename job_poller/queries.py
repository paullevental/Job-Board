"""Read queries for the dashboard and the CLI.

Both surfaces call these so the query logic exists in exactly one place.
Nothing here imports FastAPI; it returns ORM objects and plain dataclasses.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .models import Application, Posting
from .tracking import Status

DEFAULT_LIMIT = 25
MATCH_CATEGORIES = ("intern", "new_grad")
UNTRACKED = "untracked"


@dataclass(frozen=True)
class FeedGroup:
    """One role, which may be posted in several locations at once."""

    company_id: int
    company_name: str
    title: str
    postings: list[Posting]

    @property
    def posting_count(self) -> int:
        return len(self.postings)

    @property
    def locations(self) -> list[str]:
        out: list[str] = []
        for p in self.postings:
            if p.location and p.location not in out:
                out.append(p.location)
        return out

    @property
    def newest(self) -> Posting:
        return self.postings[0]

    @property
    def first_seen_at(self) -> datetime:
        return self.newest.first_seen_at


@dataclass(frozen=True)
class FeedPage:
    groups: list[FeedGroup]
    next_cursor: str | None
    fresh_count: int


@dataclass(frozen=True)
class PostingPage:
    postings: list[Posting]
    next_cursor: str | None


def feed(
    session: Session,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    canada_only: bool = False,
    engineering_only: bool = False,
    since: datetime | None = None,
) -> FeedPage:
    """Open, matching, untriaged postings, newest first, grouped by role."""
    conditions = [
        Posting.closed_at.is_(None),
        Posting.category.in_(MATCH_CATEGORIES),
        ~Posting.id.in_(select(Application.posting_id)),
    ]
    if canada_only:
        conditions.append(Posting.canada_eligible.is_(True))
    if engineering_only:
        conditions.append(Posting.engineering.is_(True))

    fresh = select(func.count()).select_from(Posting).where(*conditions)
    if since is not None:
        fresh = fresh.where(Posting.first_seen_at > since)
    fresh_count = session.scalar(fresh) or 0

    stmt = select(Posting).options(selectinload(Posting.company)).where(*conditions)
    if cursor is not None:
        stmt = stmt.where(_after(cursor))

    rows, next_cursor = _page(session, stmt, limit)
    return FeedPage(groups=_group(rows), next_cursor=next_cursor, fresh_count=fresh_count)


def browse(
    session: Session,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    category: str | None = None,
    canada_only: bool = False,
    engineering_only: bool = False,
    remote_only: bool = False,
    open_only: bool = False,
    company_id: int | None = None,
    season: str | None = None,
    year: int | None = None,
    q: str | None = None,
    status: str | None = None,
) -> PostingPage:
    """Everything, filtered. Unlike the feed this includes tracked and closed roles."""
    stmt = select(Posting).options(selectinload(Posting.company))

    if category:
        stmt = stmt.where(Posting.category == category)
    if canada_only:
        stmt = stmt.where(Posting.canada_eligible.is_(True))
    if engineering_only:
        stmt = stmt.where(Posting.engineering.is_(True))
    if remote_only:
        stmt = stmt.where(Posting.is_remote.is_(True))
    if open_only:
        stmt = stmt.where(Posting.closed_at.is_(None))
    if company_id is not None:
        stmt = stmt.where(Posting.company_id == company_id)
    if season:
        stmt = stmt.where(Posting.season == season)
    if year is not None:
        stmt = stmt.where(Posting.year == year)
    if q:
        stmt = stmt.where(Posting.title.ilike(f"%{q}%"))
    if status == UNTRACKED:
        stmt = stmt.where(~Posting.id.in_(select(Application.posting_id)))
    elif status is not None:
        tracked = select(Application.posting_id).where(Application.status == status)
        stmt = stmt.where(Posting.id.in_(tracked))
    if cursor is not None:
        stmt = stmt.where(_after(cursor))

    postings, next_cursor = _page(session, stmt, limit)
    return PostingPage(postings=postings, next_cursor=next_cursor)


def pipeline(session: Session) -> dict[Status, list[Application]]:
    """Every tracked application, bucketed by status. Empty buckets are present."""
    rows = session.scalars(
        select(Application)
        .options(selectinload(Application.posting).selectinload(Posting.company))
        .order_by(Application.updated_at.desc(), Application.id.desc())
    ).all()

    buckets: dict[Status, list[Application]] = {s: [] for s in Status}
    for app in rows:
        buckets[Status(app.status)].append(app)
    return buckets


def encode_cursor(first_seen_at: datetime, posting_id: int) -> str:
    raw = f"{first_seen_at.isoformat()}|{posting_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        iso, _, posting_id = raw.rpartition("|")
        parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed, int(posting_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed cursor {cursor!r}") from exc


def _after(cursor: str):
    """Keyset predicate. Offset paging would duplicate rows as new postings arrive."""
    seen_at, posting_id = decode_cursor(cursor)
    return or_(
        Posting.first_seen_at < seen_at,
        and_(Posting.first_seen_at == seen_at, Posting.id < posting_id),
    )


def _page(session: Session, stmt, limit: int) -> tuple[list[Posting], str | None]:
    stmt = stmt.order_by(Posting.first_seen_at.desc(), Posting.id.desc()).limit(limit + 1)
    rows = list(session.scalars(stmt))

    if len(rows) <= limit:
        return rows, None
    rows = rows[:limit]
    return rows, encode_cursor(rows[-1].first_seen_at, rows[-1].id)


def _group(postings: list[Posting]) -> list[FeedGroup]:
    grouped: dict[tuple[int, str], list[Posting]] = {}
    for p in postings:
        grouped.setdefault((p.company_id, p.title), []).append(p)

    return [
        FeedGroup(
            company_id=company_id,
            company_name=rows[0].company.name,
            title=title,
            postings=rows,
        )
        for (company_id, title), rows in grouped.items()
    ]
