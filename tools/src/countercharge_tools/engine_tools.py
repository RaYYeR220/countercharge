"""Gateway target ``engine`` (read-only, C1).

Six tools: ``audit_case`` runs the full engine audit against a case's
stored documents and persists signed findings; the rest are single-fact
lookups the agent can call mid-conversation without a full audit.
"""

from datetime import date

from countercharge_core.signing import Signer, sign_finding
from countercharge_core.store import CaseStore
from countercharge_engine import datasets
from countercharge_engine.audit import audit
from countercharge_engine.fpl import fpl_percent
from countercharge_engine.models import Bill, EOB, FapResult, FapTier, GFE, Household, Setting
from countercharge_engine.refdata.base import RefData
from countercharge_engine.rules.common import citation, mue_table, ptp_table

from countercharge_tools.deps import ToolError


def _latest_doc(docs: list[dict], kind: str) -> dict | None:
    """The last-added doc of ``kind`` (docs have no timestamp field; in the
    demo's single-bill-per-case flow there's normally only one anyway)."""
    matching = [d for d in docs if d.get("kind") == kind]
    return matching[-1] if matching else None


def audit_case(store: CaseStore, signer: Signer, refdata: RefData, *, case_id: str) -> dict:
    case = store.get_case(case_id)
    meta = case.get("meta")
    if meta is None:
        raise ToolError(f"case not found: {case_id}")

    docs = case["docs"]
    bill_doc = _latest_doc(docs, "bill")
    if bill_doc is None:
        raise ToolError(f"case {case_id} has no bill on file")
    bill = Bill.model_validate(bill_doc["payload"])

    eob_doc = _latest_doc(docs, "eob")
    eob = EOB.model_validate(eob_doc["payload"]) if eob_doc else None

    household_doc = _latest_doc(docs, "household")
    household = Household.model_validate(household_doc["payload"]) if household_doc else None

    gfe_doc = _latest_doc(docs, "gfe")
    gfe = GFE.model_validate(gfe_doc["payload"]) if gfe_doc else None

    report = audit(bill, refdata=refdata, eob=eob, household=household, gfe=gfe)

    all_findings = list(report.findings) + list(report.advisory)
    signatures = [sign_finding(signer, finding) for finding in all_findings]
    if all_findings:
        store.put_findings(case_id, all_findings, signatures)

    store.update_summary(
        case_id,
        meta["org"],
        status="audited",
        billed_cents=bill.totals.charges_cents,
        disputable_cents=report.disputable_cents,
    )

    return {
        "report": report.model_dump(mode="json"),
        "signed": [
            {"finding_id": finding.finding_id, "signature": signature}
            for finding, signature in zip(all_findings, signatures)
        ],
    }


def check_code_pair(refdata: RefData, *, code1: str, code2: str, setting: str, dos: str) -> dict:
    table = ptp_table(Setting(setting))
    dos_date = date.fromisoformat(dos)

    edit = refdata.ptp_edit(code1, code2, table, dos_date)
    if edit is None:
        edit = refdata.ptp_edit(code2, code1, table, dos_date)

    if edit is None:
        return {
            "edit": None,
            "disputable": False,
            "explanation": (
                "No NCCI procedure-to-procedure edit applies to this code "
                "pair on this date of service."
            ),
        }

    if edit.modifier_ind == 9:
        return {
            "edit": edit.model_dump(mode="json"),
            "disputable": False,
            "explanation": (
                "This pair is a documented, permitted exception to NCCI "
                "bundling (modifier indicator 9) and can't be disputed on "
                "PTP grounds."
            ),
        }

    if edit.modifier_ind == 1:
        explanation = (
            "These codes are bundled by NCCI, but an appropriate modifier "
            "on either line can unbundle them -- check whether one is "
            "present. Without one, billing both separately is disputable."
        )
    else:
        explanation = (
            "These codes can never be unbundled under NCCI edits; billing "
            "both separately is disputable."
        )

    return {"edit": edit.model_dump(mode="json"), "disputable": True, "explanation": explanation}


def check_units(refdata: RefData, *, code: str, units: int, setting: str) -> dict:
    table = mue_table(Setting(setting))
    mue_edit = refdata.mue(code, table)
    if mue_edit is None:
        return {"mue": None, "exceeds": False}
    return {"mue": mue_edit.model_dump(mode="json"), "exceeds": units > mue_edit.mue_value}


def fap_eligibility(
    refdata: RefData,
    *,
    hospital_id: str,
    household_size: int,
    annual_income_cents: int,
    state: str,
) -> dict | None:
    fap = refdata.hospital_fap(hospital_id)
    if fap is None:
        return None

    household = Household(size=household_size, annual_income_cents=annual_income_cents, state=state)
    year = date.today().year
    pct = fpl_percent(household, refdata, year)

    if fap.free_max_fpl is not None and pct <= fap.free_max_fpl:
        tier = FapTier.FREE
    elif fap.discount_max_fpl is not None:
        tier = FapTier.DISCOUNT if pct <= fap.discount_max_fpl else FapTier.NONE
    else:
        tier = FapTier.UNKNOWN

    result = FapResult(
        hospital_id=hospital_id,
        fpl_percent=pct,
        tier=tier,
        free_max_fpl=fap.free_max_fpl,
        discount_max_fpl=fap.discount_max_fpl,
        citation=citation(refdata, datasets.fap_dataset(hospital_id), {"hospital_id": hospital_id}),
    )
    return result.model_dump(mode="json")


