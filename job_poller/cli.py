"""Command line interface. Run with --help for the command list."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from . import __version__
from .ats import get_adapter
from .config import load_specs, sync_companies
from .db import init_engine, session_scope
from .models import Company, Posting, PollRun, utcnow
from .notify import ConsoleNotifier, DiscordNotifier, JsonlNotifier, dispatch
from .poller import PollConfig, fetch_all, poll_once

console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_notifiers(args) -> list:
    notifiers = [ConsoleNotifier(console)]
    webhook = (
        args.discord
        or os.environ.get("JOB_POLLER_DISCORD_WEBHOOK")
        or os.environ.get("COOPWATCH_DISCORD_WEBHOOK")  # pre-rename name
    )
    if webhook:
        notifiers.append(DiscordNotifier(webhook))
    if args.jsonl:
        notifiers.append(JsonlNotifier(args.jsonl))
    return notifiers


def cmd_load_companies(args) -> int:
    specs = load_specs(args.config)
    with session_scope() as session:
        added, updated = sync_companies(session, specs)
    console.print(f"[green]Loaded {len(specs)} boards[/green] — {added} added, {updated} updated.")
    console.print("[dim]Next: python -m job_poller verify --prune[/dim]")
    return 0


def cmd_verify(args) -> int:
    """Hit every board once and report which tokens actually work."""
    with session_scope() as session:
        companies = list(session.scalars(select(Company)))
        if not companies:
            console.print("[red]No companies loaded.[/red] Run load-companies first.")
            return 1

        console.print(f"Checking {len(companies)} boards...\n")
        results = asyncio.run(fetch_all(companies, PollConfig(concurrency=args.concurrency)))
        by_key = {(c.ats, c.board_token): c for c in companies}

        table = Table(show_header=True, header_style="bold")
        table.add_column("Company")
        table.add_column("ATS")
        table.add_column("Token")
        table.add_column("Jobs", justify="right")
        table.add_column("Status")

        live = empty = dead = 0
        for r in sorted(results, key=lambda r: (not r.ok, r.board_token)):
            company = by_key[(r.ats, r.board_token)]
            if not r.ok:
                dead += 1
                status = f"[red]{r.error}[/red]"
                count = "—"
                if args.prune:
                    company.active = False
                company.last_error = r.error
            elif not r.postings:
                empty += 1
                status = "[yellow]live but empty[/yellow]"
                count = "0"
                company.last_ok_at = utcnow()
            else:
                live += 1
                status = "[green]ok[/green]"
                count = str(len(r.postings))
                company.last_ok_at = utcnow()
            table.add_row(company.name, r.ats, r.board_token, count, status)

        console.print(table)
        console.print(
            f"\n[green]{live} live[/green] · [yellow]{empty} empty[/yellow] · [red]{dead} unreachable[/red]"
        )
        if dead and args.prune:
            console.print(f"[dim]Deactivated {dead} unreachable boards. Fix the tokens in your YAML and re-run.[/dim]")
        elif dead:
            console.print("[dim]Re-run with --prune to deactivate the unreachable ones.[/dim]")
    return 0


def _report(outcome, elapsed: float) -> None:
    console.print(
        f"[dim]{outcome.boards_ok} boards ok · {len(outcome.failures)} failed · "
        f"{outcome.postings_seen} postings seen · {len(outcome.new_postings)} new · "
        f"{len(outcome.closed_postings)} closed · {elapsed:.1f}s[/dim]"
    )
    for f in outcome.failures[:5]:
        console.print(f"  [red]![/red] {f.ats}:{f.board_token} — {f.error}")
    if len(outcome.failures) > 5:
        console.print(f"  [dim]...and {len(outcome.failures) - 5} more[/dim]")


def cmd_poll(args) -> int:
    started = utcnow()
    with session_scope() as session:
        outcome = asyncio.run(poll_once(session, PollConfig(concurrency=args.concurrency)))

        matches = outcome.new_matches
        if args.canada_only:
            matches = [p for p in matches if p.canada_eligible]
        if args.engineering_only:
            matches = [p for p in matches if p.engineering]

        elapsed = (utcnow() - started).total_seconds()
        if matches:
            console.print(f"\n[bold green]{len(matches)} new match{'es' if len(matches) != 1 else ''}[/bold green]\n")
            dispatch(_build_notifiers(args), matches)
        else:
            console.print("[dim]No new matches.[/dim]")
        _report(outcome, elapsed)
    return 0


def cmd_watch(args) -> int:
    console.print(f"[bold]Watching every {args.every}s.[/bold] Ctrl-C to stop.\n")
    cycle = 0
    while True:
        cycle += 1
        stamp = datetime.now().strftime("%H:%M:%S")
        console.print(f"[dim]— cycle {cycle} at {stamp} —[/dim]")
        try:
            cmd_poll(args)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - the watcher must survive anything
            console.print(f"[red]cycle failed: {type(exc).__name__}: {exc}[/red]")
        try:
            asyncio.run(asyncio.sleep(args.every))
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
            return 0


def cmd_list(args) -> int:
    with session_scope() as session:
        stmt = select(Posting).join(Company)
        if args.open:
            stmt = stmt.where(Posting.closed_at.is_(None))
        if not args.all_categories:
            stmt = stmt.where(Posting.category != "other")
        if args.canada_only:
            stmt = stmt.where(Posting.canada_eligible.is_(True))
        if args.tag:
            stmt = stmt.where(Company.tags.contains(args.tag))
        stmt = stmt.order_by(Posting.first_seen_at.desc()).limit(args.limit)

        rows = list(session.scalars(stmt))
        if not rows:
            console.print("[dim]Nothing matches.[/dim]")
            return 0

        table = Table(show_header=True, header_style="bold")
        table.add_column("First seen", style="dim")
        table.add_column("Company")
        table.add_column("Title")
        table.add_column("Location")
        table.add_column("Term")
        table.add_column("Days", justify="right")
        for p in rows:
            term = " ".join(x for x in [p.season, str(p.year) if p.year else None] if x)
            table.add_row(
                p.first_seen_at.strftime("%b %d"),
                p.company.name,
                p.title[:60],
                (p.location or "—")[:34],
                term or "—",
                f"{p.days_open:.0f}" + ("" if p.is_open else " ✓"),
            )
        console.print(table)
        console.print(f"[dim]{len(rows)} shown. ✓ = closed.[/dim]")
    return 0


def cmd_stats(args) -> int:
    with session_scope() as session:
        total = session.scalar(select(func.count()).select_from(Posting)) or 0
        open_now = session.scalar(
            select(func.count()).select_from(Posting).where(Posting.closed_at.is_(None))
        ) or 0
        matches = session.scalar(
            select(func.count()).select_from(Posting).where(Posting.category != "other")
        ) or 0
        canada = session.scalar(
            select(func.count()).select_from(Posting)
            .where(Posting.category != "other", Posting.canada_eligible.is_(True))
        ) or 0
        boards = session.scalar(
            select(func.count()).select_from(Company).where(Company.active.is_(True))
        ) or 0

        console.print("\n[bold]Coverage[/bold]")
        console.print(f"  active boards      {boards}")
        console.print(f"  postings tracked   {total}  ({open_now} open)")
        console.print(f"  intern / new grad  {matches}  ({canada} Canada-eligible)")

        # Median is over closed postings only.
        closed = list(session.scalars(select(Posting).where(Posting.closed_at.is_not(None))))
        if closed:
            spans = sorted(p.days_open for p in closed)
            median = spans[len(spans) // 2]
            console.print(f"\n[bold]Posting lifespan[/bold] (n={len(spans)})")
            console.print(f"  median open        {median:.1f} days")
            console.print(f"  shortest           {spans[0]:.1f} days")
            console.print(f"  longest            {spans[-1]:.1f} days")
        else:
            console.print("\n[dim]No closed postings yet — lifespan stats need a few weeks of history.[/dim]")

        last_runs = list(session.scalars(select(PollRun).order_by(PollRun.started_at.desc()).limit(5)))
        if last_runs:
            console.print("\n[bold]Recent runs[/bold]")
            for r in last_runs:
                dur = f"{r.duration_s:.1f}s" if r.duration_s else "—"
                console.print(
                    f"  {r.started_at.strftime('%b %d %H:%M')}  "
                    f"{r.boards_ok}/{r.boards_attempted} ok · {r.matches_new} new matches · {dur}"
                )
            newest = last_runs[0].started_at
            if newest.tzinfo is None:
                newest = newest.replace(tzinfo=timezone.utc)
            stale = utcnow() - newest
            if stale > timedelta(hours=6):
                console.print(f"\n[yellow]Last run was {stale.total_seconds() / 3600:.0f}h ago — is the watcher running?[/yellow]")

        unhealthy = list(session.scalars(
            select(Company).where(Company.consecutive_failures >= 3, Company.active.is_(True))
        ))
        if unhealthy:
            console.print(f"\n[yellow]{len(unhealthy)} boards failing repeatedly[/yellow]")
            for c in unhealthy[:10]:
                console.print(f"  {c.name} ({c.ats}:{c.board_token}) — {c.last_error}")
    return 0


def cmd_timing(args) -> int:
    """When does each company actually post? Needs a season of history."""
    with session_scope() as session:
        stmt = (
            select(Posting).join(Company)
            .where(Posting.category == "intern")
            .order_by(Posting.first_seen_at)
        )
        rows = list(session.scalars(stmt))
        if not rows:
            console.print("[dim]No internship postings recorded yet.[/dim]")
            return 0

        by_company: dict[str, list[Posting]] = {}
        for p in rows:
            by_company.setdefault(p.company.name, []).append(p)

        table = Table(show_header=True, header_style="bold")
        table.add_column("Company")
        table.add_column("Postings", justify="right")
        table.add_column("First seen")
        table.add_column("Most recent")
        table.add_column("Median days open", justify="right")

        for name, posts in sorted(by_company.items(), key=lambda kv: -len(kv[1])):
            closed = [p for p in posts if p.closed_at]
            med = "—"
            if closed:
                spans = sorted(p.days_open for p in closed)
                med = f"{spans[len(spans) // 2]:.0f}"
            table.add_row(
                name,
                str(len(posts)),
                posts[0].first_seen_at.strftime("%b %d, %Y"),
                posts[-1].first_seen_at.strftime("%b %d, %Y"),
                med,
            )
        console.print(table)
        console.print("[dim]This gets genuinely useful after a full co-op cycle of data.[/dim]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="job_poller", description="Internship and co-op posting watcher.")
    p.add_argument("--version", action="version", version=f"job_poller {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--db", help="SQLAlchemy URL (default: sqlite:///job_poller.db)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("load-companies", help="load board list from YAML")
    s.add_argument("--config", help="path to companies.yml")
    s.set_defaults(func=cmd_load_companies)

    s = sub.add_parser("verify", help="check which board tokens are live")
    s.add_argument("--prune", action="store_true", help="deactivate unreachable boards")
    s.add_argument("--concurrency", type=int, default=8)
    s.set_defaults(func=cmd_verify)

    def add_poll_args(sp):
        sp.add_argument("--concurrency", type=int, default=8)
        sp.add_argument("--canada-only", action="store_true", help="only alert on Canada-eligible roles")
        sp.add_argument("--engineering-only", action="store_true")
        sp.add_argument("--discord", help="Discord webhook URL")
        sp.add_argument("--jsonl", help="append matches to this JSONL file")

    s = sub.add_parser("poll", help="run one polling pass")
    add_poll_args(s)
    s.set_defaults(func=cmd_poll)

    s = sub.add_parser("watch", help="poll on a loop")
    add_poll_args(s)
    s.add_argument("--every", type=int, default=300, help="seconds between passes (default 300)")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("list", help="show tracked postings")
    s.add_argument("--open", action="store_true", help="only currently-open postings")
    s.add_argument("--all-categories", action="store_true", help="include non-matching postings")
    s.add_argument("--canada-only", action="store_true")
    s.add_argument("--tag", help="filter by company tag, e.g. canada")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("stats", help="coverage, lifespan and health")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("timing", help="when each company posts")
    s.set_defaults(func=cmd_timing)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    init_engine(args.db)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        return 130
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
