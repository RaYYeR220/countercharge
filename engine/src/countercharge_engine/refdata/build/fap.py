"""Loader for hospital financial-assistance-policy (FAP) thresholds.

Thresholds are read from the hand-maintained ``engine/data/hospitals.json``,
which cites each hospital's real, currently published FAP policy
document. Any number not plainly stated in that policy is recorded as
``null`` there -- this loader never fills in a guess.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from countercharge_engine.refdata.base import HospitalFap


def load_hospital_faps(path: Path) -> Iterator[HospitalFap]:
    """Yield one :class:`HospitalFap` per entry in ``hospitals.json``."""
    entries = json.loads(path.read_text(encoding="utf-8"))
    for entry in entries:
        yield HospitalFap(
            hospital_id=entry["hospital_id"],
            name=entry["name"],
            free_max_fpl=entry.get("free_max_fpl"),
            discount_max_fpl=entry.get("discount_max_fpl"),
            agb_pct=entry.get("agb_pct"),
            source_url=entry["source_url"],
            retrieved=date.fromisoformat(entry["retrieved"]),
        )
