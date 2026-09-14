"""Streaming parser for CMS hospital price-transparency machine-readable files.

Real files (CMS's standard-charge-information JSON template, v2.x/v3.x)
can run into the hundreds of megabytes, so this module streams with
``ijson`` and never holds the whole document in memory. Only items whose
CPT/HCPCS code is in the caller-supplied "codes of interest" set are
kept, and only one price row is produced per code even when a code
appears under more than one care setting.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path

import ijson

from countercharge_engine.refdata.base import HospitalPrice

# Prefer an outpatient/ER-relevant charge entry over an inpatient-only one
# when a code is billed in more than one setting; RefData.hospital_price()
# takes no setting argument, so exactly one row is kept per code.
_SETTING_PRIORITY = {"outpatient": 0, "both": 1, "inpatient": 2}


def _to_cents(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int((Decimal(str(value)) * 100).to_integral_value())
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_mrf_file(
    path: Path, hospital_id: str, codes_of_interest: set[str]
) -> Iterator[HospitalPrice]:
    """Stream a hospital's standard-charges JSON, yielding prices of interest."""
    with path.open("rb") as f:
        for item in ijson.items(f, "standard_charge_information.item"):
            matched_codes = {
                c.get("code")
                for c in item.get("code_information", [])
                if c.get("type") in ("CPT", "HCPCS") and c.get("code") in codes_of_interest
            }
            if not matched_codes:
                continue
            best = None
            best_rank = 4
            for charge in item.get("standard_charges", []):
                rank = _SETTING_PRIORITY.get(charge.get("setting"), 3)
                if rank < best_rank:
                    best, best_rank = charge, rank
            if best is None:
                continue
            for code in matched_codes:
                yield HospitalPrice(
                    hospital_id=hospital_id,
                    code=code,
                    setting=best.get("setting") or "",
                    gross_cents=_to_cents(best.get("gross_charge")),
                    cash_cents=_to_cents(best.get("discounted_cash")),
                    min_cents=_to_cents(best.get("minimum")),
                    max_cents=_to_cents(best.get("maximum")),
                )
