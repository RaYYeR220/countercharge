"""codes_of_interest.txt drives which codes hospital_price() can ever return
a price for (see refdata/build/mrf.py); too small a list silently narrows
R8 (CASH_PRICE) coverage on real bills. The plan's target is ~300 real,
common codes across the categories a typical ER/outpatient bill audit
needs prices for."""

from pathlib import Path

from countercharge_engine.refdata.build.__main__ import DATA_DIR, _load_codes_of_interest

CODES_PATH = DATA_DIR / "codes_of_interest.txt"


def test_codes_of_interest_has_approximately_300_codes():
    codes = _load_codes_of_interest(CODES_PATH)
    assert len(codes) >= 290


def test_codes_of_interest_has_no_duplicate_lines():
    lines = [
        line.strip()
        for line in CODES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(lines) == len(set(lines))


def test_codes_of_interest_covers_required_categories():
    codes = _load_codes_of_interest(CODES_PATH)

    # ER E/M
    assert {"99281", "99282", "99283", "99284", "99285"} <= codes
    # Office/outpatient E/M
    assert {"99202", "99203", "99204", "99205", "99211", "99212", "99213", "99214", "99215"} <= codes
    # Critical care
    assert {"99291", "99292"} <= codes
    # Observation/hospital E/M
    assert {"99221", "99222", "99223", "99231", "99232", "99233"} <= codes
    # Cardiology
    assert {"93000", "93005", "93010"} <= codes
    # Infusion/injection administration
    assert {"96360", "96365", "96372", "96374", "96379"} <= codes
    # Venipuncture
    assert "36415" in codes
    # Wound repair
    assert {"12001", "12004", "13100", "13160"} <= codes
    # Splints/casts
    assert {"29105", "29125", "29405", "29515"} <= codes
    # Ambulance
    assert {"A0425", "A0426", "A0427", "A0428", "A0429", "A0433"} <= codes
