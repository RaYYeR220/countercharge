from countercharge_engine.fpl import fpl_percent, guideline_cents
from countercharge_engine.models import Household
from countercharge_engine.refdata.memory import MemoryRefData

_FPL_2026 = {(2026, "48"): (1_596_000, 568_000)}


def _refdata():
    return MemoryRefData(fpl=_FPL_2026)


def test_guideline_cents_household_of_four():
    assert guideline_cents(4, "NY", _refdata(), 2026) == 3_300_000


def test_guideline_cents_household_of_one():
    assert guideline_cents(1, "TX", _refdata(), 2026) == 1_596_000


def test_fpl_percent_household_of_four_at_50k_income():
    household = Household(size=4, annual_income_cents=5_000_000, state="NY")
    assert fpl_percent(household, _refdata(), 2026) == 151


def test_fpl_percent_household_of_one_at_60k_income():
    household = Household(size=1, annual_income_cents=6_000_000, state="TX")
    assert fpl_percent(household, _refdata(), 2026) == 375
