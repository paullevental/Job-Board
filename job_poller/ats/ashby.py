"""Ashby Posting API: api.ashbyhq.com/posting-api/job-board/{token} (public, no auth).

Tokens are case-sensitive here, unlike the other vendors: "Linear" works, "linear" does not.
"""

from __future__ import annotations

import time

from .base import FetchResult, RawPosting, parse_iso

API = "https://api.ashbyhq.com/posting-api/job-board/{token}"


class AshbyAdapter:
    name = "ashby"

    def board_url(self, token: str) -> str:
        return f"https://jobs.ashbyhq.com/{token}"

    def parse(self, token: str, payload: dict) -> list[RawPosting]:
        out: list[RawPosting] = []
        for job in payload.get("jobs") or []:
            job_id = job.get("id")
            if job_id is None:
                continue
            out.append(
                RawPosting(
                    ats=self.name,
                    board_token=token,
                    external_id=str(job_id),
                    title=(job.get("title") or "").strip(),
                    url=job.get("jobUrl") or job.get("applyUrl") or self.board_url(token),
                    location=(job.get("location") or "").strip(),
                    department=(job.get("department") or job.get("team") or "").strip(),
                    posted_at=parse_iso(job.get("publishedAt") or job.get("updatedAt")),
                )
            )
        return out

    async def fetch(self, client, token: str) -> FetchResult:
        started = time.perf_counter()
        try:
            resp = await client.get(API.format(token=token))
            elapsed = int((time.perf_counter() - started) * 1000)
            if resp.status_code == 404:
                return FetchResult(token, self.name, False, error="board not found (404)", duration_ms=elapsed)
            resp.raise_for_status()
            return FetchResult(token, self.name, True, self.parse(token, resp.json()), duration_ms=elapsed)
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - started) * 1000)
            return FetchResult(token, self.name, False, error=f"{type(exc).__name__}: {exc}", duration_ms=elapsed)
