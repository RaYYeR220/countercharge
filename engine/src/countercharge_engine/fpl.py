"""Federal Poverty Level guideline helpers.

Guidelines are looked up from ``RefData`` (never hardcoded here) so the
offline refdata builder is the single source of truth for the dollar
amounts, keeping the engine itself free of embedded CMS/HHS figures.
"""

from countercharge_engine.models import Household
from countercharge_engine.refdata.base import RefData


def guideline_cents(size: int, state: str, refdata: RefData, year: int) -> int:
    """Annual FPL guideline in cents for a household of ``size`` in ``state``."""
    first_cents, per_additional_cents = refdata.fpl_base(year, state)
    return first_cents + per_additional_cents * (size - 1)


def fpl_percent(household: Household, refdata: RefData, year: int) -> int:
    """Household income as a floored percentage of its FPL guideline."""
    guideline = guideline_cents(household.size, household.state, refdata, year)
    return (household.annual_income_cents * 100) // guideline
