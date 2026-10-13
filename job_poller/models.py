"""Database schema. Postings are never deleted -- a vanished posting gets a
`closed_at` stamp and keeps its row, so the open/close history survives.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """A datetime that always reads back as tz-aware UTC.

    SQLite has no tz-aware type and silently drops tzinfo on read, which makes
    a stored timestamp uncomparable with a fresh `utcnow()`. Normalizing here
    keeps every datetime in the application aware.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    """One company board we poll."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    ats: Mapped[str] = mapped_column(String(32))
    board_token: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[str] = mapped_column(String(300), default="")

    last_ok_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)

    postings: Mapped[list["Posting"]] = relationship(back_populates="company")

    __table_args__ = (UniqueConstraint("ats", "board_token", name="uq_company_board"),)

    def __repr__(self) -> str:
        return f"<Company {self.name} ({self.ats}:{self.board_token})>"


class Posting(Base):
    """One job posting, with its full open/closed lifespan."""

    __tablename__ = "postings"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)

    ats: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(200))

    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(String(500), default="")
    department: Mapped[str] = mapped_column(String(300), default="")

    category: Mapped[str] = mapped_column(String(32), index=True)
    canada_eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    engineering: Mapped[bool] = mapped_column(Boolean, default=False)
    season: Mapped[str | None] = mapped_column(String(16), default=None)
    year: Mapped[int | None] = mapped_column(Integer, default=None)

    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None, index=True)
    ats_posted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)

    fingerprint: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    notified_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)

    company: Mapped[Company] = relationship(back_populates="postings")
    application: Mapped["Application | None"] = relationship(back_populates="posting")
    detail: Mapped["PostingDetail | None"] = relationship(back_populates="posting")

    __table_args__ = (
        UniqueConstraint("ats", "external_id", "company_id", name="uq_posting_identity"),
        Index("ix_posting_open", "closed_at", "category", "canada_eligible"),
    )

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def days_open(self) -> float:
        end = self.closed_at or utcnow()
        return (end - self.first_seen_at).total_seconds() / 86400

    def __repr__(self) -> str:
        state = "open" if self.is_open else "closed"
        return f"<Posting {self.title!r} @ {self.ats}:{self.external_id} ({state})>"


class PollRun(Base):
    """Audit record for one polling pass."""

    __tablename__ = "poll_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)

    boards_attempted: Mapped[int] = mapped_column(Integer, default=0)
    boards_ok: Mapped[int] = mapped_column(Integer, default=0)
    boards_failed: Mapped[int] = mapped_column(Integer, default=0)

    postings_seen: Mapped[int] = mapped_column(Integer, default=0)
    postings_new: Mapped[int] = mapped_column(Integer, default=0)
    postings_closed: Mapped[int] = mapped_column(Integer, default=0)
    postings_changed: Mapped[int] = mapped_column(Integer, default=0)
    matches_new: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def duration_s(self) -> float | None:
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class Application(Base):
    """One posting the user has acted on. At most one row per posting."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)

    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)
    next_action_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)

    posting: Mapped[Posting] = relationship(back_populates="application")
    events: Mapped[list["ApplicationEvent"]] = relationship(
        back_populates="application", order_by="ApplicationEvent.id"
    )

    def __repr__(self) -> str:
        return f"<Application posting={self.posting_id} {self.status}>"


class ApplicationEvent(Base):
    """One status transition. Append-only: never updated, never deleted."""

    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(16), default=None)
    to_status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text, default=None)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, index=True)

    application: Mapped[Application] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"<ApplicationEvent {self.from_status} -> {self.to_status}>"


class PostingDetail(Base):
    """Job description, fetched on demand and then cached permanently."""

    __tablename__ = "posting_details"

    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), primary_key=True)
    content_html: Mapped[str] = mapped_column(Text, default="")
    content_text: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    fetch_error: Mapped[str | None] = mapped_column(Text, default=None)

    posting: Mapped[Posting] = relationship(back_populates="detail")


class AppMeta(Base):
    """Key/value scratch space: the feed-seen marker and the poll lock."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
