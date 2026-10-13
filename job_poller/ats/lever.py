"""Lever Postings API: api.lever.co/v0/postings/{token}?mode=json (public, no auth)."""

from __future__ import annotations

import time

from .base import FetchResult, RawPosting, parse_epoch_millis

API = "https://api.lever.co/v0/postings/{token}?mode=json"


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
