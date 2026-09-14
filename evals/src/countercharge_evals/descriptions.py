"""Line-item descriptions and revenue codes for the corpus.

Every description here is generic, plain-English wording written for
this corpus -- never AMA CPT descriptor text (CPT descriptors are
licensed and the engine itself never stores them, see
``countercharge_engine.models``). HCPCS Level II descriptors are public
domain and are pulled live from refdata's ``hcpcs2`` table instead.
"""

from countercharge_engine.refdata.base import RefData

# code -> (generic description, UB-04 revenue code, code_type)
_CPT: dict[str, tuple[str, str]] = {
    "99283": ("Emergency department visit, level 3", "0450"),
    "99284": ("Emergency department visit, level 4", "0450"),
    "99285": ("Emergency department visit, level 5", "0450"),
    "71046": ("Chest X-ray, two views", "0320"),
    "71100": ("Rib X-ray", "0320"),
    "72100": ("Lower spine X-ray", "0320"),
    "80053": ("Blood chemistry panel", "0301"),
    "85025": ("Complete blood count with differential", "0305"),
    "82310": ("Blood calcium test", "0301"),
    "87635": ("Respiratory virus lab test", "0306"),
    "36415": ("Blood draw", "0300"),
    "93000": ("EKG, complete", "0730"),
    "93005": ("EKG tracing", "0730"),
    "12001": ("Wound repair, simple, small", "0761"),
    "12002": ("Wound repair, simple, medium", "0761"),
    "12004": ("Wound repair, simple, larger", "0761"),
    "12005": ("Wound repair, simple, extensive", "0761"),
    "12011": ("Wound repair, simple, face", "0761"),
    "29125": ("Forearm splint application", "0761"),
    "96360": ("IV hydration infusion, first hour", "0260"),
    "96361": ("IV hydration infusion, each additional hour", "0260"),
    "96365": ("IV infusion, therapeutic, first hour", "0260"),
}

# HCPCS Level II codes: (fallback description if refdata lookup fails, revenue code)
_HCPCS: dict[str, tuple[str, str]] = {
    "A0425": ("Ground mileage", "0540"),
    "A4550": ("Surgical trays", "0270"),
    "J2250": ("Inj midazolam hydrochloride", "0636"),
}


def is_hcpcs(code: str) -> bool:
    return not code.isdigit()


def describe(code: str, refdata: RefData) -> tuple[str, str, str]:
    """Return ``(description, rev_code, code_type)`` for a code."""
    if is_hcpcs(code):
        fallback, rev = _HCPCS[code]
        desc = refdata.hcpcs2_desc(code) or fallback
        return desc, rev, "HCPCS"
    desc, rev = _CPT[code]
    return desc, rev, "CPT"
