"""Application status tracking.

Status lives here rather than on `Posting` because the poller overwrites posting
rows in place, and a repost resets their timestamps. Every change appends an
`ApplicationEvent`, so the pipeline history is never lost.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Application, ApplicationEvent, Posting, utcnow


class Status(StrEnum):
    SAVED = "saved"
    APPLIED = "applied"
    ASSESSMENT = "assessment"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    DISMISSED = "dismissed"


# Distinguishes "leave this field alone" from "set this field to None".
_UNSET = object()

OPEN_STATUSES = (Status.SAVED, Status.APPLIED, Status.ASSESSMENT, Status.INTERVIEWING)


def get_application(session: Session, posting_id: int) -> Application | None:
    return session.scalar(select(Application).where(Application.posting_id == posting_id))


def set_status(
    session: Session,
    posting_id: int,
    status: str,
    note: str | None = None,
) -> Application:
    """Set a posting's status, appending a transition event."""
    status = _coerce(status)
    if session.get(Posting, posting_id) is None:
        raise ValueError(f"no posting with id {posting_id}")

    app = get_application(session, posting_id)
    previous = app.status if app is not None else None
    if previous == status:
        return app

    if app is None:
        app = Application(posting_id=posting_id, status=status)
        session.add(app)
    else:
        app.status = status
        app.updated_at = utcnow()

    if status is Status.APPLIED and app.applied_at is None:
        app.applied_at = utcnow()

    session.flush()
    session.add(
        ApplicationEvent(
            application_id=app.id,
            from_status=previous,
            to_status=status,
            note=note,
        )
    )
    session.flush()
    return app


def update_application(
    session: Session,
    application_id: int,
    notes: str | None = _UNSET,
    next_action_at: datetime | None = _UNSET,
) -> Application:
    """Edit the fields that are not status. Omitted arguments are left alone."""
    app = session.get(Application, application_id)
    if app is None:
        raise ValueError(f"no application with id {application_id}")

    if notes is not _UNSET:
        app.notes = notes
    if next_action_at is not _UNSET:
        app.next_action_at = next_action_at

    app.updated_at = utcnow()
    session.flush()
    return app


def _coerce(status: str) -> Status:
    try:
        return Status(status)
    except ValueError:
        raise ValueError(
            f"unknown status {status!r}; expected one of {', '.join(Status)}"
        ) from None
