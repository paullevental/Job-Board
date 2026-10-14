"""The history payoff: coverage, posting lifespan, and when companies post."""

from __future__ import annotations

from fastapi import APIRouter

from ... import queries
from ..deps import SessionDep
from ..schemas import CompanyTimingOut, StatsOut, TimingOut

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/stats", response_model=StatsOut)
def stats(session: SessionDep) -> StatsOut:
    return StatsOut.from_stats(queries.stats(session))


@router.get("/timing", response_model=TimingOut)
def timing(session: SessionDep) -> TimingOut:
    """Per-company posting history, with `sufficient_data` so the UI can say
    "not enough history yet" instead of drawing a confident chart."""
    report = queries.timing_report(session)
    return TimingOut(
        companies=[CompanyTimingOut(**row.__dict__) for row in report.companies],
        history_days=round(report.history_days, 1),
        sufficient_data=report.sufficient_data,
    )
