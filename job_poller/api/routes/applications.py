"""The pipeline: tracked applications by stage, and one application's timeline."""

from __future__ import annotations

from fastapi import APIRouter

from ... import queries
from ...tracking import Status
from ..deps import DbIdPath, SessionDep
from ..errors import not_found
from ..schemas import ApplicationOut, EventOut, EventsOut, PipelineColumnOut, PipelineOut

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.get("", response_model=PipelineOut)
def pipeline(session: SessionDep, status: Status | None = None) -> PipelineOut:
    """Applications bucketed by status.

    Empty columns are returned too -- the pipeline is a board, and a missing
    column would make the UI's layout depend on the data.
    """
    buckets = queries.pipeline(session)
    wanted = [status] if status else list(Status)

    return PipelineOut(
        columns=[
            PipelineColumnOut(
                status=s.value,
                count=len(buckets[s]),
                applications=[ApplicationOut.from_application(a) for a in buckets[s]],
            )
            for s in wanted
        ]
    )


@router.get("/{application_id}/events", response_model=EventsOut)
def events(application_id: DbIdPath, session: SessionDep) -> EventsOut:
    """The append-only transition log, oldest first."""
    app = queries.get_application_by_id(session, application_id)
    if app is None:
        raise not_found("application", application_id)
    return EventsOut(events=[EventOut.from_event(e) for e in app.events])
