"""HTTP layer. Every endpoint reads through `queries.py` or writes through
`tracking.py` -- the API owns serialization and status codes, nothing else.
"""

from .app import create_app

__all__ = ["create_app"]
