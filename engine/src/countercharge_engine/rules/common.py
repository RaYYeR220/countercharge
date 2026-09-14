"""Shared types and helpers used by every rule module.

``AuditContext`` bundles everything a rule needs to inspect a single
bill; each rule module is a pure function of ``AuditContext`` to a list
of :class:`~countercharge_engine.models.Finding`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from countercharge_engine import datasets
from countercharge_engine.models import Bill, Citation, EOB, GFE, Household, Finding, Setting
from countercharge_engine.refdata.base import RefData


@dataclass(frozen=True)
class AuditContext:
    bill: Bill
    eob: EOB | None
    household: Household | None
    gfe: GFE | None
    refdata: RefData
    as_of: date


RuleFn = Callable[[AuditContext], list[Finding]]


def ptp_table(setting: Setting) -> Literal["prac", "opps"]:
    """NCCI PTP table to query for a given bill setting."""
    return "prac" if setting == Setting.PROFESSIONAL else "opps"


def mue_table(setting: Setting) -> Literal["prac", "opps", "dme"]:
    """NCCI MUE table to query for a given bill setting."""
    return "prac" if setting == Setting.PROFESSIONAL else "opps"


def ptp_dataset(table: Literal["prac", "opps"]) -> str:
    """Dataset key to cite for an NCCI PTP table, matching the refdata builder."""
    return datasets.PTP_DATASET[table]


def mue_dataset(table: Literal["prac", "opps", "dme"]) -> str:
    """Dataset key to cite for an NCCI MUE table, matching the refdata builder."""
    return datasets.MUE_DATASET[table]


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


def citation(refdata: RefData, dataset: str, record: dict) -> Citation:
    """Build a Citation for a CMS dataset via ``refdata.info(dataset)``."""
    info = refdata.info(dataset)
    return Citation(dataset=info.dataset, version=info.version, url=info.url, record=record)
