"""Unit tests for the refdata builder's parsers, against tiny hand-made
fixture files mirroring the real CMS file layouts (see
``tests/fixtures/cms/``)."""

from datetime import date
from pathlib import Path

from countercharge_engine.refdata.build.fap import load_hospital_faps
from countercharge_engine.refdata.build.fpl import ROWS as FPL_ROWS
from countercharge_engine.refdata.build.fpl import YEAR as FPL_YEAR
from countercharge_engine.refdata.build.hcpcs import parse_hcpcs2_file
from countercharge_engine.refdata.build.mrf import parse_mrf_file
from countercharge_engine.refdata.build.mue import parse_mue_file
from countercharge_engine.refdata.build.ncci import parse_ptp_file, parse_ptp_files
from countercharge_engine.refdata.build.rates import parse_rates_file

FIXTURES = Path(__file__).parent / "fixtures" / "cms"


# --------------------------------------------------------------------------
# NCCI PTP
# --------------------------------------------------------------------------


def test_parse_ptp_file_reads_all_data_rows():
    edits = list(parse_ptp_file(FIXTURES / "ptp_sample.txt"))
    assert len(edits) == 4


def test_parse_ptp_file_active_deletion_is_none():
    edits = list(parse_ptp_file(FIXTURES / "ptp_sample.txt"))
    edit = next(e for e in edits if e.col1 == "99285" and e.col2 == "71046")
    assert edit.deleted is None
    assert edit.modifier_ind == 1
    assert edit.effective == date(2022, 1, 1)


def test_parse_ptp_file_parses_deletion_date():
    edits = list(parse_ptp_file(FIXTURES / "ptp_sample.txt"))
    edit = next(e for e in edits if e.col1 == "99213" and e.col2 == "99214")
    assert edit.deleted == date(2020, 1, 1)
    assert edit.modifier_ind == 9


def test_parse_ptp_file_ind0_row():
    edits = list(parse_ptp_file(FIXTURES / "ptp_sample.txt"))
    edit = next(e for e in edits if e.col1 == "36415" and e.col2 == "85025")
    assert edit.modifier_ind == 0
    assert edit.deleted is None


def test_parse_ptp_files_chains_multiple_parts():
    edits = list(parse_ptp_files([FIXTURES / "ptp_sample.txt", FIXTURES / "ptp_sample.txt"]))
    assert len(edits) == 8


# --------------------------------------------------------------------------
# NCCI MUE
# --------------------------------------------------------------------------


def test_parse_mue_file_reads_rows():
    edits = list(parse_mue_file(FIXTURES / "mue_sample.csv"))
    assert len(edits) == 3


def test_parse_mue_file_extracts_mai_digit():
    edits = {e.code: e for e in parse_mue_file(FIXTURES / "mue_sample.csv")}
    assert edits["85025"].mue_value == 2
    assert edits["85025"].mai == 3
    assert edits["96374"].mue_value == 1
    assert edits["96374"].mai == 3
    assert edits["99285"].mai == 2


# --------------------------------------------------------------------------
# PFS RVU rates
# --------------------------------------------------------------------------


def test_parse_rates_file_computes_cents_from_rvu_times_cf():
    rows = {(r.code, r.facility): r.cents for r in parse_rates_file(FIXTURES / "rvu_sample.csv")}
    # 2.85 total RVU * 33.4009 CF = $95.192565 -> 9519 cents
    assert rows[("99213", False)] == 9519
    # 1.72 total RVU * 33.4009 CF = $57.449548 -> 5745 cents
    assert rows[("99213", True)] == 5745


def test_parse_rates_file_skips_modifier_rows():
    codes = {r.code for r in parse_rates_file(FIXTURES / "rvu_sample.csv")}
    assert "0075T" not in codes


def test_parse_rates_file_skips_zero_rvu():
    codes = {r.code for r in parse_rates_file(FIXTURES / "rvu_sample.csv")}
    assert "99999" not in codes


# --------------------------------------------------------------------------
# HCPCS Level II
# --------------------------------------------------------------------------


def test_parse_hcpcs2_file_keeps_level2_codes_only():
    rows = dict(parse_hcpcs2_file(FIXTURES / "hcpcs_sample.txt"))
    assert rows["J1885"] == "Ketorolac tromethamine inj"
    assert rows["G0378"] == "Hospital observation per hr"


def test_parse_hcpcs2_file_excludes_cpt_numeric_codes():
    rows = dict(parse_hcpcs2_file(FIXTURES / "hcpcs_sample.txt"))
    assert "99213" not in rows


def test_parse_hcpcs2_file_excludes_terminated_codes():
    rows = dict(parse_hcpcs2_file(FIXTURES / "hcpcs_sample.txt"))
    assert "A9999" not in rows


def test_parse_hcpcs2_file_excludes_modifier_records():
    rows = dict(parse_hcpcs2_file(FIXTURES / "hcpcs_sample.txt"))
    assert "AA" not in rows


# --------------------------------------------------------------------------
# FPL
# --------------------------------------------------------------------------


def test_fpl_rows_48_states_guideline_matches_known_value():
    row = next(r for r in FPL_ROWS if r[0] == "48")
    _, first_cents, add_cents = row
    # 2026 HHS guideline for a household of 4, 48 states: $33,000.
    assert first_cents + add_cents * 3 == 33_000_00
    assert FPL_YEAR == 2026


def test_fpl_rows_include_ak_and_hi():
    states = {r[0] for r in FPL_ROWS}
    assert {"48", "AK", "HI"} <= states


# --------------------------------------------------------------------------
# Hospital price transparency MRF
# --------------------------------------------------------------------------


def test_parse_mrf_file_keeps_only_codes_of_interest():
    prices = {p.code: p for p in parse_mrf_file(FIXTURES / "mrf_sample.json", "test", {"85025", "99285"})}
    assert set(prices) == {"85025", "99285"}


def test_parse_mrf_file_converts_dollars_to_cents():
    prices = {p.code: p for p in parse_mrf_file(FIXTURES / "mrf_sample.json", "test", {"85025"})}
    price = prices["85025"]
    assert price.hospital_id == "test"
    assert price.gross_cents == 11300
    assert price.cash_cents == 11300
    assert price.min_cents == 544
    assert price.max_cents == 35460


def test_parse_mrf_file_prefers_outpatient_setting():
    prices = {p.code: p for p in parse_mrf_file(FIXTURES / "mrf_sample.json", "test", {"99285"})}
    price = prices["99285"]
    assert price.setting == "outpatient"
    assert price.gross_cents == 599000


# --------------------------------------------------------------------------
# Hospital FAP
# --------------------------------------------------------------------------


def test_load_hospital_faps_reads_all_entries():
    faps = {f.hospital_id: f for f in load_hospital_faps(FIXTURES / "hospitals_sample.json")}
    assert set(faps) == {"test1", "test2"}


def test_load_hospital_faps_preserves_null_thresholds():
    faps = {f.hospital_id: f for f in load_hospital_faps(FIXTURES / "hospitals_sample.json")}
    assert faps["test1"].free_max_fpl == 200
    assert faps["test1"].discount_max_fpl == 400
    assert faps["test2"].free_max_fpl is None
    assert faps["test2"].discount_max_fpl is None
    assert faps["test1"].retrieved == date(2026, 1, 15)
