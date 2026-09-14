"""2026 HHS Federal Poverty Guidelines.

The 48-contiguous-states-plus-DC figures and the higher, separately
published Alaska and Hawaii figures are all fixed for the whole
calendar year (HHS republishes them each January), so they are
hardcoded here rather than fetched at build time. All three were read
directly from the FY2026 ASPE detailed guidelines PDF:

    https://aspe.hhs.gov/sites/default/files/documents/
    b1bfa16b20ae9b89d525bc35de7c1643/detailed-guidelines-2026.pdf

    48 states + DC: 1-person $15,960/yr, +$5,680 per additional person.
    Alaska:         1-person $19,950/yr, +$7,100 per additional person.
    Hawaii:         1-person $18,360/yr, +$6,530 per additional person.

``countercharge_engine.refdata.sqlite`` stores the 48-state row under
the state key ``"48"``; Alaska and Hawaii use their own USPS codes.
"""

from __future__ import annotations

YEAR = 2026

# (state_key, first_person_cents, per_additional_person_cents)
ROWS: tuple[tuple[str, int, int], ...] = (
    ("48", 1_596_000, 568_000),
    ("AK", 1_995_000, 710_000),
    ("HI", 1_836_000, 653_000),
)
