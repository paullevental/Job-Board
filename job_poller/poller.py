"""Concurrent board polling and change detection.

A posting is only closed when its board was fetched successfully and the posting
was absent. Without that rule a brief network failure would close every posting
at a company and reopen them on the next pass, firing a storm of false alerts.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ats import FetchResult, get_adapter
from .ats.base import REQUEST_HEADERS
from .classify import Category, classify
from .models import Company, PollRun, Posting, utcnow

log = logging.getLogger("job_poller.poller")

@dataclass
class PollConfig:
    concurrency: int = 8
    timeout_s: float = 20.0
    max_retries: int = 2
    retry_backoff_s: float = 1.5
    max_response_bytes: int = 40 * 1024 * 1024


@dataclass
class PollOutcome:
    """What one poll pass found."""

    run_id: int
    new_postings: list[Posting] = field(default_factory=list)
    closed_postings: list[Posting] = field(default_factory=list)
    changed_postings: list[Posting] = field(default_factory=list)
    failures: list[FetchResult] = field(default_factory=list)
    boards_ok: int = 0
    postings_seen: int = 0

    @property
    def new_matches(self) -> list[Posting]:
        """New postings that are actually relevant -- what we alert on."""
        return [p for p in self.new_postings if p.category != Category.OTHER]


async def _fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    company: Company,
    cfg: PollConfig,
) -> FetchResult:
    """Fetch one board with bounded concurrency and bounded retries."""
    adapter = get_adapter(company.ats)
    async with sem:
        last: FetchResult | None = None
        for attempt in range(cfg.max_retries + 1):
            result = await adapter.fetch(client, company.board_token)
            if result.ok:
                return result
            last = result
            # A 404 is a permanent answer; retrying wastes time and requests.
            if result.error and "404" in result.error:
                break
            if attempt < cfg.max_retries:
                await asyncio.sleep(cfg.retry_backoff_s * (2 ** attempt))
        assert last is not None
        return last


async def fetch_all(companies: list[Company], cfg: PollConfig | None = None) -> list[FetchResult]:
    """Fetch every board concurrently. Never raises on a per-board failure."""
    cfg = cfg or PollConfig()
    sem = asyncio.Semaphore(cfg.concurrency)
    limits = httpx.Limits(max_connections=cfg.concurrency, max_keepalive_connections=cfg.concurrency)
    async with httpx.AsyncClient(
        timeout=cfg.timeout_s,
        limits=limits,
        follow_redirects=True,
        headers=REQUEST_HEADERS,
    ) as client:
        return await asyncio.gather(*(_fetch_one(client, sem, c, cfg) for c in companies))


def apply_results(
    session: Session,
    companies: list[Company],
    results: list[FetchResult],
    run: PollRun,
) -> PollOutcome:
    """Diff fetched postings against stored state and record the changes."""
    by_key = {(c.ats, c.board_token): c for c in companies}
    outcome = PollOutcome(run_id=run.id)
    now = utcnow()

    for result in results:
        company = by_key.get((result.ats, result.board_token))
        if company is None:
            continue

        if not result.ok:
            company.consecutive_failures += 1
            company.last_error = result.error
            outcome.failures.append(result)
            log.warning("board %s:%s failed: %s", result.ats, result.board_token, result.error)
            continue  # never diff a board we could not read

        company.consecutive_failures = 0
        company.last_error = None
        company.last_ok_at = now
        outcome.boards_ok += 1
        outcome.postings_seen += len(result.postings)

        stored = {
            p.external_id: p
            for p in session.scalars(
                select(Posting).where(Posting.company_id == company.id)
            )
        }
        seen_ids: set[str] = set()

        for raw in result.postings:
            seen_ids.add(raw.external_id)
            existing = stored.get(raw.external_id)
            cls = classify(raw.title, raw.location, raw.department)

            if existing is None:
                posting = Posting(
                    company_id=company.id,
                    ats=raw.ats,
                    external_id=raw.external_id,
                    title=raw.title,
                    url=raw.url,
                    location=raw.location,
                    department=raw.department,
                    category=str(cls.category),
                    canada_eligible=cls.canada_eligible,
                    is_remote=cls.is_remote,
                    engineering=cls.engineering,
                    season=cls.season,
                    year=cls.year,
                    first_seen_at=now,
                    last_seen_at=now,
                    ats_posted_at=raw.posted_at,
                    fingerprint=raw.fingerprint(),
                    dedupe_key=raw.dedupe_key(),
                )
                session.add(posting)
                posting.company = company
                outcome.new_postings.append(posting)
                continue

            existing.last_seen_at = now

            # A repost means the company re-opened the role, so alert again.
            if existing.closed_at is not None:
                existing.closed_at = None
                existing.first_seen_at = now
                existing.notified_at = None
                outcome.new_postings.append(existing)

            new_fp = raw.fingerprint()
            if existing.fingerprint != new_fp:
                existing.title = raw.title
                existing.url = raw.url
                existing.location = raw.location
                existing.department = raw.department
                existing.fingerprint = new_fp
                existing.dedupe_key = raw.dedupe_key()
                existing.category = str(cls.category)
                existing.canada_eligible = cls.canada_eligible
                existing.is_remote = cls.is_remote
                existing.engineering = cls.engineering
                existing.season = cls.season
                existing.year = cls.year
                if existing not in outcome.new_postings:
                    outcome.changed_postings.append(existing)

        for ext_id, posting in stored.items():
            if ext_id not in seen_ids and posting.closed_at is None:
                posting.closed_at = now
                outcome.closed_postings.append(posting)

    run.boards_attempted = len(results)
    run.boards_ok = outcome.boards_ok
    run.boards_failed = len(outcome.failures)
    run.postings_seen = outcome.postings_seen
    run.postings_new = len(outcome.new_postings)
    run.postings_closed = len(outcome.closed_postings)
    run.postings_changed = len(outcome.changed_postings)
    run.matches_new = len(outcome.new_matches)
    run.finished_at = utcnow()

    return outcome


async def poll_once(session: Session, cfg: PollConfig | None = None) -> PollOutcome:
    """Run one full poll: fetch every active board, then diff and persist."""
    companies = list(session.scalars(select(Company).where(Company.active.is_(True))))
    if not companies:
        raise RuntimeError("no active companies configured; run `job_poller load-companies` first")

    run = PollRun(started_at=utcnow())
    session.add(run)
    session.flush()  # assign run.id

    results = await fetch_all(companies, cfg)
    return apply_results(session, companies, results, run)
