"""ASGI entry point: `uvicorn job_poller.api.asgi:app --reload`.

Importing this initializes the engine, which is also what creates the four new
tables on first start -- `Base.metadata.create_all` in `db.init_engine` means
no migration tool is needed until an existing column has to change.

The poller stays a separate process (`python -m job_poller watch`). This server
reads the database it writes; it does not own or supervise it.
"""

from __future__ import annotations

from ..db import init_engine
from .app import create_app

init_engine()
app = create_app()
