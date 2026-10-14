"""Offline end-to-end demo.

Runs the real pipeline -- adapters, classifier, diff, storage, notifier --
against recorded fixtures instead of the live network, across three simulated
polling cycles. Proves the system works without touching any ATS.

    python tests/demo_offline.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from job_poller.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter  # noqa: E402
from job_poller.ats.base import FetchResult  # noqa: E402
from job_poller.models import Base, Company, PollRun, Posting  # noqa: E402
from job_poller.notify import ConsoleNotifier, dispatch  # noqa: E402
from job_poller.poller import apply_results  # noqa: E402

console = Console()
FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def main() -> int:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    boards = [
        Company(name="Acme Corp", ats="greenhouse", board_token="acme"),
        Company(name="Beta Inc", ats="lever", board_token="beta"),
        Company(name="Gamma Labs", ats="ashby", board_token="gamma"),
    ]

    gh, lv, ash = GreenhouseAdapter(), LeverAdapter(), AshbyAdapter()
    gh_payload = load("greenhouse_sample.json")
    lv_payload = load("lever_sample.json")
    ash_payload = load("ashby_sample.json")

    with Session(engine, expire_on_commit=False) as session:
        session.add_all(boards)
        session.flush()

        def cycle(label, results, note=""):
            run = PollRun()
            session.add(run)
            session.flush()
            outcome = apply_results(session, boards, results, run)
            session.flush()
            console.rule(f"[bold]{label}[/bold]")
            if note:
                console.print(f"[dim]{note}[/dim]\n")
            matches = outcome.new_matches
            if matches:
                console.print(f"[bold green]{len(matches)} new match(es)[/bold green]\n")
                dispatch([ConsoleNotifier(console)], matches)
            else:
                console.print("[dim]No new matches.[/dim]")
            console.print(
                f"[dim]seen {outcome.postings_seen} · new {len(outcome.new_postings)} · "
                f"closed {len(outcome.closed_postings)} · changed {len(outcome.changed_postings)} · "
                f"failed boards {len(outcome.failures)}[/dim]\n"
            )
            return outcome

        # --- Cycle 1: cold start -------------------------------------------
        cycle("Cycle 1 — cold start", [
            FetchResult("acme", "greenhouse", True, gh.parse("acme", gh_payload)),
            FetchResult("beta", "lever", True, lv.parse("beta", lv_payload)),
            FetchResult("gamma", "ashby", True, ash.parse("gamma", ash_payload)),
        ], "11 raw postings across 3 boards. Only relevant ones should alert.")

        # --- Cycle 2: a board fails ----------------------------------------
        out = cycle("Cycle 2 — Acme's board times out", [
            FetchResult("acme", "greenhouse", False, [], error="ConnectTimeout"),
            FetchResult("beta", "lever", True, lv.parse("beta", lv_payload)),
            FetchResult("gamma", "ashby", True, ash.parse("gamma", ash_payload)),
        ], "A failed fetch must NOT close Acme's postings.")

        acme_open = session.scalar(
            select(Posting).where(Posting.company_id == boards[0].id, Posting.closed_at.is_(None))
        )
        assert acme_open is not None, "FAIL: network error closed postings"
        assert out.closed_postings == [], "FAIL: phantom closures"
        console.print("[green]✓ phantom-closure guard held[/green]\n")

        # --- Cycle 3: recovery, one closure, one new posting ---------------
        reduced = dict(gh_payload)
        reduced["jobs"] = [j for j in gh_payload["jobs"] if j["id"] != 5001]
        reduced["jobs"] = reduced["jobs"] + [{
            "id": 5099,
            "title": "Machine Learning Co-op, Summer 2027",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/5099",
            "location": {"name": "Vancouver, BC, Canada"},
            "departments": [{"name": "Research"}],
            "first_published": "2026-10-13T08:00:00Z",
        }]

        out = cycle("Cycle 3 — recovery: one role pulled, one added", [
            FetchResult("acme", "greenhouse", True, gh.parse("acme", reduced)),
            FetchResult("beta", "lever", True, lv.parse("beta", lv_payload)),
            FetchResult("gamma", "ashby", True, ash.parse("gamma", ash_payload)),
        ], "Expect exactly 1 new and 1 closed, and NO re-alerts for the boards that recovered.")

        assert len(out.new_postings) == 1, f"FAIL: expected 1 new, got {len(out.new_postings)}"
        assert len(out.closed_postings) == 1, f"FAIL: expected 1 closed, got {len(out.closed_postings)}"
        console.print("[green]✓ no alert storm after recovery[/green]\n")

        # --- Summary --------------------------------------------------------
        console.rule("[bold]What the database now holds[/bold]")
        all_postings = list(session.scalars(select(Posting).join(Company)))
        matched = [p for p in all_postings if p.category != "other"]
        filtered = [p for p in all_postings if p.category == "other"]
        canada = [p for p in matched if p.canada_eligible]

        console.print(f"  tracked           {len(all_postings)}")
        console.print(f"  relevant          {len(matched)}  ([green]{len(canada)} Canada-eligible[/green])")
        console.print(f"  filtered out      {len(filtered)}")
        console.print(f"  closed            {len([p for p in all_postings if p.closed_at])}")

        console.print("\n[bold]Correctly filtered out:[/bold]")
        for p in filtered:
            console.print(f"  [dim]·[/dim] {p.title}  [dim]({p.location})[/dim]")

        console.print("\n[bold]Geography calls:[/bold]")
        for p in matched:
            mark = "[green]✓[/green]" if p.canada_eligible else "[red]✗[/red]"
            console.print(f"  {mark} {p.title[:46]:46} [dim]{p.location}[/dim]")

    console.print("\n[bold green]All assertions passed.[/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