def hospital_price(refdata: RefData, *, hospital_id: str, code: str) -> dict | None:
    price = refdata.hospital_price(hospital_id, code)
    return price.model_dump(mode="json") if price is not None else None


_RULE_EXPLANATIONS: dict[str, dict[str, str]] = {
    "DUPLICATE": {
        "title": "Duplicate charge",
        "plain_english": (
            "The same service, on the same date, with the same units and "
            "modifiers, was billed more than once. Only the first copy is a "
            "legitimate charge."
        ),
        "legal_basis": "Billing/coding accuracy -- not a specific statute.",
        "url": "",
    },
    "NCCI_PTP": {
        "title": "Bundled procedure billed separately (NCCI PTP edit)",
        "plain_english": (
            "Medicare's National Correct Coding Initiative says these two "
            "procedure codes shouldn't be billed as separate charges on the "
            "same day -- one is considered part of the other -- unless a "
            "specific modifier documents an exception."
        ),
        "legal_basis": "CMS National Correct Coding Initiative (NCCI) procedure-to-procedure edits.",
        "url": "https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits",
    },
    "MUE": {
        "title": "Units exceed Medicare's per-day limit (MUE)",
        "plain_english": (
            "Medicare publishes a maximum plausible number of units of this "
            "code per day; this bill exceeds it."
        ),
        "legal_basis": "CMS Medically Unlikely Edits (MUE).",
        "url": (
            "https://www.cms.gov/medicare/coding-billing/"
            "national-correct-coding-initiative-ncci-edits/medically-unlikely-edits"
        ),
    },
    "ARITHMETIC": {
        "title": "Bill math doesn't add up",
        "plain_english": (
            "The line charges, adjustments, payments and the balance shown "
            "on this bill don't add up the way the bill itself says they "
            "should."
        ),
        "legal_basis": "Bill-internal consistency -- not a specific statute.",
        "url": "",
    },
    "EOB_BALANCE_BILLING": {
        "title": "Billed above what your insurance says you owe",
        "plain_english": (
            "This is in-network care. Your insurer's explanation of "
            "benefits says you owe less than this bill charges; an "
            "in-network provider can't bill you above that."
        ),
        "legal_basis": "In-network provider contract / plan patient-responsibility terms.",
        "url": "",
    },
    "NSA_EMERGENCY": {
        "title": "Out-of-network emergency care billed above plan responsibility",
        "plain_english": (
            "Federal law caps what you can be billed for out-of-network "
            "emergency care at your plan's in-network patient "
            "responsibility amount."
        ),
        "legal_basis": "No Surprises Act, 42 USC 300gg-111 et seq.",
        "url": "https://www.cms.gov/nosurprises",
    },
    "NSA_GFE": {
        "title": "Billed well above your good-faith estimate",
        "plain_english": (
            "As a self-pay patient you got a good-faith estimate; the "
            "actual bill is at least $400 above it, which qualifies you to "
            "dispute the difference through the patient-provider dispute "
            "resolution process."
        ),
        "legal_basis": (
            "No Surprises Act good-faith-estimate / patient-provider dispute "
            "resolution (PPDR), 42 USC 300gg-112."
        ),
        "url": "https://www.cms.gov/nosurprises",
    },
    "CASH_PRICE": {
        "title": "Charged above the hospital's own posted cash price",
        "plain_english": (
            "This hospital publishes a discounted cash price for self-pay "
            "patients under the price-transparency rule; you were charged "
            "more than that for the same code."
        ),
        "legal_basis": "Hospital Price Transparency rule, 45 CFR 180.",
        "url": "https://www.cms.gov/hospital-price-transparency",
    },
    "FAP_501R": {
        "title": "Financial assistance policy eligibility",
        "plain_english": (
            "Nonprofit hospitals must offer a financial assistance policy. "
            "Based on your household size and income, you may qualify for "
            "free or discounted care."
        ),
        "legal_basis": "IRS 501(r), 26 USC 501(r).",
        "url": (
            "https://www.irs.gov/charities-non-profits/"
            "section-501r-requirements-for-charitable-hospital-organizations"
        ),
    },
    "MEDICARE_BENCHMARK": {
        "title": "Charged well above the Medicare rate (advisory)",
        "plain_english": (
            "This line was charged at three times or more the national "
            "Medicare rate for the same code. Medicare's rate isn't a legal "
            "ceiling, so this is informational, not a dispute on its own."
        ),
        "legal_basis": "Advisory comparison only -- not a specific statute.",
        "url": "",
    },
}


def explain_rule(*, rule_id: str) -> dict:
    meta = _RULE_EXPLANATIONS.get(rule_id)
    if meta is None:
        raise ToolError(f"unknown rule_id: {rule_id}")
    return {"rule_id": rule_id, **meta}
