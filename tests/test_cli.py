"""The CLI as a user drives it.

These exist because the CLI and the API must answer the same questions the same
way. They are integration tests on purpose: they run `main()` against a real
database on disk, so a CLI command that stops calling the shared query layer
fails here rather than quietly drifting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from job_poller import db
from job_poller.cli import main
from job_poller.models import Company, PollRun, Posting

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def seeded(tmp_path):
    """init_engine sets module globals; put them back afterwards."""
    saved = (db._engine, db._Session)
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    db.init_engine(url)
    with db.session_scope() as s:
        company = Company(name="Acme", ats="greenhouse", board_token="acme", tags="canada")
        s.add(company)
        s.flush()
        s.add(
            Posting(
                company_id=company.id,
                ats="greenhouse",
                external_id="1",
                title="SWE Intern",
                url="https://x.test/1",
                location="Toronto, ON",
                category="intern",
                canada_eligible=True,
                engineering=True,
                first_seen_at=T0,
                last_seen_at=T0,
                closed_at=T0 + timedelta(days=4),
                fingerprint="f",
                dedupe_key="d",
            )
        )
        s.add(
            PollRun(
                started_at=T0,
                finished_at=T0 + timedelta(seconds=3),
                boards_attempted=1,
                boards_ok=1,
                matches_new=1,
            )
        )
    yield url
    db._engine, db._Session = saved


def test_list_renders_postings(seeded, capsys):
    assert main(["--db", seeded, "list"]) == 0

    assert "SWE Intern" in capsys.readouterr().out


def test_list_filters_by_company_tag(seeded, capsys):
    assert main(["--db", seeded, "list", "--tag", "canada"]) == 0
    assert "SWE Intern" in capsys.readouterr().out

    assert main(["--db", seeded, "list", "--tag", "nonexistent"]) == 0
    assert "SWE Intern" not in capsys.readouterr().out


def test_list_can_restrict_to_open_postings(seeded, capsys):
    assert main(["--db", seeded, "list", "--open"]) == 0

    # The only seeded posting is closed, so --open must exclude it.
    assert "SWE Intern" not in capsys.readouterr().out


def test_stats_reports_the_closed_posting_lifespan(seeded, capsys):
    assert main(["--db", seeded, "stats"]) == 0

    assert "4.0 days" in capsys.readouterr().out


def test_timing_warns_when_history_is_too_thin(seeded, capsys):
    assert main(["--db", seeded, "timing"]) == 0

    out = capsys.readouterr().out
    assert "Acme" in out
    assert "full co-op cycle" in out
