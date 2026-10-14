"""Read queries for the dashboard and the CLI.

Both surfaces call these so the query logic exists in exactly one place.
Nothing here imports FastAPI; it returns ORM objects and plain dataclasses.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .models import Application, Company, PollRun, Posting, utcnow
from .tracking import Status

DEFAULT_LIMIT = 25
MATCH_CATEGORIES = ("intern", "new_grad")
UNTRACKED = "untracked"

# A board that has failed this many times in a row has probably moved or been
# renamed, rather than being briefly unreachable.
FAILING_AFTER = 3
# Past this, an empty feed means the poller died rather than nothing being posted.
DEAD_AFTER_S = 3600


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
    categories: Sequence[str] | None = None,
    canada_only: bool = False,
    engineering_only: bool = False,
    remote_only: bool = False,
    open_only: bool = False,
    company_id: int | None = None,
    tag: str | None = None,
    season: str | None = None,
    year: int | None = None,
    q: str | None = None,
    status: str | None = None,
) -> PostingPage:
    """Everything, filtered. Unlike the feed this includes tracked and closed roles."""
    # `application` is eager-loaded because every serialized row reports its
    # tracked status; lazily it would be one extra query per posting.
    stmt = select(Posting).options(
        selectinload(Posting.company), selectinload(Posting.application)
    )

    if categories:
        stmt = stmt.where(Posting.category.in_(categories))
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
    if tag:
        # Tags are a comma-joined string on Company, so this is a substring match:
        # fine for the handful of curated tags in companies.yml, and the reason a
        # tag must not be a prefix of another one.
        stmt = stmt.where(
            Posting.company_id.in_(select(Company.id).where(Company.tags.contains(tag)))
        )
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


# --- insights and health -------------------------------------------------
#
# These answer the three questions the dashboard header and /insights ask:
# how much have we seen, when does each company post, and is the poller alive.
# They lived inline in `cli.py` until the API needed them too.


@dataclass(frozen=True)
class Lifespan:
    """How long postings stay up. Measured over closed postings only — an open
    posting's age is a lower bound, not a lifespan, and averaging the two lies."""

    closed_count: int
    median_days: float | None
    shortest_days: float | None
    longest_days: float | None


@dataclass(frozen=True)
class Stats:
    active_boards: int
    postings_total: int
    postings_open: int
    matches_total: int
    matches_canada: int
    lifespan: Lifespan
    recent_runs: list[PollRun]


@dataclass(frozen=True)
class CompanyTiming:
    """When one company actually opens internship roles."""

    company_id: int
    company_name: str
    posting_count: int
    first_seen_at: datetime
    latest_seen_at: datetime
    median_days_open: float | None


@dataclass(frozen=True)
class BoardHealth:
    company_id: int
    name: str
    ats: str
    board_token: str
    active: bool
    tags: list[str]
    state: str
    last_ok_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    posting_count: int
    open_count: int


@dataclass(frozen=True)
class SystemHealth:
    """Whether the poller is alive. An empty feed beside a red light is
    unambiguous; an empty feed alone is not."""

    state: str
    last_run: PollRun | None
    stale_seconds: float | None
    failing_boards: int


RECENT_RUN_LIMIT = 5


def stats(session: Session, *, recent_limit: int = RECENT_RUN_LIMIT) -> Stats:
    """Coverage, posting lifespan, and the last few poll runs."""

    def count(*where) -> int:
        return session.scalar(select(func.count()).select_from(Posting).where(*where)) or 0

    return Stats(
        active_boards=session.scalar(
            select(func.count()).select_from(Company).where(Company.active.is_(True))
        )
        or 0,
        postings_total=count(),
        postings_open=count(Posting.closed_at.is_(None)),
        matches_total=count(Posting.category.in_(MATCH_CATEGORIES)),
        matches_canada=count(
            Posting.category.in_(MATCH_CATEGORIES), Posting.canada_eligible.is_(True)
        ),
        lifespan=_lifespan(
            session.scalars(select(Posting).where(Posting.closed_at.is_not(None))).all()
        ),
        recent_runs=list(
            session.scalars(
                select(PollRun).order_by(PollRun.started_at.desc(), PollRun.id.desc()).limit(recent_limit)
            )
        ),
    )


def timing(session: Session) -> list[CompanyTiming]:
    """Internship posting history per company, busiest company first.

    Only says something useful after a full co-op cycle of data; the caller is
    responsible for being honest about that rather than drawing a confident chart.
    """
    rows = session.scalars(
        select(Posting)
        .options(selectinload(Posting.company))
        .where(Posting.category == "intern")
        .order_by(Posting.first_seen_at)
    ).all()

    by_company: dict[int, list[Posting]] = {}
    for p in rows:
        by_company.setdefault(p.company_id, []).append(p)

    out = [
        CompanyTiming(
            company_id=company_id,
            company_name=posts[0].company.name,
            posting_count=len(posts),
            first_seen_at=posts[0].first_seen_at,
            latest_seen_at=posts[-1].first_seen_at,
            median_days_open=_lifespan([p for p in posts if p.closed_at]).median_days,
        )
        for company_id, posts in by_company.items()
    ]
    out.sort(key=lambda t: (-t.posting_count, t.company_name))
    return out


