"""Golden and orchestration tests for the audit() entry point and CLI.

Each golden case's Bill/EOB/Household fixture lives in tests/golden/ and is
loaded through the same pydantic models the CLI reads from disk, so they
double as sample CLI inputs. Expected cents are computed by hand in the
comments next to each assertion.
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

from countercharge_engine.audit import RULES, _disputable_cents, audit
from countercharge_engine.models import (
    Bill,
    Citation,
    CodeType,
    EOB,
    FapTier,
    Finding,
    Household,
    LineItem,
    Provider,
    Setting,
    Totals,
)
from countercharge_engine.refdata.base import DatasetInfo, HospitalFap, HospitalPrice, MueEdit, PtpEdit
from countercharge_engine.refdata.memory import MemoryRefData
from countercharge_engine.refdata.sqlite import SCHEMA_SQL
from countercharge_engine.rules import (
    r1_duplicate,
    r2_ncci_ptp,
    r3_mue,
    r4_arithmetic,
    r5_eob,
    r6_nsa_emergency,
    r7_nsa_gfe,
    r8_cash_price,
    r9_fap,
    r10_benchmark,
)

GOLDEN_DIR = Path(__file__).parent / "golden"


def _load(model, filename):
    data = json.loads((GOLDEN_DIR / filename).read_text(encoding="utf-8"))
    return model.model_validate(data)


def test_rules_list_is_r1_through_r10_in_order():
    assert RULES == [
        r1_duplicate.check,
        r2_ncci_ptp.check,
        r3_mue.check,
        r4_arithmetic.check,
        r5_eob.check,
        r6_nsa_emergency.check,
        r7_nsa_gfe.check,
        r8_cash_price.check,
        r9_fap.check,
        r10_benchmark.check,
    ]


# --- Case 1: clean ER bill -> nothing disputable --------------------------


def test_case1_clean_bill_has_no_findings():
    bill = _load(Bill, "case1_clean_bill.json")
    refdata = MemoryRefData()

    report = audit(bill, refdata=refdata)

    assert report.findings == []
    assert report.advisory == []
    assert report.disputable_cents == 0
    assert report.fap is None
    assert report.refdata_version == "memory-fixture"


# --- Case 2: duplicate + unbundled pair + MUE overage ---------------------


def _case2_refdata():
    return MemoryRefData(
        ptp_opps=[
            PtpEdit(
                col1="99285",
                col2="36415",
                modifier_ind=0,
                effective=date(2026, 1, 1),
                deleted=None,
                rationale="standards of medical/surgical practice",
            )
        ],
        mue_opps=[MueEdit(code="96374", mue_value=1, mai=2, rationale="policy")],
        infos={
            "NCCI-PTP-OPPS": DatasetInfo(
                dataset="NCCI-PTP-OPPS", version="2026Q4 v323r0", url="https://cms.gov/ncci"
            ),
            "NCCI-MUE-OPPS": DatasetInfo(
                dataset="NCCI-MUE-OPPS", version="2026Q4", url="https://cms.gov/mue"
            ),
        },
    )


def test_case2_duplicate_unbundle_and_mue_overage_exact_cents():
    bill = _load(Bill, "case2_bundle_dup_mue_bill.json")
    refdata = _case2_refdata()

    report = audit(bill, refdata=refdata)

    by_rule = {f.rule_id: f for f in report.findings}
    assert set(by_rule) == {"DUPLICATE", "NCCI_PTP", "MUE"}

    # DUPLICATE: l1 and l2 are identical 85025 lines at 3000c -> amount is
    # the copy's (l2's) charge: 3000.
    assert by_rule["DUPLICATE"].line_ids == ["l1", "l2"]
    assert by_rule["DUPLICATE"].amount_cents == 3000

    # NCCI_PTP: 36415 (l4, 1500c) is bundled into 99285 (l3) with
    # modifier_ind 0 -> amount is the column-2 line's (l4's) charge: 1500.
    assert by_rule["NCCI_PTP"].line_ids == ["l3", "l4"]
    assert by_rule["NCCI_PTP"].amount_cents == 1500

    # MUE: 96374 (l5) billed 3 units totalling 30000c (10000c/unit); MUE
    # value is 1 -> excess_units = 3 - 1 = 2, amount = 2 * 10000 = 20000.
    assert by_rule["MUE"].line_ids == ["l5"]
    assert by_rule["MUE"].amount_cents == 20000

    assert report.advisory == []
    assert report.fap is None

    # disputable_cents: none of the three findings above share a line id, so
    # the line-level sum is a plain sum: 3000 + 1500 + 20000 = 24500. No
    # balance-level rule fired (charges/balance both reconcile), and the
    # patient balance (117500) doesn't cap it.
    assert report.disputable_cents == 24500


# --- Case 3: self-pay nonprofit, household at 151% FPL ---------------------


def _case3_refdata():
    return MemoryRefData(
        fpl={(2026, "48"): (1_596_000, 568_000)},
        faps={
            "nyp": HospitalFap(
                hospital_id="nyp",
                name="NewYork-Presbyterian",
                free_max_fpl=250,
                discount_max_fpl=None,
                agb_pct=20,
                source_url="https://nyp.org/fap",
                retrieved=date(2026, 1, 15),
            )
        },
        prices={
            ("nyp", "99213"): HospitalPrice(
                hospital_id="nyp",
                code="99213",
                setting="OUTPATIENT",
                gross_cents=100000,
                cash_cents=60000,
                min_cents=None,
                max_cents=None,
            )
        },
        infos={
            "HPT-nyp": DatasetInfo(
                dataset="HPT-nyp", version="2026-01-15", url="https://nyp.org/cms-hpt.txt"
            ),
            "FAP-nyp": DatasetInfo(
                dataset="FAP-nyp", version="2026-01-15", url="https://nyp.org/fap"
            ),
        },
    )


def test_case3_self_pay_nonprofit_household_gets_fap_free_and_cash_price():
    bill = _load(Bill, "case3_bill.json")
    household = _load(Household, "case3_household.json")
    refdata = _case3_refdata()

    report = audit(bill, refdata=refdata, household=household)

    by_rule = {f.rule_id: f for f in report.findings}
    assert set(by_rule) == {"CASH_PRICE", "FAP_501R"}

    # CASH_PRICE: charged 90000c for 99213, hospital's cash price is
    # 60000c -> amount = 90000 - 60000 = 30000.
    assert by_rule["CASH_PRICE"].disputable is True
    assert by_rule["CASH_PRICE"].amount_cents == 30000

    # FAP_501R: household of 4, income 5,000,000c. 2026 48-state guideline =
    # 1,596,000 + 568,000 * 3 = 3,300,000c. pct = floor(5,000,000 * 100 /
    # 3,300,000) = floor(151.51...) = 151, which is <= free_max_fpl (250) ->
    # FREE, amount = patient_balance_cents = 90000. Non-disputable.
    assert by_rule["FAP_501R"].disputable is False
    assert by_rule["FAP_501R"].amount_cents == 90000

    assert report.advisory == []
    assert report.fap is not None
    assert report.fap.tier == FapTier.FREE
    assert report.fap.fpl_percent == 151

    # disputable_cents: only CASH_PRICE is disputable and line-level;
    # FAP_501R is excluded entirely. No overlapping lines, no balance-level
    # findings -> min(30000, patient_balance 90000) = 30000.
    assert report.disputable_cents == 30000


# --- Case 4: out-of-network emergency care above plan responsibility -------


def test_case4_out_of_network_emergency_triggers_nsa():
    bill = _load(Bill, "case4_bill.json")
    eob = _load(EOB, "case4_eob.json")
    refdata = MemoryRefData()

    report = audit(bill, refdata=refdata, eob=eob)

    assert [f.rule_id for f in report.findings] == ["NSA_EMERGENCY"]
    finding = report.findings[0]
    assert finding.disputable is True

    # bill's patient balance is 80000c, EOB says the plan-responsibility
    # amount is only 20000c -> amount = 80000 - 20000 = 60000.
    assert finding.amount_cents == 60000

    assert report.advisory == []
    assert report.fap is None

    # disputable_cents: no line-level findings fired, so the balance-level
    # max is the whole total: 60000. Capped at patient_balance_cents
    # (80000), which doesn't bind -> 60000.
    assert report.disputable_cents == 60000


# --- Dedupe: same line claimed by two line-level rules ---------------------


def test_dedupe_same_line_hit_by_duplicate_and_cash_price_counts_once_at_max():
    dos = date(2026, 3, 1)
    lines = [
        LineItem(
            line_id="l1", dos=dos, code="99213", code_type=CodeType.CPT, units=1, charge_cents=90000
        ),
        LineItem(
            line_id="l2", dos=dos, code="99213", code_type=CodeType.CPT, units=1, charge_cents=90000
        ),
    ]
    bill = Bill(
        provider=Provider(name="Test Hospital", hospital_id="nyp"),
        account_no="D1",
        statement_date=dos,
        setting=Setting.OUTPATIENT,
        self_pay=True,
        lines=lines,
        totals=Totals(charges_cents=180000, patient_balance_cents=180000),
    )
    refdata = MemoryRefData(
        prices={
            ("nyp", "99213"): HospitalPrice(
                hospital_id="nyp",
                code="99213",
                setting="OUTPATIENT",
                gross_cents=100000,
                cash_cents=50000,
                min_cents=None,
                max_cents=None,
            )
        },
        infos={
            "HPT-nyp": DatasetInfo(
                dataset="HPT-nyp", version="2026-01-15", url="https://nyp.org/cms-hpt.txt"
            )
        },
    )

    report = audit(bill, refdata=refdata)

    by_rule: dict[str, list] = {}
    for finding in report.findings:
        by_rule.setdefault(finding.rule_id, []).append(finding)

    # DUPLICATE: l1 and l2 are identical -> one finding, amount = copy's
    # (l2's) full charge: 90000.
    assert len(by_rule["DUPLICATE"]) == 1
    assert by_rule["DUPLICATE"][0].amount_cents == 90000

    # CASH_PRICE fires per-line: both l1 and l2 are billed at 90000c against
    # a 50000c cash price -> two findings, each 90000 - 50000 = 40000.
    assert len(by_rule["CASH_PRICE"]) == 2
    assert {f.amount_cents for f in by_rule["CASH_PRICE"]} == {40000}

    # disputable_cents: target keys are l2 (DUPLICATE's copy, and one of the
    # two CASH_PRICE findings) and l1 (the other CASH_PRICE finding). l2's
    # key takes the max of the two findings that share it -- max(90000,
    # 40000) = 90000 -- while l1's CASH_PRICE finding is a distinct, actually
    # disputed line and is not folded into l2's component just because
    # DUPLICATE's finding also happens to reference l1 as the "original":
    # 90000 (l2) + 40000 (l1) = 130000.
    assert report.disputable_cents == 130000


# --- Dedupe: target-key regression cases ------------------------------------


def test_dedupe_triple_duplicate_counts_each_extra_copy():
    # Reviewer's repro: the same line billed three times must count both
    # extra copies, not collapse to a single copy's amount.
    dos = date(2026, 3, 1)
    lines = [
        LineItem(
            line_id=f"l{i}", dos=dos, code="99213", code_type=CodeType.CPT, units=1, charge_cents=10000
        )
        for i in (1, 2, 3)
    ]
    bill = Bill(
        provider=Provider(name="Test Hospital"),
        account_no="D2",
        statement_date=dos,
        setting=Setting.OUTPATIENT,
        lines=lines,
        totals=Totals(charges_cents=30000, patient_balance_cents=30000),
    )

    report = audit(bill, refdata=MemoryRefData())

    assert len(report.findings) == 2
    assert all(f.rule_id == "DUPLICATE" for f in report.findings)

    # Two extra copies at 10000c each -- 20000c ($200) is individually proven
    # by the engine's own two findings, not the 10000c a naive shared-line
    # dedup would collapse them to.
    assert report.disputable_cents == 20000


def test_dedupe_ncci_ptp_and_duplicate_sharing_same_target_line():
    # l2 and l3 are identical copies of the same code, both bundled (as
    # NCCI's column-2 line) into l1's code. l3 is doubly implicated: it's
    # the NCCI_PTP finding's target *and* the DUPLICATE finding's target.
    # That shared key must be deduped to its max, not summed; l2's own,
    # unshared NCCI_PTP finding must still be counted in full.
    dos = date(2026, 3, 1)
    lines = [
        LineItem(line_id="l1", dos=dos, code="99285", code_type=CodeType.CPT, units=1, charge_cents=50000),
        LineItem(line_id="l2", dos=dos, code="36415", code_type=CodeType.CPT, units=1, charge_cents=1500),
        LineItem(line_id="l3", dos=dos, code="36415", code_type=CodeType.CPT, units=1, charge_cents=1500),
    ]
    bill = Bill(
        provider=Provider(name="Test Hospital"),
        account_no="D3",
        statement_date=dos,
        setting=Setting.ER,
        lines=lines,
        totals=Totals(charges_cents=53000, patient_balance_cents=53000),
    )
    refdata = MemoryRefData(
        ptp_opps=[
            PtpEdit(
                col1="99285",
                col2="36415",
                modifier_ind=0,
                effective=date(2026, 1, 1),
                deleted=None,
                rationale="standards of medical/surgical practice",
            )
        ],
        infos={
            "NCCI-PTP-OPPS": DatasetInfo(
                dataset="NCCI-PTP-OPPS", version="2026Q4 v323r0", url="https://cms.gov/ncci"
            )
        },
    )

    report = audit(bill, refdata=refdata)

    by_rule: dict[str, list] = {}
    for finding in report.findings:
        by_rule.setdefault(finding.rule_id, []).append(finding)

    # NCCI_PTP fires once for (l1, l2) and once for (l1, l3), 1500c each.
    assert len(by_rule["NCCI_PTP"]) == 2
    # DUPLICATE fires once: l2 is the original, l3 the copy, 1500c.
    assert len(by_rule["DUPLICATE"]) == 1

    # l2's NCCI_PTP finding (1500) is unshared -> counted in full. l3's
    # NCCI_PTP finding and its DUPLICATE finding share target_key=l3 (both
    # 1500) -> deduped to max(1500, 1500) = 1500, not summed to 3000.
    # Total: 1500 (l2) + 1500 (l3, deduped) = 3000.
    assert report.disputable_cents == 3000


def _mue_group_finding(amount: int, line_ids: list[str], mai: int = 3) -> Finding:
    code, dos = "96374", "2026-03-01"
    return Finding(
        rule_id="MUE",
        disputable=True,
        line_ids=line_ids,
        amount_cents=amount,
        title="Units exceed Medicare's per-day limit",
        detail="",
        citation=Citation(dataset="NCCI-MUE-OPPS", version="v", url="", record={}),
        evidence={"mai": mai, "target_key": f"{code}|{dos}"},
    )


def _line_finding(rule_id: str, line_id: str, amount: int) -> Finding:
    return Finding(
        rule_id=rule_id,
        disputable=True,
        line_ids=[line_id],
        amount_cents=amount,
        title="",
        detail="",
        citation=Citation(dataset="HPT-nyp", version="v", url="", record={}),
        evidence={"target_key": line_id},
    )


def test_dedupe_mue_group_overlap_uses_group_amount_when_larger():
    # l1 is inside the MUE group *and* is CASH_PRICE's own target -- the
    # group's contribution is max(group amount, sum of the overlapping
    # line-key amounts inside the group), here the group amount wins.
    findings = [
        _mue_group_finding(amount=20000, line_ids=["l1"]),
        _line_finding("CASH_PRICE", "l1", amount=3000),
    ]

    assert _disputable_cents(findings, patient_balance_cents=100000) == 20000


def test_dedupe_mue_group_overlap_uses_line_amounts_sum_when_larger():
    # Two lines in the same MUE group each separately over-charged on their
    # own cash-price terms; the sum of those two line-key amounts (16000)
    # exceeds the group's own amount (10000), so it wins instead.
    findings = [
        _mue_group_finding(amount=10000, line_ids=["l1", "l2"]),
        _line_finding("CASH_PRICE", "l1", amount=8000),
        _line_finding("CASH_PRICE", "l2", amount=8000),
    ]

    assert _disputable_cents(findings, patient_balance_cents=100000) == 16000


def test_dedupe_mue_group_with_no_overlap_counts_group_amount_once():
    findings = [_mue_group_finding(amount=20000, line_ids=["l1", "l2"])]

    assert _disputable_cents(findings, patient_balance_cents=100000) == 20000


# --- Determinism ------------------------------------------------------------


def test_audit_is_deterministic():
    bill = _load(Bill, "case2_bundle_dup_mue_bill.json")
    refdata = _case2_refdata()

    report1 = audit(bill, refdata=refdata)
    report2 = audit(bill, refdata=refdata)

    assert report1.model_dump_json() == report2.model_dump_json()


# --- CLI ---------------------------------------------------------------


def test_cli_schema_exports_json_schema_for_each_model(tmp_path):
    from countercharge_engine.__main__ import main

    out_dir = tmp_path / "schemas"
    exit_code = main(["schema", "--out", str(out_dir)])

    assert exit_code == 0
    for name in ("Bill", "EOB", "Household", "GFE", "Finding", "AuditReport"):
        schema = json.loads((out_dir / f"{name}.json").read_text(encoding="utf-8"))
        assert schema["title"] == name


def test_cli_audit_prints_report_json(tmp_path, capsys):
    from countercharge_engine.__main__ import main

    db_path = tmp_path / "refdata.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT INTO meta (key, value) VALUES ('version', 'test-1')")
    conn.commit()
    conn.close()

    exit_code = main(
        [
            "audit",
            "--bill",
            str(GOLDEN_DIR / "case1_clean_bill.json"),
            "--refdata",
            str(db_path),
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["disputable_cents"] == 0
    assert output["refdata_version"] == "test-1"
