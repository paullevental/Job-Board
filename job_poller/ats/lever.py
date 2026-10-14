"""Lever Postings API: api.lever.co/v0/postings/{token}?mode=json (public, no auth)."""

from __future__ import annotations

import time

from .base import (
    DetailResult,
    FetchResult,
    RawPosting,
    describe_error,
    parse_epoch_millis,
    payload_is_posting,
)

API = "https://api.lever.co/v0/postings/{token}?mode=json"
DETAIL_API = "https://api.lever.co/v0/postings/{token}/{job_id}?mode=json"


class LeverAdapter:
    name = "lever"

    def board_url(self, token: str) -> str:
        return f"https://jobs.lever.co/{token}"

    def parse(self, token: str, payload: list) -> list[RawPosting]:
        out: list[RawPosting] = []
        for job in payload or []:
            job_id = job.get("id")
            if job_id is None:
                continue
            categories = job.get("categories") or {}
            out.append(
                RawPosting(
                    ats=self.name,
                    board_token=token,
                    external_id=str(job_id),
                    title=(job.get("text") or "").strip(),
                    url=job.get("hostedUrl") or job.get("applyUrl") or self.board_url(token),
                    location=(categories.get("location") or "").strip(),
                    department=(categories.get("department") or categories.get("team") or "").strip(),
                    posted_at=parse_epoch_millis(job.get("createdAt")),
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
            data = resp.json()
            if not isinstance(data, list):
                return FetchResult(token, self.name, False, error="unexpected payload shape", duration_ms=elapsed)
            return FetchResult(token, self.name, True, self.parse(token, data), duration_ms=elapsed)
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - started) * 1000)
            return FetchResult(token, self.name, False, error=f"{type(exc).__name__}: {exc}", duration_ms=elapsed)

    def parse_detail(self, payload: dict, external_id: str) -> str:
        """Lever splits one posting across three places.

        `description` is the opening, `lists[]` are the titled bullet sections
        (requirements, what we value), and `additional` is the closing. Taking
        only `description` silently drops the requirements, which is the half a
        candidate most needs. The HTML is already raw -- no unescaping.
        """
        parts = [payload.get("description") or ""]
        for section in payload.get("lists") or []:
            heading = (section.get("text") or "").strip()
            if heading:
                parts.append(f"<h3>{heading}</h3>")
            parts.append(f"<ul>{section.get('content') or ''}</ul>")
        parts.append(payload.get("additional") or "")
        return "\n".join(part for part in parts if part.strip())

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
