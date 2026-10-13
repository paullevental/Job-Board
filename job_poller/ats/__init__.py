"""ATS vendor adapters."""

from .ashby import AshbyAdapter
from .base import ATSAdapter, FetchResult, RawPosting
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter

ADAPTERS: dict[str, ATSAdapter] = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "ashby": AshbyAdapter(),
}

SUPPORTED = sorted(ADAPTERS)


def get_adapter(name: str) -> ATSAdapter:
    try:
        return ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown ATS {name!r}; supported: {', '.join(SUPPORTED)}") from None


__all__ = [
    "ADAPTERS",
    "SUPPORTED",
    "ATSAdapter",
    "FetchResult",
    "RawPosting",
    "get_adapter",
    "GreenhouseAdapter",
    "LeverAdapter",
    "AshbyAdapter",
]
