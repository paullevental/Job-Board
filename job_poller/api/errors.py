"""One error shape for the whole API: `{"error": {"code", "message"}}`.

The frontend branches on `code`, so it is a stable contract and must not carry
a traceback or an internal message.
"""

from __future__ import annotations

from contextlib import contextmanager


class ApiError(Exception):
    """An error with a deliberate HTTP status and a stable machine code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def not_found(what: str, identifier: object) -> ApiError:
    return ApiError(404, "not_found", f"no {what} with id {identifier}")


@contextmanager
def cursor_errors():
    """Turn a malformed pagination cursor into a 422 rather than a 500.

    Cursors are opaque to the client, so a bad one means a stale or hand-edited
    link; the client's correct response is to restart paging from the top.
    """
    try:
        yield
    except ValueError as exc:
        raise ApiError(422, "invalid_cursor", str(exc)) from exc
