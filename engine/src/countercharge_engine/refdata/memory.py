"""In-memory RefData fixture, for rule unit tests.

``PtpEdit`` and ``MueEdit`` carry no notion of which CMS table
(``prac``/``opps``/``dme``) they were published under, so this fixture
does not filter by ``table`` -- tests seed only the edits relevant to
the scenario under test. :class:`countercharge_engine.refdata.sqlite.SqliteRefData`
performs real per-table filtering against its ``tbl`` column.
"""

from datetime import date
from typing import Literal

from countercharge_engine.refdata.base import (
    DatasetInfo,
    HospitalFap,
    HospitalPrice,
    MueEdit,
    PtpEdit,
)

_VERSION = "memory-fixture"


class MemoryRefData:
    def __init__(
        self,
        ptp: list[PtpEdit] | None = None,
        mue: list[MueEdit] | None = None,
        rates: dict[tuple[str, bool], int] | None = None,
        prices: dict[tuple[str, str], HospitalPrice] | None = None,
        faps: dict[str, HospitalFap] | None = None,
        fpl: dict[tuple[int, str], tuple[int, int]] | None = None,
        infos: dict[str, DatasetInfo] | None = None,
    ) -> None:
        self._ptp = list(ptp or [])
        self._mue = list(mue or [])
        self._rates = dict(rates or {})
        self._prices = dict(prices or {})
        self._faps = dict(faps or {})
        self._fpl = dict(fpl or {})
        self._infos = dict(infos or {})

    def ptp_edit(
        self, col1: str, col2: str, table: Literal["prac", "opps"], dos: date
    ) -> PtpEdit | None:
        for edit in self._ptp:
            if edit.col1 != col1 or edit.col2 != col2:
                continue
            if edit.effective > dos:
                continue
            if edit.deleted is not None and edit.deleted <= dos:
                continue
            return edit
        return None

    def mue(self, code: str, table: Literal["prac", "opps", "dme"]) -> MueEdit | None:
        for edit in self._mue:
            if edit.code == code:
                return edit
        return None

    def medicare_rate_cents(self, code: str, facility: bool) -> int | None:
        return self._rates.get((code, facility))

    def hcpcs2_desc(self, code: str) -> str | None:
        return None

    def fpl_base(self, year: int, state: str) -> tuple[int, int]:
        key_state = state if state in ("AK", "HI") else "48"
        return self._fpl[(year, key_state)]

    def hospital_price(self, hospital_id: str, code: str) -> HospitalPrice | None:
        return self._prices.get((hospital_id, code))

    def hospital_fap(self, hospital_id: str) -> HospitalFap | None:
        return self._faps.get(hospital_id)

    def info(self, dataset: str) -> DatasetInfo:
        return self._infos[dataset]

    @property
    def version(self) -> str:
        return _VERSION
