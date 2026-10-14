"""Ashby Posting API: api.ashbyhq.com/posting-api/job-board/{token} (public, no auth).

Tokens are case-sensitive here, unlike the other vendors: "Linear" works, "linear" does not.
"""

from __future__ import annotations

import time

from .base import DetailResult, FetchResult, RawPosting, describe_error, parse_iso

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

    def find_job(self, payload: dict, external_id: str) -> dict | None:
        """Ashby publishes no per-posting endpoint, so the whole board comes back
        and the posting has to be located by id. Absent means the role closed."""
        for job in payload.get("jobs") or []:
            if str(job.get("id")) == str(external_id):
                return job
        return None

    def parse_detail(self, payload: dict, external_id: str) -> str:
        job = self.find_job(payload, external_id)
        return (job or {}).get("descriptionHtml") or ""

    async def fetch_detail(self, client, token: str, external_id: str) -> DetailResult:
        """Costs a whole board fetch per description, because Ashby offers no
        single-posting route (`.../job-board/{token}/job/{id}` returns 401 for
        valid and invalid ids alike). Acceptable because only a human opening a
        posting triggers it, and a successful fetch is cached forever."""
        try:
            resp = await client.get(API.format(token=token))
            if resp.status_code == 404:
                return DetailResult(ok=False, error="board not found (404)")
            resp.raise_for_status()
            job = self.find_job(resp.json(), external_id)
            if job is None:
                return DetailResult(ok=False, error="posting is no longer on the board")
            # A posting that is on the board but has no body is an empty
            # description, not a closed role; `details.py` decides what that means.
            return DetailResult(ok=True, html=job.get("descriptionHtml") or "")
        except Exception as exc:  # noqa: BLE001 - a dead board must not break the page
            return DetailResult(ok=False, error=describe_error(exc))
