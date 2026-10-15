"""Request-scoped dependencies.

The session dependency is the only seam the tests need: they override it with a
session bound to an in-memory database, so the entire API is testable without a
server, a file, or a network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Path, Query
from sqlalchemy.orm import Session

from .. import details
from ..ats.base import DetailResult
from ..db import session_scope
from ..models import Posting

MAX_LIMIT = 100
# SQLite stores signed 64-bit integers; anything larger raises OverflowError
# deep in the driver, which would surface to the client as "we have a bug"
# rather than "that id cannot exist".
MAX_DB_INT = 2**63 - 1
MAX_SEARCH_LEN = 200


def get_session() -> Iterator[Session]:
    """Yield a transactional session for one request."""
    with session_scope() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

# Bounded so a client cannot ask for the whole table in one page. Over the
# limit is a 422, not a silent clamp, so the caller learns about it.
LimitDep = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


DetailFetcher = Callable[[Posting], DetailResult]


def get_detail_fetcher() -> DetailFetcher:
    """The live ATS description fetch.

    A dependency rather than a direct call so tests can substitute it: without
    this seam, testing the detail route would mean either hitting a real board
    or monkeypatching a module global.
    """
    return details.fetch_via_adapter


FetcherDep = Annotated[DetailFetcher, Depends(get_detail_fetcher)]

# A database id, bounded so an impossible one is a 422 rather than a 500.
DbIdPath = Annotated[int, Path(ge=0, le=MAX_DB_INT)]
DbIdQuery = Annotated[int | None, Query(ge=0, le=MAX_DB_INT)]
