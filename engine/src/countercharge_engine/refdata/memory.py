"""In-memory RefData fixture, for rule unit tests.

``ptp`` / ``mue`` seed edits that apply regardless of which CMS table
(``prac``/``opps``/``dme``) is queried. ``ptp_prac`` / ``ptp_opps`` and
``mue_prac`` / ``mue_opps`` / ``mue_dme`` seed edits scoped to one
specific table only -- ``ptp_edit(..., table=...)`` / ``mue(...,
table=...)`` filter accordingly, mirroring the real per-table filtering
:class:`countercharge_engine.refdata.sqlite.SqliteRefData` performs
against its ``tbl`` column.
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
        ptp_prac: list[PtpEdit] | None = None,
        ptp_opps: list[PtpEdit] | None = None,
        mue_prac: list[MueEdit] | None = None,
        mue_opps: list[MueEdit] | None = None,
        mue_dme: list[MueEdit] | None = None,
        rates: dict[tuple[str, bool], int] | None = None,
        hcpcs2: dict[str, str] | None = None,
        prices: dict[tuple[str, str], HospitalPrice] | None = None,
        faps: dict[str, HospitalFap] | None = None,
        fpl: dict[tuple[int, str], tuple[int, int]] | None = None,
        infos: dict[str, DatasetInfo] | None = None,
    ) -> None:
        self._ptp = list(ptp or [])
        self._ptp_by_table: dict[str, list[PtpEdit]] = {
            "prac": list(ptp_prac or []),
            "opps": list(ptp_opps or []),
        }
        self._mue = list(mue or [])
        self._mue_by_table: dict[str, list[MueEdit]] = {
            "prac": list(mue_prac or []),
            "opps": list(mue_opps or []),
            "dme": list(mue_dme or []),
        }
        self._rates = dict(rates or {})
        self._hcpcs2 = dict(hcpcs2 or {})
        self._prices = dict(prices or {})
        self._faps = dict(faps or {})
        self._fpl = dict(fpl or {})
        self._infos = dict(infos or {})

    def ptp_edit(
        self, col1: str, col2: str, table: Literal["prac", "opps"], dos: date
    ) -> PtpEdit | None:
        candidates = self._ptp + self._ptp_by_table.get(table, [])
        for edit in candidates:
            if edit.col1 != col1 or edit.col2 != col2:
                continue
            if edit.effective > dos:
                continue
            if edit.deleted is not None and edit.deleted <= dos:
                continue
            return edit
        return None

    def mue(self, code: str, table: Literal["prac", "opps", "dme"]) -> MueEdit | None:
        candidates = self._mue + self._mue_by_table.get(table, [])
        for edit in candidates:
            if edit.code == code:
                return edit
        return None

    def medicare_rate_cents(self, code: str, facility: bool) -> int | None:
        return self._rates.get((code, facility))

    def hcpcs2_desc(self, code: str) -> str | None:
        return self._hcpcs2.get(code)

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
