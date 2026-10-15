"""Poller liveness.

This endpoint is why an empty feed is never ambiguous: the header reads its
state, so "nothing new" and "the poller died three days ago" look different.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ... import queries
from ..deps import SessionDep
from ..schemas import HealthOut

router = APIRouter(prefix="/api/system", tags=["system"])

# The documented `watch --every` default. The client may know better and pass
# its own, but the server must not pretend not to have an expectation.
DEFAULT_POLL_INTERVAL_S = 300


@router.get("/health", response_model=HealthOut)
def health(
    session: SessionDep,
    expected_interval_s: Annotated[int, Query(ge=1)] = DEFAULT_POLL_INTERVAL_S,
) -> HealthOut:
    return HealthOut.from_health(
        queries.system_health(session, expected_interval_s=expected_interval_s)
    )
