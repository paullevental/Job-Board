"""Response models. ORM objects are never returned directly.

The `from_*` constructors are explicit on purpose: the frontend's contract is
these field names, so a rename in `models.py` should break a test here rather
than silently change the API.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from .. import queries
from ..models import Application, ApplicationEvent, PollRun, Posting, PostingDetail
from ..tracking import Status

# Derived from Status so a new status cannot be filterable in one place and not
# the other. `untracked` is not a status -- it means "no application row".
StatusFilter = StrEnum(
    "StatusFilter",
    {s.name: s.value for s in Status} | {"UNTRACKED": queries.UNTRACKED},
)

Category = StrEnum("Category", {"INTERN": "intern", "NEW_GRAD": "new_grad", "OTHER": "other"})


class DescriptionOut(BaseModel):
    """Job description cache state.

    `not_fetched` and `unavailable` are different: the first is "ask again", the
    second is "we asked and the board refused". The UI falls back to the
    metadata plus an ATS link in both cases -- a missing description never
    blocks applying.
    """

    state: Literal["ready", "not_fetched", "unavailable"]
    html: str | None = None
    text: str | None = None
    fetched_at: datetime | None = None
    error: str | None = None


class PostingOut(BaseModel):
    id: int
    company_id: int
    company_name: str
    ats: str
    external_id: str
    title: str
    url: str
    location: str
    department: str
    category: str
    canada_eligible: bool
    is_remote: bool
    engineering: bool
    season: str | None
    year: int | None
    first_seen_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None
    # The middle term of the feed's sort key. Without it a client cannot
    # reproduce or verify the order it was handed.
    ats_posted_at: datetime | None
    is_open: bool
    days_open: float
    status: str | None
    application_id: int | None

    @classmethod
    def from_posting(cls, posting: Posting, *, application: Application | None = None) -> PostingOut:
        app = application if application is not None else posting.application
        return cls(
            id=posting.id,
            company_id=posting.company_id,
            company_name=posting.company.name,
            ats=posting.ats,
            external_id=posting.external_id,
            title=posting.title,
            url=posting.url,
            location=posting.location,
            department=posting.department,
            category=posting.category,
            canada_eligible=posting.canada_eligible,
            is_remote=posting.is_remote,
            engineering=posting.engineering,
            season=posting.season,
            year=posting.year,
            first_seen_at=posting.first_seen_at,
            last_seen_at=posting.last_seen_at,
            closed_at=posting.closed_at,
            ats_posted_at=posting.ats_posted_at,
            is_open=posting.is_open,
            days_open=round(posting.days_open, 3),
            status=app.status if app else None,
            application_id=app.id if app else None,
        )


class PostingDetailOut(PostingOut):
    description: DescriptionOut

    @classmethod
    def with_description(
        cls, posting: Posting, detail: PostingDetail | None, **kwargs
    ) -> PostingDetailOut:
        """The detail is passed in rather than read off `posting.detail`, because
        a description can be freshly fetched and deliberately not cached."""
        base = PostingOut.from_posting(posting, **kwargs)
        return cls(**base.model_dump(), description=_description(detail))


class FeedGroupOut(BaseModel):
    """One role, possibly open in several locations.

    Grouping is presentation only: each posting keeps its own id because the
    user applies to one specific location, not to a group.
    """

    company_id: int
    company_name: str
    title: str
    posting_count: int
    locations: list[str]
    first_seen_at: datetime
    postings: list[PostingOut]

    @classmethod
    def from_group(cls, group: queries.FeedGroup) -> FeedGroupOut:
        return cls(
            company_id=group.company_id,
            company_name=group.company_name,
            title=group.title,
            posting_count=group.posting_count,
            locations=group.locations,
            first_seen_at=group.first_seen_at,
            postings=[PostingOut.from_posting(p) for p in group.postings],
        )


class FeedOut(BaseModel):
    groups: list[FeedGroupOut]
    next_cursor: str | None
    fresh_count: int
    last_seen_at: datetime | None


class PostingsOut(BaseModel):
    postings: list[PostingOut]
    next_cursor: str | None


class ApplicationOut(BaseModel):
    id: int
    posting_id: int
    status: str
    applied_at: datetime | None
    next_action_at: datetime | None
    notes: str
    created_at: datetime
    updated_at: datetime
    posting: PostingOut

    @classmethod
    def from_application(cls, app: Application) -> ApplicationOut:
        return cls(
            id=app.id,
            posting_id=app.posting_id,
            status=app.status,
            applied_at=app.applied_at,
            next_action_at=app.next_action_at,
            notes=app.notes,
            created_at=app.created_at,
            updated_at=app.updated_at,
            posting=PostingOut.from_posting(app.posting, application=app),
        )


class PipelineColumnOut(BaseModel):
    status: str
    count: int
    applications: list[ApplicationOut]


class PipelineOut(BaseModel):
    columns: list[PipelineColumnOut]


class EventOut(BaseModel):
    id: int
    from_status: str | None
    to_status: str
    note: str | None
    occurred_at: datetime

    @classmethod
    def from_event(cls, event: ApplicationEvent) -> EventOut:
        return cls(
            id=event.id,
            from_status=event.from_status,
            to_status=event.to_status,
            note=event.note,
            occurred_at=event.occurred_at,
        )


class EventsOut(BaseModel):
    events: list[EventOut]


class BoardHealthOut(BaseModel):
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

    @classmethod
    def from_health(cls, health: queries.BoardHealth) -> BoardHealthOut:
        return cls(**health.__dict__)


class CompaniesOut(BaseModel):
    companies: list[BoardHealthOut]


class PollRunOut(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    duration_s: float | None
    boards_attempted: int
    boards_ok: int
    boards_failed: int
    postings_seen: int
    postings_new: int
    postings_closed: int
    postings_changed: int
    matches_new: int

    @classmethod
    def from_run(cls, run: PollRun) -> PollRunOut:
        return cls(
            id=run.id,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_s=run.duration_s,
            boards_attempted=run.boards_attempted,
            boards_ok=run.boards_ok,
            boards_failed=run.boards_failed,
            postings_seen=run.postings_seen,
            postings_new=run.postings_new,
            postings_closed=run.postings_closed,
            postings_changed=run.postings_changed,
            matches_new=run.matches_new,
        )


class LifespanOut(BaseModel):
    closed_count: int
    median_days: float | None
    shortest_days: float | None
    longest_days: float | None


class StatsOut(BaseModel):
    active_boards: int
    postings_total: int
    postings_open: int
    matches_total: int
    matches_canada: int
    lifespan: LifespanOut
    recent_runs: list[PollRunOut]

    @classmethod
    def from_stats(cls, stats: queries.Stats) -> StatsOut:
        return cls(
            active_boards=stats.active_boards,
            postings_total=stats.postings_total,
            postings_open=stats.postings_open,
            matches_total=stats.matches_total,
            matches_canada=stats.matches_canada,
            lifespan=LifespanOut(**stats.lifespan.__dict__),
            recent_runs=[PollRunOut.from_run(r) for r in stats.recent_runs],
        )


class CompanyTimingOut(BaseModel):
    company_id: int
    company_name: str
    posting_count: int
    first_seen_at: datetime
    latest_seen_at: datetime
    median_days_open: float | None


class TimingOut(BaseModel):
    """Per-company posting history, plus an honest flag for how much to trust it."""

    companies: list[CompanyTimingOut]
    history_days: float
    sufficient_data: bool


class HealthOut(BaseModel):
    state: Literal["green", "amber", "red", "unknown"]
    last_run: PollRunOut | None
    stale_seconds: float | None
    failing_boards: int

    @classmethod
    def from_health(cls, health: queries.SystemHealth) -> HealthOut:
        return cls(
            state=health.state,
            last_run=PollRunOut.from_run(health.last_run) if health.last_run else None,
            stale_seconds=health.stale_seconds,
            failing_boards=health.failing_boards,
        )


def _description(detail: PostingDetail | None) -> DescriptionOut:
    if detail is None:
        return DescriptionOut(state="not_fetched")
    if detail.fetch_error or not detail.content_html:
        return DescriptionOut(
            state="unavailable", error=detail.fetch_error, fetched_at=detail.fetched_at
        )
    return DescriptionOut(
        state="ready",
        html=detail.content_html,
        text=detail.content_text,
        fetched_at=detail.fetched_at,
    )
