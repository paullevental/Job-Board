"""Greenhouse Job Board API: boards-api.greenhouse.io/v1/boards/{token}/jobs (public, no auth).

`content=true` would inline full descriptions and inflate the payload ~12x, so we omit it.
"""

from __future__ import annotations

import time

from .base import FetchResult, RawPosting, parse_iso

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseAdapter:
    name = "greenhouse"

    def board_url(self, token: str) -> str:
        return f"https://job-boards.greenhouse.io/{token}"

    def parse(self, token: str, payload: dict) -> list[RawPosting]:
        out: list[RawPosting] = []
        for job in payload.get("jobs") or []:
            job_id = job.get("id")
            if job_id is None:
                continue
            location = ""
            loc = job.get("location")
            if isinstance(loc, dict):
                location = loc.get("name") or ""
            elif isinstance(loc, str):
                location = loc

            departments = job.get("departments") or []
            department = ""
            if departments and isinstance(departments[0], dict):
                department = departments[0].get("name") or ""

            out.append(
                RawPosting(
                    ats=self.name,
                    board_token=token,
                    external_id=str(job_id),
                    title=(job.get("title") or "").strip(),
                    url=job.get("absolute_url") or f"{self.board_url(token)}/jobs/{job_id}",
                    location=location.strip(),
                    department=department.strip(),
                    posted_at=parse_iso(job.get("first_published") or job.get("updated_at")),
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
        except Exception as exc:  # noqa: BLE001 - one bad board must not stop the run
            elapsed = int((time.perf_counter() - started) * 1000)
            return FetchResult(token, self.name, False, error=f"{type(exc).__name__}: {exc}", duration_ms=elapsed)
