"""Offline CMS refdata builder.

Downloads (and caches) CMS public datasets, parses them, and writes
``refdata.sqlite`` per the schema in
:mod:`countercharge_engine.refdata.sqlite`. All network access happens
here -- the audit engine itself never touches the network.

Usage::

    uv run python -m countercharge_engine.refdata.build --out data/refdata.sqlite [--skip-mrf]
"""

from __future__ import annotations

import argparse
import sqlite3
import zipfile
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from countercharge_engine import datasets
from countercharge_engine.refdata.build import sources
from countercharge_engine.refdata.build.fap import load_hospital_faps
from countercharge_engine.refdata.build.fpl import ROWS as FPL_ROWS
from countercharge_engine.refdata.build.fpl import YEAR as FPL_YEAR
from countercharge_engine.refdata.build.hcpcs import parse_hcpcs2_file
from countercharge_engine.refdata.build.mrf import parse_mrf_file
from countercharge_engine.refdata.build.mue import parse_mue_file
from countercharge_engine.refdata.build.ncci import keep_ptp_edit, parse_ptp_files
from countercharge_engine.refdata.build.rates import parse_rates_file
from countercharge_engine.refdata.build.sources import Source
from countercharge_engine.refdata.sqlite import SCHEMA_SQL

ENGINE_DIR = Path(__file__).resolve().parents[4]
DATA_DIR = ENGINE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

# NCCI edits deleted well in the past are irrelevant to a bill audit and make
# up the bulk of the raw PTP row count; dropping them keeps refdata.sqlite a
# reasonable size. Anything still active, or deleted recently enough that a
# 2024+ date of service could still fall under it, is kept. See
# "NCCI PTP deletion cutoff" in engine/data/REFDATA.md for the full
# rationale and its (conservative-only) correctness implication.
_PTP_DELETION_CUTOFF = date(2024, 1, 1)


# --------------------------------------------------------------------------
# download / extract helpers
# --------------------------------------------------------------------------


def _download(url: str, raw_dir: Path, client: httpx.Client) -> Path:
    name = Path(urlsplit(url).path).name
    dest = raw_dir / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    raw_dir.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url, follow_redirects=True, timeout=300.0) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    return dest


def _extract(zip_path: Path, raw_dir: Path) -> list[Path]:
    into = raw_dir / "extracted" / zip_path.stem
    if not into.exists():
        into.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(into)
    return sorted(p for p in into.rglob("*") if p.is_file())


def _pick(
    files: Iterable[Path],
    *,
    contains: tuple[str, ...] = (),
    excludes: tuple[str, ...] = (),
    suffixes: tuple[str, ...] = (),
) -> Path:
    for p in files:
        name = p.name.lower()
        if suffixes and not name.endswith(suffixes):
            continue
        if not all(c.lower() in name for c in contains):
            continue
        if any(x.lower() in name for x in excludes):
            continue
        return p
    raise FileNotFoundError(
        f"no file matching contains={contains} suffixes={suffixes} excludes={excludes} "
        f"among {[p.name for p in files]}"
    )


def _load_codes_of_interest(path: Path) -> set[str]:
    codes: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        codes.add(line)
    return codes


# --------------------------------------------------------------------------
# per-dataset ingestion
# --------------------------------------------------------------------------


def _ingest_ptp(
    conn: sqlite3.Connection, client: httpx.Client, raw_dir: Path, source: Source, table: str
) -> int:
    txt_paths = []
    for url in source.files:
        zpath = _download(url, raw_dir, client)
        files = _extract(zpath, raw_dir)
        txt_paths.append(_pick(files, suffixes=(".txt",)))
    rows = (
        (
            table,
            e.col1,
            e.col2,
            e.modifier_ind,
            e.effective.isoformat(),
            e.deleted.isoformat() if e.deleted else None,
            e.rationale,
        )
        for e in parse_ptp_files(txt_paths)
        if keep_ptp_edit(e, _PTP_DELETION_CUTOFF)
    )
    conn.executemany(
        "INSERT INTO ptp (tbl, col1, col2, modifier_ind, effective, deleted, rationale) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM ptp WHERE tbl = ?", (table,)).fetchone()[0]


