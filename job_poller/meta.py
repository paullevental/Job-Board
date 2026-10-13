"""Key/value scratch space: the feed-seen marker and the poll lock."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import AppMeta, utcnow

LAST_FEED_SEEN_AT = "last_feed_seen_at"
POLL_LOCK = "poll_lock"


def get(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(AppMeta, key)
    return row.value if row is not None else default


def set(session: Session, key: str, value: str) -> None:
    row = session.get(AppMeta, key)
    if row is None:
        session.add(AppMeta(key=key, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()
    session.flush()


def get_time(session: Session, key: str) -> datetime | None:
    raw = get(session, key)
    if raw is None:
        return None
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def set_time(session: Session, key: str, when: datetime) -> None:
    set(session, key, when.isoformat())
