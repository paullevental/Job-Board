"""Alert delivery to console, JSONL and Discord."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Protocol

import httpx

from .models import Posting, utcnow

log = logging.getLogger("job_poller.notify")

_CATEGORY_LABEL = {"intern": "Internship / Co-op", "new_grad": "New Grad"}


def format_posting(p: Posting, company_name: str = "") -> str:
    bits = [f"{company_name or (p.company.name if p.company else '?')} — {p.title}"]
    meta = []
    if p.location:
        meta.append(p.location)
    if p.season or p.year:
        meta.append(" ".join(x for x in [p.season, str(p.year) if p.year else None] if x))
    if meta:
        bits.append("  " + " · ".join(meta))
    bits.append(f"  {p.url}")
    return "\n".join(bits)


class Notifier(Protocol):
    def send(self, postings: list[Posting]) -> int: ...


class ConsoleNotifier:
    """Prints matches to stdout. Always enabled."""

    def __init__(self, console=None):
        self.console = console

    def send(self, postings: list[Posting]) -> int:
        if not postings:
            return 0
        if self.console is not None:
            from rich.panel import Panel
            for p in postings:
                label = _CATEGORY_LABEL.get(p.category, p.category)
                company = p.company.name if p.company else "?"
                body = f"[bold]{p.title}[/bold]\n{p.location or 'location not listed'}"
                if p.season or p.year:
                    term = " ".join(x for x in [p.season, str(p.year) if p.year else None] if x)
                    body += f"\n[dim]{term}[/dim]"
                body += f"\n[blue underline]{p.url}[/blue underline]"
                self.console.print(Panel(body, title=f"{company} · {label}", border_style="green"))
        else:
            for p in postings:
                print(format_posting(p))
                print()
        return len(postings)


class JsonlNotifier:
    """Appends one JSON object per posting to a file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, postings: list[Posting]) -> int:
        if not postings:
            return 0
        with self.path.open("a", encoding="utf-8") as fh:
            for p in postings:
                fh.write(json.dumps({
                    "detected_at": utcnow().isoformat(),
                    "company": p.company.name if p.company else None,
                    "title": p.title,
                    "url": p.url,
                    "location": p.location,
                    "category": p.category,
                    "season": p.season,
                    "year": p.year,
                    "ats": p.ats,
                    "external_id": p.external_id,
                }, ensure_ascii=False) + "\n")
        return len(postings)


class DiscordNotifier:
    """Posts matches to a Discord webhook. Discord caps embeds at 10 per message."""

    MAX_EMBEDS = 10

    def __init__(self, webhook_url: str, timeout_s: float = 10.0):
        self.webhook_url = webhook_url
        self.timeout_s = timeout_s

    def _embed(self, p: Posting) -> dict:
        company = p.company.name if p.company else "?"
        fields = []
        if p.location:
            fields.append({"name": "Location", "value": p.location[:1024], "inline": True})
        term = " ".join(x for x in [p.season, str(p.year) if p.year else None] if x)
        if term:
            fields.append({"name": "Term", "value": term, "inline": True})
        return {
            "title": p.title[:256],
            "url": p.url,
            "description": f"**{company}** · {_CATEGORY_LABEL.get(p.category, p.category)}",
            "color": 0x2ECC71 if p.category == "intern" else 0x3498DB,
            "fields": fields,
        }

    def send(self, postings: list[Posting]) -> int:
        if not postings:
            return 0
        sent = 0
        with httpx.Client(timeout=self.timeout_s) as client:
            for i in range(0, len(postings), self.MAX_EMBEDS):
                chunk = postings[i:i + self.MAX_EMBEDS]
                payload = {
                    "content": f"**{len(chunk)} new posting{'s' if len(chunk) != 1 else ''}**",
                    "embeds": [self._embed(p) for p in chunk],
                }
                try:
                    resp = client.post(self.webhook_url, json=payload)
                    resp.raise_for_status()
                    sent += len(chunk)
                except Exception as exc:  # noqa: BLE001
                    log.error("discord webhook failed: %s", exc)
        return sent


def dispatch(notifiers: Iterable[Notifier], postings: list[Posting]) -> int:
    """Send to every configured sink and stamp the postings as notified."""
    pending = [p for p in postings if p.notified_at is None]
    if not pending:
        return 0
    for n in notifiers:
        try:
            n.send(pending)
        except Exception as exc:  # noqa: BLE001
            log.error("notifier %s failed: %s", type(n).__name__, exc)
    stamp = utcnow()
    for p in pending:
        p.notified_at = stamp
    return len(pending)
