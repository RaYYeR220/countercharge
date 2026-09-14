"""SQLite-backed RefData implementation for production use.

``SCHEMA_SQL`` is the DDL the offline refdata builder uses to create
``refdata.sqlite``; this module only reads that database.
"""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal

from countercharge_engine.refdata.base import (
    DatasetInfo,
    HospitalFap,
    HospitalPrice,
    MueEdit,
    PtpEdit,
)

SCHEMA_SQL = """
CREATE TABLE ptp (
    tbl TEXT NOT NULL,
    col1 TEXT NOT NULL,
    col2 TEXT NOT NULL,
    modifier_ind INTEGER NOT NULL,
    effective TEXT NOT NULL,
    deleted TEXT NULL,
    rationale TEXT NOT NULL
);
CREATE INDEX idx_ptp_lookup ON ptp (tbl, col1, col2);

CREATE TABLE mue (
    tbl TEXT NOT NULL,
    code TEXT NOT NULL,
    mue_value INTEGER NOT NULL,
    mai INTEGER NOT NULL,
    rationale TEXT NOT NULL
);
CREATE INDEX idx_mue_lookup ON mue (tbl, code);

CREATE TABLE rates (
    code TEXT NOT NULL,
    facility INTEGER NOT NULL,
    cents INTEGER NOT NULL
);

CREATE TABLE hcpcs2 (
    code TEXT NOT NULL,
    short_desc TEXT NOT NULL
);

CREATE TABLE fpl (
    year INTEGER NOT NULL,
    state TEXT NOT NULL,
    first_cents INTEGER NOT NULL,
    add_cents INTEGER NOT NULL
);

CREATE TABLE hospital_price (
    hospital_id TEXT NOT NULL,
    code TEXT NOT NULL,
    setting TEXT NOT NULL,
    gross_cents INTEGER NULL,
    cash_cents INTEGER NULL,
    min_cents INTEGER NULL,
    max_cents INTEGER NULL
);

CREATE TABLE hospital_fap (
    hospital_id TEXT NOT NULL,
    name TEXT NOT NULL,
    free_max_fpl INTEGER NULL,
    discount_max_fpl INTEGER NULL,
    agb_pct INTEGER NULL,
    source_url TEXT NOT NULL,
    retrieved TEXT NOT NULL
);

CREATE TABLE datasets (
    dataset TEXT NOT NULL,
    version TEXT NOT NULL,
    url TEXT NOT NULL
);

CREATE TABLE meta (
    key TEXT NOT NULL,
    value TEXT NOT NULL
);
"""


class SqliteRefData:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row

    def ptp_edit(
        self, col1: str, col2: str, table: Literal["prac", "opps"], dos: date
    ) -> PtpEdit | None:
        dos_iso = dos.isoformat()
        row = self._conn.execute(
            """
            SELECT col1, col2, modifier_ind, effective, deleted, rationale
            FROM ptp
            WHERE tbl = ? AND col1 = ? AND col2 = ?
              AND effective <= ?
              AND (deleted IS NULL OR deleted > ?)
            ORDER BY effective DESC
            LIMIT 1
            """,
            (table, col1, col2, dos_iso, dos_iso),
        ).fetchone()
        if row is None:
            return None
        return PtpEdit(
            col1=row["col1"],
            col2=row["col2"],
            modifier_ind=row["modifier_ind"],
            effective=date.fromisoformat(row["effective"]),
            deleted=date.fromisoformat(row["deleted"]) if row["deleted"] else None,
            rationale=row["rationale"],
        )

    def mue(self, code: str, table: Literal["prac", "opps", "dme"]) -> MueEdit | None:
        row = self._conn.execute(
            "SELECT code, mue_value, mai, rationale FROM mue WHERE tbl = ? AND code = ?",
            (table, code),
        ).fetchone()
        if row is None:
            return None
        return MueEdit(**dict(row))

    def medicare_rate_cents(self, code: str, facility: bool) -> int | None:
        row = self._conn.execute(
            "SELECT cents FROM rates WHERE code = ? AND facility = ?",
            (code, int(facility)),
        ).fetchone()
        return row["cents"] if row is not None else None

    def hcpcs2_desc(self, code: str) -> str | None:
        row = self._conn.execute(
            "SELECT short_desc FROM hcpcs2 WHERE code = ?", (code,)
        ).fetchone()
        return row["short_desc"] if row is not None else None

    def fpl_base(self, year: int, state: str) -> tuple[int, int]:
        key_state = state if state in ("AK", "HI") else "48"
        row = self._conn.execute(
            "SELECT first_cents, add_cents FROM fpl WHERE year = ? AND state = ?",
            (year, key_state),
        ).fetchone()
        if row is None:
            raise KeyError(f"no FPL guideline for year={year} state={key_state}")
        return row["first_cents"], row["add_cents"]

    def hospital_price(self, hospital_id: str, code: str) -> HospitalPrice | None:
        row = self._conn.execute(
            """
            SELECT hospital_id, code, setting, gross_cents, cash_cents, min_cents, max_cents
            FROM hospital_price WHERE hospital_id = ? AND code = ?
            """,
            (hospital_id, code),
        ).fetchone()
        if row is None:
            return None
        return HospitalPrice(**dict(row))

    def hospital_fap(self, hospital_id: str) -> HospitalFap | None:
        row = self._conn.execute(
            """
            SELECT hospital_id, name, free_max_fpl, discount_max_fpl, agb_pct,
                   source_url, retrieved
            FROM hospital_fap WHERE hospital_id = ?
            """,
            (hospital_id,),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["retrieved"] = date.fromisoformat(data["retrieved"])
        return HospitalFap(**data)

    def info(self, dataset: str) -> DatasetInfo:
        row = self._conn.execute(
            "SELECT dataset, version, url FROM datasets WHERE dataset = ?", (dataset,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no dataset info for {dataset!r}")
        return DatasetInfo(**dict(row))

    @property
    def version(self) -> str:
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        return row["value"] if row is not None else ""
