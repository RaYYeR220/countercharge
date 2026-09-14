"""Canonical dataset key constants shared by rules and the refdata builder.

Every rule that cites a CMS dataset asks ``refdata.info(dataset)`` for that
dataset's version/url metadata. That call only succeeds if the key the rule
asks for is exactly the key the offline builder wrote into the sqlite
``datasets`` table (see ``refdata/build/__main__.py``'s ``record()``). This
module is the single place both sides read the key from, so the two can't
drift apart the way R3's MUE citation once did (it asked for ``MUE-OPPS``
while the builder had written ``NCCI-MUE-OPPS``, and `SqliteRefData.info()`
raised ``KeyError`` on every real MUE finding).
"""

from typing import Literal

NCCI_PTP_PRAC = "NCCI-PTP-PRAC"
NCCI_PTP_OPPS = "NCCI-PTP-OPPS"
NCCI_MUE_PRAC = "NCCI-MUE-PRAC"
NCCI_MUE_OPPS = "NCCI-MUE-OPPS"
NCCI_MUE_DME = "NCCI-MUE-DME"
PFS_RVU = "PFS-RVU"
HCPCS2 = "HCPCS2"
FPL = "FPL"

PTP_DATASET: dict[Literal["prac", "opps"], str] = {
    "prac": NCCI_PTP_PRAC,
    "opps": NCCI_PTP_OPPS,
}

MUE_DATASET: dict[Literal["prac", "opps", "dme"], str] = {
    "prac": NCCI_MUE_PRAC,
    "opps": NCCI_MUE_OPPS,
    "dme": NCCI_MUE_DME,
}


def hpt_dataset(hospital_id: str) -> str:
    """Per-hospital price-transparency (MRF) dataset key."""
    return f"HPT-{hospital_id}"


def fap_dataset(hospital_id: str) -> str:
    """Per-hospital financial-assistance-policy dataset key."""
    return f"FAP-{hospital_id}"
