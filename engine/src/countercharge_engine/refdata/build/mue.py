"""Parser for NCCI Medically Unlikely Edit (MUE) CSV tables.

The real practitioner, OPPS and DME MUE files are CSVs whose first cell
is a quoted, multi-line AMA copyright notice, followed by a header row
naming the table-specific second column (e.g. "Practitioner Services MUE
Values") before per-code data rows begin::

    "<AMA copyright notice ...>",,,
    "HCPCS/\nCPT Code","Practitioner Services MUE Values","MUE Adjudication Indicator","MUE Rationale"
    0001U,1,"2 Date of Service Edit: Policy","Code Descriptor / CPT Instruction"
    ...

The MUE Adjudication Indicator (MAI) cell is the digit 1, 2 or 3
followed by descriptive text; only the digit is the MAI value.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from countercharge_engine.refdata.base import MueEdit


def parse_mue_file(path: Path) -> Iterator[MueEdit]:
    """Yield every MUE row in one practitioner/OPPS/DME MUE CSV table."""
    with path.open(newline="", encoding="latin-1") as f:
        reader = csv.reader(f)
        rows = iter(reader)
        for row in rows:
            if row and row[0].strip().upper().startswith("HCPCS"):
                break
        for row in rows:
            if len(row) < 4:
                continue
            code, mue_value, mai_field, rationale = row[0], row[1], row[2], row[3]
            code = code.strip()
            mai_field = mai_field.strip()
            if not code or not mai_field:
                continue
            mai = int(mai_field.split()[0])
            yield MueEdit(
                code=code,
                mue_value=int(mue_value.strip()),
                mai=mai,
                rationale=rationale.strip(),
            )
