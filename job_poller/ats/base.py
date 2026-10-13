"""Common posting shape that every ATS adapter normalizes into."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class RawPosting:
    ats: str
    board_token: str
    external_id: str
    title: str
    url: str
    location: str = ""
    department: str = ""
    posted_at: datetime | None = None

    def fingerprint(self) -> str:
        """Detects a posting edited in place rather than reposted."""
        payload = f"{self.title}\x1f{self.location}\x1f{self.department}\x1f{self.url}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def dedupe_key(self) -> str:
        """Collapses the same role listed on more than one board."""
        norm_title = " ".join(self.title.lower().split())
        norm_loc = " ".join(self.location.lower().replace(",", " ").split())
        return hashlib.sha256(f"{norm_title}\x1f{norm_loc}".encode("utf-8")).hexdigest()[:32]


@dataclass
class FetchResult:
    board_token: str
    ats: str
    ok: bool
    postings: list[RawPosting] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ATSAdapter(Protocol):
    name: str

    def board_url(self, token: str) -> str: ...

    async def fetch(self, client, token: str) -> FetchResult: ...


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def parse_epoch_millis(value) -> datetime | None:
    """Lever reports timestamps as epoch milliseconds."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        return None
