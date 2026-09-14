"""Parser for the Medicare Physician Fee Schedule Relative Value File (PPRRVU CSV).

The real file has a multi-row preamble (release title, AMA/ADA copyright
notices, a 5-row split column header) before data begins; the combined
header for the columns used here reads roughly::

    HCPCS,MOD,DESCRIPTION,CODE,PAYMENT,RVU,PE RVU,INDICATOR,PE RVU,INDICATOR,
    RVU,TOTAL,TOTAL,IND,DAYS,...,FACTOR,...

i.e. column 0 is the code, column 1 the modifier, column 11 the
non-facility total RVU, column 12 the facility total RVU. National rate
= total RVU x conversion factor, rounded to the nearest cent. Only the
un-modified ("global") row is used per code -- modifier-specific rows
(26 professional component, TC technical component, etc.) are skipped
since the engine has no notion of a modifier-qualified rate.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from countercharge_engine.refdata.build.sources import PFS_CONVERSION_FACTOR

_CONVERSION_FACTOR = Decimal(PFS_CONVERSION_FACTOR)

_CODE_COL = 0
_MODIFIER_COL = 1
_NON_FACILITY_TOTAL_COL = 11
_FACILITY_TOTAL_COL = 12


@dataclass(frozen=True)
class RateRow:
    code: str
    facility: bool
    cents: int


def _rvu_to_cents(raw: str) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        rvu = Decimal(raw)
    except Exception:
        return None
    if rvu <= 0:
        return None
    dollars = rvu * _CONVERSION_FACTOR
    return int((dollars * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_rates_file(path: Path) -> Iterator[RateRow]:
    """Yield national non-facility/facility rates for un-modified PFS codes."""
    with path.open(newline="", encoding="latin-1") as f:
        reader = csv.reader(f)
        rows = iter(reader)
        for row in rows:
            if row and row[0].strip().upper() == "HCPCS":
                break
        for row in rows:
            if len(row) <= _FACILITY_TOTAL_COL:
                continue
            code = row[_CODE_COL].strip()
            modifier = row[_MODIFIER_COL].strip()
            if not code or modifier:
                continue
            non_facility_cents = _rvu_to_cents(row[_NON_FACILITY_TOTAL_COL])
            if non_facility_cents is not None:
                yield RateRow(code=code, facility=False, cents=non_facility_cents)
            facility_cents = _rvu_to_cents(row[_FACILITY_TOTAL_COL])
            if facility_cents is not None:
                yield RateRow(code=code, facility=True, cents=facility_cents)
