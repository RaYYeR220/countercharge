import sqlite3
from datetime import date
from pathlib import Path

from countercharge_engine.refdata.base import (
    DatasetInfo,
    HospitalFap,
    HospitalPrice,
    MueEdit,
    PtpEdit,
)
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.refdata.sqlite import SCHEMA_SQL, SqliteRefData


def _edit(effective, deleted=None, modifier_ind=0):
    return PtpEdit(
        col1="99285",
        col2="36415",
        modifier_ind=modifier_ind,
        effective=effective,
        deleted=deleted,
        rationale="standards of medical/surgical practice",
    )


def test_memory_ptp_active_by_dos():
    edit = _edit(effective=date(2026, 1, 1), deleted=date(2026, 6, 1))
    refdata = MemoryRefData(ptp=[edit])

    assert refdata.ptp_edit("99285", "36415", "opps", date(2026, 3, 1)) == edit

    # deleted before dos -> None
    assert refdata.ptp_edit("99285", "36415", "opps", date(2026, 7, 1)) is None

    # effective after dos -> None
    assert refdata.ptp_edit("99285", "36415", "opps", date(2025, 12, 31)) is None


def test_memory_fpl_maps_non_ak_hi_states_to_48():
    refdata = MemoryRefData(
        fpl={(2026, "48"): (1596000, 568000), (2026, "AK"): (1994000, 710000)}
    )

    assert refdata.fpl_base(2026, "NY") == (1596000, 568000)
    assert refdata.fpl_base(2026, "CA") == (1596000, 568000)
    assert refdata.fpl_base(2026, "AK") == (1994000, 710000)


def test_sqlite_refdata_schema_round_trip(tmp_path: Path):
    db_path = tmp_path / "refdata.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO ptp VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("opps", "99285", "36415", 1, "2026-01-01", None, "bundled"),
    )
    conn.execute(
        "INSERT INTO mue VALUES (?, ?, ?, ?, ?)",
        ("opps", "85025", 1, 3, "medically unlikely"),
    )
    conn.execute("INSERT INTO rates VALUES (?, ?, ?)", ("99213", 0, 9260))
    conn.execute(
        "INSERT INTO hcpcs2 VALUES (?, ?)", ("J1885", "Ketorolac tromethamine inj")
    )
    conn.execute("INSERT INTO fpl VALUES (?, ?, ?, ?)", (2026, "48", 1596000, 568000))
    conn.execute(
        "INSERT INTO hospital_price VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("nyp", "99285", "ER", 500000, 250000, 100000, 800000),
    )
    conn.execute(
        "INSERT INTO hospital_fap VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("nyp", "NewYork-Presbyterian", 250, 400, 20, "https://nyp.org/fap", "2026-01-15"),
    )
    conn.execute(
        "INSERT INTO datasets VALUES (?, ?, ?)",
        ("NCCI-PTP-OPPS", "2026Q4 v323r0", "https://cms.gov/ncci"),
    )
    conn.execute("INSERT INTO meta VALUES (?, ?)", ("version", "2026Q4"))
    conn.commit()
    conn.close()

    refdata = SqliteRefData(db_path)

    edit = refdata.ptp_edit("99285", "36415", "opps", date(2026, 6, 1))
    assert edit == PtpEdit(
        col1="99285",
        col2="36415",
        modifier_ind=1,
        effective=date(2026, 1, 1),
        deleted=None,
        rationale="bundled",
    )
    assert refdata.ptp_edit("99285", "36415", "prac", date(2026, 6, 1)) is None

    mue = refdata.mue("85025", "opps")
    assert mue == MueEdit(code="85025", mue_value=1, mai=3, rationale="medically unlikely")
    assert refdata.mue("85025", "prac") is None

    assert refdata.medicare_rate_cents("99213", False) == 9260
    assert refdata.medicare_rate_cents("99213", True) is None

    assert refdata.hcpcs2_desc("J1885") == "Ketorolac tromethamine inj"
    assert refdata.hcpcs2_desc("00000") is None

    assert refdata.fpl_base(2026, "TX") == (1596000, 568000)

    price = refdata.hospital_price("nyp", "99285")
    assert price == HospitalPrice(
        hospital_id="nyp",
        code="99285",
        setting="ER",
        gross_cents=500000,
        cash_cents=250000,
        min_cents=100000,
        max_cents=800000,
    )
    assert refdata.hospital_price("nyp", "00000") is None

    fap = refdata.hospital_fap("nyp")
    assert fap == HospitalFap(
        hospital_id="nyp",
        name="NewYork-Presbyterian",
        free_max_fpl=250,
        discount_max_fpl=400,
        agb_pct=20,
        source_url="https://nyp.org/fap",
        retrieved=date(2026, 1, 15),
    )
    assert refdata.hospital_fap("ccf") is None

    info = refdata.info("NCCI-PTP-OPPS")
    assert info == DatasetInfo(
        dataset="NCCI-PTP-OPPS", version="2026Q4 v323r0", url="https://cms.gov/ncci"
    )

    assert refdata.version == "2026Q4"
