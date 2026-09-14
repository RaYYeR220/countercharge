"""CMS public data sources: exact URLs and version strings.

Each :class:`Source` records the dataset name and version string that
gets written to the sqlite ``datasets`` table, a human-checkable page
URL to confirm the release is still current, and the direct file URLs
to download. Version strings are the exact CMS release identifiers
(quarter, revision number, effective date) -- never synthesized.

CMS gates NCCI zip downloads behind an AMA click-through license; the
license page's "Accept" button is a GET form that simply appends
``?agree=yes`` to the file URL, which is reflected in the URLs below.
"""

from __future__ import annotations

from dataclasses import dataclass

from countercharge_engine import datasets


@dataclass(frozen=True)
class Source:
    dataset: str
    version: str
    page_url: str
    files: tuple[str, ...] = ()


_NCCI_PTP_PAGE = (
    "https://www.cms.gov/medicare/coding-billing/national-correct-coding-"
    "initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits"
)

NCCI_PTP_PRAC = Source(
    dataset=datasets.NCCI_PTP_PRAC,
    version="2026Q4 v323r0",
    page_url=_NCCI_PTP_PAGE,
    files=tuple(
        "https://www.cms.gov/files/zip/medicare-ncci-2026q4-practitioner-ptp-"
        f"edits-ccipra-v323r0-f{n}.zip?agree=yes"
        for n in (1, 2, 3, 4)
    ),
)

NCCI_PTP_OPPS = Source(
    dataset=datasets.NCCI_PTP_OPPS,
    version="2026Q4 v323r0",
    page_url=_NCCI_PTP_PAGE,
    files=tuple(
        "https://www.cms.gov/files/zip/medicare-ncci-2026q4-hospital-ptp-"
        f"edits-ccioph-v323r0-f{n}.zip?agree=yes"
        for n in (1, 2, 3, 4)
    ),
)

_NCCI_MUE_PAGE = (
    "https://www.cms.gov/medicare/coding-billing/national-correct-coding-"
    "initiative-ncci-edits/medicare-ncci-medically-unlikely-edits-mues"
)

NCCI_MUE_PRAC = Source(
    dataset=datasets.NCCI_MUE_PRAC,
    version="2026Q4 eff. 2026-10-01",
    page_url=_NCCI_MUE_PAGE,
    files=(
        "https://www.cms.gov/files/zip/medicare-ncci-2026-q4-practitioner-"
        "services-mue-table.zip?agree=yes",
    ),
)

NCCI_MUE_OPPS = Source(
    dataset=datasets.NCCI_MUE_OPPS,
    version="2026Q4 eff. 2026-10-01",
    page_url=_NCCI_MUE_PAGE,
    files=(
        "https://www.cms.gov/files/zip/medicare-ncci-2026-q4-facility-"
        "outpatient-hospital-services-mue-table.zip?agree=yes",
    ),
)

NCCI_MUE_DME = Source(
    dataset=datasets.NCCI_MUE_DME,
    version="2026Q4 eff. 2026-10-01",
    page_url=_NCCI_MUE_PAGE,
    files=(
        "https://www.cms.gov/files/zip/medicare-ncci-2026-q4-dme-supplier-"
        "services-mue-table.zip?agree=yes",
    ),
)

PFS_RVU = Source(
    dataset=datasets.PFS_RVU,
    version="RVU26B eff. 2026-05-01",
    page_url="https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files/rvu26b",
    files=("https://www.cms.gov/files/zip/rvu26b-updated-05-01-2026.zip",),
)

# National PFS conversion factor printed on every PPRRVU row for RVU26B.
PFS_CONVERSION_FACTOR = "33.4009"

HCPCS2 = Source(
    dataset=datasets.HCPCS2,
    version="2026 October quarterly update",
    page_url="https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update",
    files=("https://www.cms.gov/files/zip/october-2026-alpha-numeric-hcpcs-file.zip",),
)

FPL = Source(
    dataset=datasets.FPL,
    version="2026 HHS Poverty Guidelines",
    page_url=(
        "https://aspe.hhs.gov/sites/default/files/documents/"
        "b1bfa16b20ae9b89d525bc35de7c1643/detailed-guidelines-2026.pdf"
    ),
    files=(),
)

HPT_NYP = Source(
    dataset=datasets.hpt_dataset("nyp"),
    version="NewYork-Presbyterian standardcharges.json v3.0.0 (last_updated_on 2026-03-31)",
    page_url=(
        "https://www.nyp.org/patients-visitors/paying-for-care/"
        "hospital-price-transparency/standard-charges"
    ),
    files=(
        "https://nyp.widen.net/content/hisgjrgpuk/original/"
        "133957095_NewYork-Presbyterian-Hospital_standardcharges.json.zip"
        "?u=n8xzey&download=true",
    ),
)

ALL_SOURCES: tuple[Source, ...] = (
    NCCI_PTP_PRAC,
    NCCI_PTP_OPPS,
    NCCI_MUE_PRAC,
    NCCI_MUE_OPPS,
    NCCI_MUE_DME,
    PFS_RVU,
    HCPCS2,
    FPL,
    HPT_NYP,
)