def company_health(session: Session) -> list[BoardHealth]:
    """Board-by-board health. Broken boards sort first — they are the ones that
    need the manual token fix, and a healthy board needs no attention."""
    counts = dict(
        session.execute(
            select(Posting.company_id, func.count()).group_by(Posting.company_id)
        ).all()
    )
    open_counts = dict(
        session.execute(
            select(Posting.company_id, func.count())
            .where(Posting.closed_at.is_(None))
            .group_by(Posting.company_id)
        ).all()
    )

    out = [
        BoardHealth(
            company_id=c.id,
            name=c.name,
            ats=c.ats,
            board_token=c.board_token,
            active=c.active,
            tags=[t for t in (c.tags or "").split(",") if t],
            state=_board_state(c),
            last_ok_at=c.last_ok_at,
            last_error=c.last_error,
            consecutive_failures=c.consecutive_failures,
            posting_count=counts.get(c.id, 0),
            open_count=open_counts.get(c.id, 0),
        )
        for c in session.scalars(select(Company))
    ]
    out.sort(key=lambda h: (-h.consecutive_failures, h.name))
    return out


def system_health(
    session: Session, *, expected_interval_s: int = 300
) -> SystemHealth:
    """Derive poller liveness from the last `PollRun`.

    green  — finished within two poll intervals
    amber  — stale, but ran within the hour
    red    — nothing in an hour, or most boards failed on the last pass
    unknown— never polled, which is first-run onboarding, not a failure
    """
    last_run = session.scalar(
        select(PollRun).order_by(PollRun.started_at.desc(), PollRun.id.desc()).limit(1)
    )
    failing = (
        session.scalar(
            select(func.count())
            .select_from(Company)
            .where(Company.consecutive_failures >= FAILING_AFTER)
        )
        or 0
    )

    if last_run is None:
        return SystemHealth(state="unknown", last_run=None, stale_seconds=None, failing_boards=failing)

    # An unfinished run is judged by when it started: a pass that began an hour
    # ago and never finished is a hung poller, not a healthy one.
    reference = last_run.finished_at or last_run.started_at
    stale_s = (utcnow() - reference).total_seconds()

    attempted = last_run.boards_attempted
    mostly_failed = attempted > 0 and last_run.boards_failed * 2 > attempted

    if mostly_failed or stale_s > DEAD_AFTER_S:
        state = "red"
    elif stale_s > expected_interval_s * 2:
        state = "amber"
    else:
        state = "green"

    return SystemHealth(
        state=state, last_run=last_run, stale_seconds=stale_s, failing_boards=failing
    )


def get_posting(session: Session, posting_id: int) -> Posting | None:
    """One posting with everything the detail view renders."""
    return session.scalar(
        select(Posting)
        .options(
            selectinload(Posting.company),
            selectinload(Posting.detail),
            selectinload(Posting.application),
        )
        .where(Posting.id == posting_id)
    )


def get_application_by_id(session: Session, application_id: int) -> Application | None:
    """One application with its posting and its full event timeline."""
    return session.scalar(
        select(Application)
        .options(
            selectinload(Application.posting).selectinload(Posting.company),
            selectinload(Application.events),
        )
        .where(Application.id == application_id)
    )


def _board_state(company: Company) -> str:
    if company.consecutive_failures >= FAILING_AFTER:
        return "failing"
    if company.consecutive_failures > 0:
        return "degraded"
    if company.last_ok_at is None:
        return "never_polled"
    return "ok"


def _lifespan(closed: list[Posting]) -> Lifespan:
    if not closed:
        return Lifespan(closed_count=0, median_days=None, shortest_days=None, longest_days=None)
    spans = sorted(p.days_open for p in closed)
    return Lifespan(
        closed_count=len(spans),
        median_days=spans[len(spans) // 2],
        shortest_days=spans[0],
        longest_days=spans[-1],
    )


@dataclass(frozen=True)
class TimingReport:
    """Timing rows plus how much history they rest on.

    `sufficient_data` exists so the UI can be honest. Three weeks of data makes
    a chart that looks confident and says nothing; the insights route must be
    able to tell the difference.
    """

    companies: list[CompanyTiming]
    history_days: float
    sufficient_data: bool


# A full co-op cycle. Below this, timing is noise.
SUFFICIENT_HISTORY_DAYS = 180


def timing_report(session: Session) -> TimingReport:
    rows = timing(session)
    if not rows:
        return TimingReport(companies=[], history_days=0.0, sufficient_data=False)

    earliest = min(r.first_seen_at for r in rows)
    latest = max(r.latest_seen_at for r in rows)
    history_days = (latest - earliest).total_seconds() / 86400
    # A long span of postings that never closed still says nothing about how
    # long a posting stays up, which is half of what timing is for.
    has_lifespans = any(r.median_days_open is not None for r in rows)

    return TimingReport(
        companies=rows,
        history_days=history_days,
        sufficient_data=history_days >= SUFFICIENT_HISTORY_DAYS and has_lifespans,
    )