def _ingest_mue(
    conn: sqlite3.Connection, client: httpx.Client, raw_dir: Path, source: Source, table: str
) -> int:
    zpath = _download(source.files[0], raw_dir, client)
    files = _extract(zpath, raw_dir)
    csv_path = _pick(files, suffixes=(".csv",))
    rows = (
        (table, e.code, e.mue_value, e.mai, e.rationale) for e in parse_mue_file(csv_path)
    )
    conn.executemany(
        "INSERT INTO mue (tbl, code, mue_value, mai, rationale) VALUES (?,?,?,?,?)", rows
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM mue WHERE tbl = ?", (table,)).fetchone()[0]


def _ingest_rates(conn: sqlite3.Connection, client: httpx.Client, raw_dir: Path, source: Source) -> int:
    zpath = _download(source.files[0], raw_dir, client)
    files = _extract(zpath, raw_dir)
    csv_path = _pick(files, contains=("pprrvu", "nonqpp"), suffixes=(".csv",))
    rows = ((r.code, int(r.facility), r.cents) for r in parse_rates_file(csv_path))
    conn.executemany("INSERT INTO rates (code, facility, cents) VALUES (?,?,?)", rows)
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM rates").fetchone()[0]


def _ingest_hcpcs2(conn: sqlite3.Connection, client: httpx.Client, raw_dir: Path, source: Source) -> int:
    zpath = _download(source.files[0], raw_dir, client)
    files = _extract(zpath, raw_dir)
    txt_path = _pick(
        files, contains=("anweb",), excludes=("transaction",), suffixes=(".txt",)
    )
    rows = parse_hcpcs2_file(txt_path)
    conn.executemany("INSERT INTO hcpcs2 (code, short_desc) VALUES (?,?)", rows)
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM hcpcs2").fetchone()[0]


def _ingest_fpl(conn: sqlite3.Connection) -> int:
    rows = ((FPL_YEAR, state, first_cents, add_cents) for state, first_cents, add_cents in FPL_ROWS)
    conn.executemany(
        "INSERT INTO fpl (year, state, first_cents, add_cents) VALUES (?,?,?,?)", rows
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM fpl").fetchone()[0]


def _ingest_mrf(
    conn: sqlite3.Connection,
    client: httpx.Client,
    raw_dir: Path,
    source: Source,
    hospital_id: str,
    codes: set[str],
) -> int:
    zpath = _download(source.files[0], raw_dir, client)
    files = _extract(zpath, raw_dir)
    json_path = _pick(files, suffixes=(".json",))
    rows = (
        (p.hospital_id, p.code, p.setting, p.gross_cents, p.cash_cents, p.min_cents, p.max_cents)
        for p in parse_mrf_file(json_path, hospital_id, codes)
    )
    conn.executemany(
        "INSERT INTO hospital_price "
        "(hospital_id, code, setting, gross_cents, cash_cents, min_cents, max_cents) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM hospital_price").fetchone()[0]


def _ingest_fap(conn: sqlite3.Connection, hospitals_path: Path) -> int:
    rows = (
        (h.hospital_id, h.name, h.free_max_fpl, h.discount_max_fpl, h.agb_pct, h.source_url, h.retrieved.isoformat())
        for h in load_hospital_faps(hospitals_path)
    )
    conn.executemany(
        "INSERT INTO hospital_fap "
        "(hospital_id, name, free_max_fpl, discount_max_fpl, agb_pct, source_url, retrieved) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM hospital_fap").fetchone()[0]


# --------------------------------------------------------------------------
# sqlite setup, smoke test, report
# --------------------------------------------------------------------------


def _connect(out_path: Path) -> sqlite3.Connection:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    conn = sqlite3.connect(str(out_path))
    conn.executescript(SCHEMA_SQL)
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = MEMORY")
    return conn


def _smoke_test(conn: sqlite3.Connection, *, skip_mrf: bool) -> None:
    checks: list[tuple[str, bool]] = []

    row = conn.execute("SELECT 1 FROM ptp WHERE col1 = '99285' LIMIT 1").fetchone()
    checks.append(("PTP edit exists for col1 99285", row is not None))

    row = conn.execute("SELECT 1 FROM mue WHERE code = '85025' LIMIT 1").fetchone()
    checks.append(("MUE exists for 85025", row is not None))

    row = conn.execute(
        "SELECT cents FROM rates WHERE code = '99213' AND facility = 0"
    ).fetchone()
    rate_ok = row is not None and 5000 <= row[0] <= 20000
    checks.append(("medicare_rate_cents(99213, False) in [5000, 20000]", rate_ok))

    if not skip_mrf:
        row = conn.execute("SELECT COUNT(*) FROM hospital_price").fetchone()
        checks.append(("NYP price rows > 0", row[0] > 0))

    for name, ok in checks:
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    failed = [name for name, ok in checks if not ok]
    if failed:
        raise SystemExit(f"smoke test failed: {'; '.join(failed)}")


def _write_refdata_md(path: Path, rows: list[tuple[str, str, str, int, str]]) -> None:
    lines = [
        "# Reference data",
        "",
        "Built by `countercharge_engine.refdata.build`. Each row below is one "
        "dataset baked into `refdata.sqlite`, with its exact CMS/HHS release "
        "version, source URL, row count and retrieval date.",
        "",
        "| dataset | version | url | rows | retrieved |",
        "|---|---|---|---|---|",
    ]
    for dataset, version, url, count, retrieved in rows:
        lines.append(f"| {dataset} | {version} | {url} | {count} | {retrieved} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="countercharge_engine.refdata.build")
    parser.add_argument("--out", type=Path, default=DATA_DIR / "refdata.sqlite")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--hospital-id", default="nyp")
    parser.add_argument("--skip-mrf", action="store_true")
    args = parser.parse_args(argv)

    codes = _load_codes_of_interest(DATA_DIR / "codes_of_interest.txt")
    retrieved = date.today().isoformat()
    refdata_rows: list[tuple[str, str, str, int, str]] = []

    conn = _connect(args.out)

    def record(source: Source, count: int) -> None:
        conn.execute(
            "INSERT INTO datasets (dataset, version, url) VALUES (?,?,?)",
            (source.dataset, source.version, source.page_url),
        )
        refdata_rows.append((source.dataset, source.version, source.page_url, count, retrieved))

    with httpx.Client(headers={"User-Agent": "Mozilla/5.0 countercharge-refdata-builder"}) as client:
        n = _ingest_ptp(conn, client, args.raw_dir, sources.NCCI_PTP_PRAC, "prac")
        print(f"ptp[prac]: {n} rows")
        record(sources.NCCI_PTP_PRAC, n)

        n = _ingest_ptp(conn, client, args.raw_dir, sources.NCCI_PTP_OPPS, "opps")
        print(f"ptp[opps]: {n} rows")
        record(sources.NCCI_PTP_OPPS, n)

        for table, source in (
            ("prac", sources.NCCI_MUE_PRAC),
            ("opps", sources.NCCI_MUE_OPPS),
            ("dme", sources.NCCI_MUE_DME),
        ):
            n = _ingest_mue(conn, client, args.raw_dir, source, table)
            print(f"mue[{table}]: {n} rows")
            record(source, n)

        n = _ingest_rates(conn, client, args.raw_dir, sources.PFS_RVU)
        print(f"rates: {n} rows")
        record(sources.PFS_RVU, n)

        n = _ingest_hcpcs2(conn, client, args.raw_dir, sources.HCPCS2)
        print(f"hcpcs2: {n} rows")
        record(sources.HCPCS2, n)

        n = _ingest_fpl(conn)
        print(f"fpl: {n} rows")
        record(sources.FPL, n)

        if args.skip_mrf:
            print("hospital_price: skipped (--skip-mrf)")
        else:
            n = _ingest_mrf(conn, client, args.raw_dir, sources.HPT_NYP, args.hospital_id, codes)
            print(f"hospital_price[{args.hospital_id}]: {n} rows")
            record(sources.HPT_NYP, n)

    hospitals_path = DATA_DIR / "hospitals.json"
    n = _ingest_fap(conn, hospitals_path)
    print(f"hospital_fap: {n} rows")
    for h in load_hospital_faps(hospitals_path):
        fap_dataset = datasets.fap_dataset(h.hospital_id)
        fap_version = f"policy retrieved {h.retrieved.isoformat()}"
        conn.execute(
            "INSERT INTO datasets (dataset, version, url) VALUES (?,?,?)",
            (fap_dataset, fap_version, h.source_url),
        )
        refdata_rows.append((fap_dataset, fap_version, h.source_url, 1, retrieved))

    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('version', ?)",
        (f"2026Q4 (built {retrieved})",),
    )
    conn.commit()

    print()
    _smoke_test(conn, skip_mrf=args.skip_mrf)
    conn.close()

    _write_refdata_md(DATA_DIR / "REFDATA.md", refdata_rows)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
