"""Common posting shape that every ATS adapter normalizes into."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

import httpx

# Sent on every request to every vendor, from the poller and the API alike.
USER_AGENT = (
    "job_poller/0.1 (student internship tracker; "
    "polite polling; contact via repository)"
)
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}


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
        return hashlib.sha256(f"{norm_title}\x1f{norm_loc}".encode()).hexdigest()[:32]


@dataclass
class FetchResult:
    board_token: str
    ats: str
    ok: bool
    postings: list[RawPosting] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DetailResult:
    """One posting's description, as the vendor returned it.

    `html` is raw and untrusted here -- sanitizing is `details.py`'s job, and
    keeping the two separate means an adapter cannot accidentally be the thing
    that decides what is safe to store.
    """

    ok: bool
    html: str = ""
    error: str | None = None


def payload_is_posting(payload: dict, external_id: str) -> bool:
    """Whether a single-posting response is the posting we asked for.

    Requests follow redirects, so a board can answer with a different job than
    the one requested. Serving that under our posting's id would be quietly
    wrong. A payload with no `id` at all is accepted -- absence is not evidence
    of a mismatch.
    """
    returned = payload.get("id")
    return returned is None or str(returned) == str(external_id)


def describe_error(exc: Exception) -> str:
    """A short, honest failure reason that is safe to show a client.

    Deliberately not `str(exc)`: httpx puts the full request URL in
    `HTTPStatusError`, and that URL contains the board token. The status code is
    the useful part; the rest belongs in the log.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"board returned {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "board did not respond in time"
    return type(exc).__name__


class ATSAdapter(Protocol):
    name: str

    def board_url(self, token: str) -> str: ...

    async def fetch(self, client, token: str) -> FetchResult: ...

    async def fetch_detail(self, client, token: str, external_id: str) -> DetailResult: ...

    # `external_id` is part of the uniform signature even though only Ashby needs
    # it to locate the posting: Greenhouse and Lever address it in the URL.
    def parse_detail(self, payload: dict, external_id: str) -> str: ...


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
