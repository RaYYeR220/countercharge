"""Read-only reference-data helpers used while *authoring* the corpus.

This module talks directly to :class:`countercharge_engine.refdata.base.RefData`
(the same CMS/hospital reference tables the audit engine reads) so that
seed authors can ground a case's expected finding amounts in the real
NCCI PTP/MUE tables, a hospital's real posted cash price, and a real
hospital's real FAP thresholds -- instead of retyping numbers from a
one-off manual query and risking a transcription mistake.

It deliberately never imports ``countercharge_engine.audit`` or any
``countercharge_engine.rules.*`` module. Those modules *are* the engine
under test; importing them here would make the pre-registered answer
key circular (the key would come from running the engine, not from
seeding it). Everything below is a small, independent, auditable
re-derivation from raw reference-data lookups, written by reading the
rule modules' docstrings and source once during corpus design -- not by
calling them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from countercharge_engine.refdata.base import RefData

# Mirrors countercharge_engine.rules.common.NCCI_MODIFIERS. Copied as a
# plain data constant (the set of modifier codes CMS recognizes as
# unbundling a modifier-indicator-1 PTP edit) -- not imported from the
# rules package, per the module docstring above.
NCCI_MODIFIERS: frozenset[str] = frozenset(
    {
        "59", "XE", "XS", "XP", "XU", "25", "91",
        "LT", "RT", "E1", "E2", "E3", "E4",
        "FA", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9",
        "TA", "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9",
        "LC", "LD", "RC", "LM", "RI",
        "24", "27", "57", "78", "79",
    }
)


def edit_table(setting: str) -> str:
    return "prac" if setting == "PROFESSIONAL" else "opps"


@dataclass(frozen=True)
class PtpHit:
    col1_line_id: str
    col2_line_id: str
    modifier_ind: int
    amount_cents: int  # column-2 line's charge, per r2_ncci_ptp semantics


@dataclass(frozen=True)
class MueHit:
    mai: int
    target_key: str  # a single line_id (MAI 1) or "{code}|{dos}" (MAI 2/3)
    excess_units: int
    amount_cents: int


def scan_ptp_hits(lines: list[dict], setting: str, refdata: RefData) -> list[PtpHit]:
    """Every ordered same-day line pair that would fire NCCI_PTP, per the
    exact logic in ``rules/r2_ncci_ptp.py``."""
    table = edit_table(setting)
    hits: list[PtpHit] = []
    for a in lines:
        for b in lines:
            if a is b or a["dos"] != b["dos"]:
                continue
            edit = refdata.ptp_edit(a["code"], b["code"], table, date.fromisoformat(a["dos"]))
            if edit is None or edit.modifier_ind == 9:
                continue
            mods = set(a.get("modifiers", [])) | set(b.get("modifiers", []))
            if edit.modifier_ind == 1 and (mods & NCCI_MODIFIERS):
                continue
            hits.append(
                PtpHit(
                    col1_line_id=a["line_id"],
                    col2_line_id=b["line_id"],
                    modifier_ind=edit.modifier_ind,
                    amount_cents=b["charge_cents"],
                )
            )
    return hits


def scan_mue_hits(lines: list[dict], setting: str, refdata: RefData) -> list[MueHit]:
    """Every MUE excess that would fire, per the exact logic in
    ``rules/r3_mue.py`` (MAI 1 per-line, MAI 2/3 summed-per-day)."""
    table = edit_table(setting)
    groups: dict[tuple[str, str], list[dict]] = {}
    for line in lines:
        groups.setdefault((line["code"], line["dos"]), []).append(line)

    hits: list[MueHit] = []
    for (code, dos), grp in groups.items():
        mue = refdata.mue(code, table)
        if mue is None:
            continue
        if mue.mai == 1:
            for line in grp:
                if line["units"] > mue.mue_value:
                    excess = line["units"] - mue.mue_value
                    unit_price = line["charge_cents"] // line["units"]
                    hits.append(
                        MueHit(
                            mai=1,
                            target_key=line["line_id"],
                            excess_units=excess,
                            amount_cents=excess * unit_price,
                        )
                    )
        else:
            total_units = sum(line["units"] for line in grp)
            if total_units > mue.mue_value:
                total_charge = sum(line["charge_cents"] for line in grp)
                excess = total_units - mue.mue_value
                unit_price = total_charge // total_units
                hits.append(
                    MueHit(
                        mai=mue.mai,
                        target_key=f"{code}|{dos}",
                        excess_units=excess,
                        amount_cents=excess * unit_price,
                    )
                )
    return hits


def cash_price_overcharge(line: dict, hospital_id: str, refdata: RefData) -> int | None:
    """``None`` if the line isn't above the hospital's posted cash price
    (or no price is published); otherwise the overcharge in cents, per
    ``rules/r8_cash_price.py``."""
    price = refdata.hospital_price(hospital_id, line["code"])
    if price is None or price.cash_cents is None:
        return None
    expected = price.cash_cents * line["units"]
    if line["charge_cents"] <= expected:
        return None
    return line["charge_cents"] - expected


def fpl_percent(size: int, annual_income_cents: int, state: str, year: int, refdata: RefData) -> int:
    first_cents, add_cents = refdata.fpl_base(year, state)
    guideline = first_cents + add_cents * (size - 1)
    return (annual_income_cents * 100) // guideline


def fap_tier(
    hospital_id: str, size: int, annual_income_cents: int, state: str, year: int, refdata: RefData
) -> tuple[str, int]:
    """``(tier, fpl_percent)`` per ``rules/r9_fap.py``'s ``evaluate_fap``."""
    fap = refdata.hospital_fap(hospital_id)
    assert fap is not None, f"no hospital_fap row for {hospital_id!r}"
    pct = fpl_percent(size, annual_income_cents, state, year, refdata)
    if fap.free_max_fpl is not None and pct <= fap.free_max_fpl:
        return "FREE", pct
    if fap.discount_max_fpl is not None:
        return ("DISCOUNT" if pct <= fap.discount_max_fpl else "NONE"), pct
    return "UNKNOWN", pct


def assert_no_stray_edits(
    lines: list[dict],
    setting: str,
    refdata: RefData,
    *,
    allowed_ptp_pairs: frozenset[tuple[str, str]] = frozenset(),
    allowed_mue_keys: frozenset[str] = frozenset(),
    context: str = "",
) -> None:
    """Build-time guard: fail loudly if a case picks up an NCCI_PTP or MUE
    hit its author didn't declare. ``allowed_ptp_pairs`` holds
    ``(col1_line_id, col2_line_id)`` pairs; ``allowed_mue_keys`` holds MUE
    ``target_key`` values (see :func:`scan_mue_hits`)."""
    for hit in scan_ptp_hits(lines, setting, refdata):
        pair = (hit.col1_line_id, hit.col2_line_id)
        if pair not in allowed_ptp_pairs:
            raise AssertionError(
                f"{context}: unexpected NCCI_PTP hit {pair} (modifier_ind={hit.modifier_ind}, "
                f"amount={hit.amount_cents})"
            )
    for hit in scan_mue_hits(lines, setting, refdata):
        if hit.target_key not in allowed_mue_keys:
            raise AssertionError(
                f"{context}: unexpected MUE hit {hit.target_key} (mai={hit.mai}, "
                f"excess={hit.excess_units}, amount={hit.amount_cents})"
            )
