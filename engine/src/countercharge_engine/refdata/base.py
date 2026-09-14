"""RefData protocol and the reference-data models it returns.

``RefData`` abstracts over how CMS reference data (NCCI edits, MUEs,
Medicare fee schedule rates, hospital prices, FAP thresholds, etc.) is
stored. Rule modules depend only on this protocol -- never on a concrete
backend -- so they can be unit-tested against :class:`MemoryRefData` and
run in production against :class:`SqliteRefData`.
"""

from datetime import date
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel


class PtpEdit(BaseModel):
    col1: str
    col2: str
    modifier_ind: int
    effective: date
    deleted: date | None
    rationale: str


class MueEdit(BaseModel):
    code: str
    mue_value: int
    mai: int
    rationale: str


class HospitalPrice(BaseModel):
    hospital_id: str
    code: str
    setting: str
    gross_cents: int | None
    cash_cents: int | None
    min_cents: int | None
    max_cents: int | None


class HospitalFap(BaseModel):
    hospital_id: str
    name: str
    free_max_fpl: int | None
    discount_max_fpl: int | None
    agb_pct: int | None
    source_url: str
    retrieved: date


class DatasetInfo(BaseModel):
    dataset: str
    version: str
    url: str


@runtime_checkable
class RefData(Protocol):
    def ptp_edit(
        self, col1: str, col2: str, table: Literal["prac", "opps"], dos: date
    ) -> PtpEdit | None:
        """Return the PTP edit for (col1, col2) active on ``dos``, if any."""
        ...

    def mue(self, code: str, table: Literal["prac", "opps", "dme"]) -> MueEdit | None:
        """Return the MUE edit for ``code`` in ``table``, if any."""
        ...

    def medicare_rate_cents(self, code: str, facility: bool) -> int | None:
        """Return the Medicare national rate in cents for ``code``, if known."""
        ...

    def hcpcs2_desc(self, code: str) -> str | None:
        """Return the HCPCS Level II short descriptor for ``code``, if known."""
        ...

    def fpl_base(self, year: int, state: str) -> tuple[int, int]:
        """Return (first_person_cents, per_additional_cents) for the FPL guideline."""
        ...

    def hospital_price(self, hospital_id: str, code: str) -> HospitalPrice | None:
        """Return the hospital's posted price for ``code``, if known."""
        ...

    def hospital_fap(self, hospital_id: str) -> HospitalFap | None:
        """Return the hospital's financial assistance policy thresholds, if known."""
        ...

    def info(self, dataset: str) -> DatasetInfo:
        """Return version/citation metadata for a named dataset."""
        ...

    @property
    def version(self) -> str:
        """Return the overall refdata bundle version."""
        ...
