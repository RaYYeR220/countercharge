"""Hospitals registry (C6): load ``hospitals.json`` and gate recipient emails."""

import json
from pathlib import Path

from pydantic import BaseModel


class Hospital(BaseModel):
    hospital_id: str
    name: str
    nonprofit: bool
    billing_email_domain: str
    demo_billing_email: str
    free_max_fpl: int | None = None
    discount_max_fpl: int | None = None
    agb_pct: float | None = None
    source_url: str = ""
    retrieved: str = ""
    note: str = ""


def load_hospitals(path: str | Path) -> dict[str, Hospital]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {entry["hospital_id"]: Hospital(**entry) for entry in data}


def recipient_allowed(hospital: Hospital, email: str) -> bool:
    """Allowed recipients per C6: exact demo billing email or ``*@<billing_email_domain>``."""
    normalized = email.strip().lower()
    if normalized == hospital.demo_billing_email.strip().lower():
        return True
    domain = hospital.billing_email_domain.strip().lower()
    if not domain or "@" not in normalized:
        return False
    return normalized.rsplit("@", 1)[1] == domain
