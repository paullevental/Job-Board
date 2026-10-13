"""Loading the company list from YAML into the database."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ats import SUPPORTED
from .models import Company

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "companies.yml"


@dataclass
class CompanySpec:
    name: str
    ats: str
    token: str
    tags: list[str]


def load_specs(path: str | Path | None = None) -> list[CompanySpec]:
    """Parse and validate the YAML company list."""
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"company config not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("companies") or []
    specs: list[CompanySpec] = []
    seen: set[tuple[str, str]] = set()

    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"companies[{i}] is not a mapping")
        name = str(entry.get("name") or "").strip()
        ats = str(entry.get("ats") or "").strip().lower()
        token = str(entry.get("token") or "").strip()  # case preserved: Ashby needs it
        if not (name and ats and token):
            raise ValueError(f"companies[{i}] missing name, ats or token")
        if ats not in SUPPORTED:
            raise ValueError(f"companies[{i}] ({name}): unsupported ats {ats!r}; supported: {', '.join(SUPPORTED)}")
        key = (ats, token)
        if key in seen:
            raise ValueError(f"duplicate board {ats}:{token}")
        seen.add(key)
        tags = entry.get("tags") or []
        specs.append(CompanySpec(name=name, ats=ats, token=token, tags=[str(t) for t in tags]))

    return specs


def sync_companies(session: Session, specs: list[CompanySpec]) -> tuple[int, int]:
    """Upsert specs into the companies table, returning (added, updated).

    Existing rows keep their active flag, so this never resurrects a pruned board.
    """
    added = updated = 0
    for spec in specs:
        existing = session.scalar(
            select(Company).where(Company.ats == spec.ats, Company.board_token == spec.token)
        )
        if existing is None:
            session.add(Company(
                name=spec.name,
                ats=spec.ats,
                board_token=spec.token,
                tags=",".join(spec.tags),
                active=True,
            ))
            added += 1
        else:
            changed = False
            if existing.name != spec.name:
                existing.name = spec.name
                changed = True
            joined = ",".join(spec.tags)
            if existing.tags != joined:
                existing.tags = joined
                changed = True
            if changed:
                updated += 1
    return added, updated
