"""Core pydantic models for the countercharge audit engine.

All money fields are integer cents (``StrictInt``); floats are never
accepted. Dates are ``datetime.date``. CPT descriptors are never stored
here (AMA license) -- only codes.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, StrictInt


class CodeType(StrEnum):
    CPT = "CPT"
    HCPCS = "HCPCS"
    REV = "REV"


class Setting(StrEnum):
    ER = "ER"
    OUTPATIENT = "OUTPATIENT"
    INPATIENT = "INPATIENT"
    PROFESSIONAL = "PROFESSIONAL"


class Network(StrEnum):
    IN = "IN"
    OUT = "OUT"


class FapTier(StrEnum):
    FREE = "FREE"
    DISCOUNT = "DISCOUNT"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class LineItem(BaseModel):
    line_id: str
    dos: date
    code: str
    code_type: CodeType
    rev_code: str | None = None
    modifiers: list[str] = []
    units: int = 1
    charge_cents: StrictInt
    description: str = ""


class Provider(BaseModel):
    name: str
    npi: str | None = None
    ein: str | None = None
    nonprofit: bool = False
    billing_email_domain: str | None = None
    hospital_id: str | None = None


class Totals(BaseModel):
    charges_cents: StrictInt
    adjustments_cents: StrictInt = 0
    payments_cents: StrictInt = 0
    patient_balance_cents: StrictInt


class Bill(BaseModel):
    provider: Provider
    account_no: str
    statement_date: date
    setting: Setting
    pos: str | None = None
    lines: list[LineItem]
    totals: Totals
    self_pay: bool = False


class EOBLine(BaseModel):
    code: str
    dos: date
    billed_cents: StrictInt
    allowed_cents: StrictInt
    plan_paid_cents: StrictInt
    patient_resp_cents: StrictInt


class EOB(BaseModel):
    payer: str
    claim_no: str
    network: Network
    emergency: bool = False
    lines: list[EOBLine]
    total_patient_resp_cents: StrictInt


class Household(BaseModel):
    size: int
    annual_income_cents: StrictInt
    state: str  # 2-letter USPS code


class GFE(BaseModel):
    total_cents: StrictInt
    date: date


class Citation(BaseModel):
    dataset: str
    version: str
    url: str
    record: dict[str, str | int | None]


class Finding(BaseModel):
    finding_id: str = ""
    rule_id: str
    disputable: bool
    line_ids: list[str]
    amount_cents: StrictInt
    title: str
    detail: str
    citation: Citation
    evidence: dict = {}


class FapResult(BaseModel):
    hospital_id: str
    fpl_percent: int
    tier: FapTier
    free_max_fpl: int | None
    discount_max_fpl: int | None
    citation: Citation


class AuditReport(BaseModel):
    findings: list[Finding]
    advisory: list[Finding]
    disputable_cents: StrictInt
    fap: FapResult | None
    refdata_version: str
