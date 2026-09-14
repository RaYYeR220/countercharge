"""``--only-mrf`` rebuilds just hospital_price (and its dataset row) in an
existing sqlite, without touching any other table -- so a codes_of_interest
expansion (or a refreshed hospital price-transparency file) doesn't require
re-downloading and re-parsing every other CMS dataset.

This exercises the real cached NYP price-transparency file already present
under engine/data/raw/ (see refdata/build/sources.py's HPT_NYP), so it runs
fully offline -- no network access, no fixtures.
"""

import sqlite3
from pathlib import Path

from countercharge_engine.refdata.build.__main__ import RAW_DIR, _connect, main
from countercharge_engine.refdata.build.sources import HPT_NYP


def test_only_mrf_rebuilds_hospital_price_without_touching_other_tables(tmp_path: Path):
    db_path = tmp_path / "refdata.sqlite"

    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO ptp (tbl, col1, col2, modifier_ind, effective, deleted, rationale) "
        "VALUES (?,?,?,?,?,?,?)",
        ("opps", "99285", "36415", 0, "2020-01-01", None, "untouched by --only-mrf"),
    )
    conn.execute(
        "INSERT INTO hospital_price "
        "(hospital_id, code, setting, gross_cents, cash_cents, min_cents, max_cents) "
        "VALUES (?,?,?,?,?,?,?)",
        ("nyp", "00000", "outpatient", 100, 100, None, None),
    )
    conn.execute(
        "INSERT INTO datasets (dataset, version, url) VALUES (?,?,?)",
        ("HPT-nyp", "stale-version", "https://stale.example/"),
    )
    conn.commit()
    conn.close()

    exit_code = main(
        ["--out", str(db_path), "--raw-dir", str(RAW_DIR), "--only-mrf", "--hospital-id", "nyp"]
    )
    assert exit_code == 0

    conn = sqlite3.connect(str(db_path))
    try:
        # The pre-existing PTP row is untouched -- --only-mrf never opens
        # that code path.
        ptp_count = conn.execute("SELECT COUNT(*) FROM ptp").fetchone()[0]
        assert ptp_count == 1

        # The old dummy hospital_price row for "nyp" is gone, replaced by a
        # real, much larger set of rows parsed from the cached MRF file.
        rows = conn.execute(
            "SELECT COUNT(*) FROM hospital_price WHERE hospital_id = 'nyp'"
        ).fetchone()[0]
        assert rows > 1

        row = conn.execute(
            "SELECT code FROM hospital_price WHERE hospital_id = 'nyp' AND code = '00000'"
        ).fetchone()
        assert row is None

        # Exactly one HPT-nyp dataset row, carrying the real source version
        # -- not the stale dummy one, and not duplicated.
        dataset_rows = conn.execute(
            "SELECT version FROM datasets WHERE dataset = 'HPT-nyp'"
        ).fetchall()
        assert dataset_rows == [(HPT_NYP.version,)]
    finally:
        conn.close()
