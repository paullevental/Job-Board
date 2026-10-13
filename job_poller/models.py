"""Database schema. Postings are never deleted -- a vanished posting gets a
`closed_at` stamp and keeps its row, so the open/close history survives.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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

    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

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

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, index=True)
    ats_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    fingerprint: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    company: Mapped[Company] = relationship(back_populates="postings")

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
        start = self.first_seen_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return (end - start).total_seconds() / 86400

    def __repr__(self) -> str:
        state = "open" if self.is_open else "closed"
        return f"<Posting {self.title!r} @ {self.ats}:{self.external_id} ({state})>"


class PollRun(Base):
    """Audit record for one polling pass."""

    __tablename__ = "poll_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

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
