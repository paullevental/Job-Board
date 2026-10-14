"""Board health. Which tokens are live, which are failing, which never ran."""

from __future__ import annotations

from fastapi import APIRouter

from ... import queries
from ..deps import SessionDep
from ..schemas import BoardHealthOut, CompaniesOut

router = APIRouter(prefix="/api/companies", tags=["companies"])


@router.get("", response_model=CompaniesOut)
def list_companies(session: SessionDep) -> CompaniesOut:
    return CompaniesOut(
        companies=[BoardHealthOut.from_health(h) for h in queries.company_health(session)]
    )
