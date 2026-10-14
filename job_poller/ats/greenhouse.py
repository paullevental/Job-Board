"""Greenhouse Job Board API: boards-api.greenhouse.io/v1/boards/{token}/jobs (public, no auth).

`content=true` on the board listing would inline every description and inflate the
payload ~13x (measured: 751 KB for 895 jobs), so the poll omits it and a single
posting is fetched on demand instead.
"""

from __future__ import annotations

import html
import time

from .base import (
    DetailResult,
    FetchResult,
    RawPosting,
    describe_error,
    parse_iso,
    payload_is_posting,
)

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
DETAIL_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?content=true"


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

    def parse_detail(self, payload: dict, external_id: str) -> str:
        """Greenhouse HTML-escapes `content`, so it arrives as `&lt;p&gt;`.

        Confirmed against a live response, not assumed: without one unescape pass
        the user would read literal tags. Unescaping is safe here because the
        result is sanitized before it is stored or rendered.
        """
        content = payload.get("content") or ""
        return html.unescape(content)

    async def fetch_detail(self, client, token: str, external_id: str) -> DetailResult:
        try:
            resp = await client.get(DETAIL_API.format(token=token, job_id=external_id))
            if resp.status_code == 404:
                return DetailResult(ok=False, error="posting not found (404)")
            resp.raise_for_status()
            payload = resp.json()
            if not payload_is_posting(payload, external_id):
                return DetailResult(ok=False, error="board returned a different posting")
            return DetailResult(ok=True, html=self.parse_detail(payload, external_id))
        except Exception as exc:  # noqa: BLE001 - a dead board must not break the page
            return DetailResult(ok=False, error=describe_error(exc))
