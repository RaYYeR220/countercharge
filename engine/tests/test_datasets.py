"""The dataset keys rules cite must be exactly the keys the refdata builder
writes into the sqlite ``datasets`` table. ``datasets.py`` is the single
source of truth for those keys; this test pins the builder's ``sources.py``
to it so the two can never drift apart again the way R3's MUE citation did.
"""

from countercharge_engine import datasets
from countercharge_engine.refdata.build import sources


def test_ptp_sources_use_canonical_dataset_keys():
    assert sources.NCCI_PTP_PRAC.dataset == datasets.NCCI_PTP_PRAC
    assert sources.NCCI_PTP_OPPS.dataset == datasets.NCCI_PTP_OPPS


def test_mue_sources_use_canonical_dataset_keys():
    assert sources.NCCI_MUE_PRAC.dataset == datasets.NCCI_MUE_PRAC
    assert sources.NCCI_MUE_OPPS.dataset == datasets.NCCI_MUE_OPPS
    assert sources.NCCI_MUE_DME.dataset == datasets.NCCI_MUE_DME


def test_other_sources_use_canonical_dataset_keys():
    assert sources.PFS_RVU.dataset == datasets.PFS_RVU
    assert sources.HCPCS2.dataset == datasets.HCPCS2
    assert sources.FPL.dataset == datasets.FPL


def test_hpt_nyp_source_uses_hpt_dataset_helper():
    assert sources.HPT_NYP.dataset == datasets.hpt_dataset("nyp")


def test_ptp_dataset_lookup_table_covers_both_tables():
    assert datasets.PTP_DATASET == {
        "prac": "NCCI-PTP-PRAC",
        "opps": "NCCI-PTP-OPPS",
    }


def test_mue_dataset_lookup_table_covers_all_three_tables():
    assert datasets.MUE_DATASET == {
        "prac": "NCCI-MUE-PRAC",
        "opps": "NCCI-MUE-OPPS",
        "dme": "NCCI-MUE-DME",
    }
