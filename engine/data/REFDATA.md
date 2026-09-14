# Reference data

Built by `countercharge_engine.refdata.build`. Each row below is one dataset baked into `refdata.sqlite`, with its exact CMS/HHS release version, source URL, row count and retrieval date.

| dataset | version | url | rows | retrieved |
|---|---|---|---|---|
| NCCI-PTP-PRAC | 2026Q4 v323r0 | https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits | 1762040 | 2026-09-14 |
| NCCI-PTP-OPPS | 2026Q4 v323r0 | https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits | 1429512 | 2026-09-14 |
| NCCI-MUE-PRAC | 2026Q4 eff. 2026-10-01 | https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits-mues | 15212 | 2026-09-14 |
| NCCI-MUE-OPPS | 2026Q4 eff. 2026-10-01 | https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits-mues | 15162 | 2026-09-14 |
| NCCI-MUE-DME | 2026Q4 eff. 2026-10-01 | https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits-mues | 3109 | 2026-09-14 |
| PFS-RVU | RVU26B eff. 2026-05-01 | https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files/rvu26b | 15784 | 2026-09-14 |
| HCPCS2 | 2026 October quarterly update | https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update | 7448 | 2026-09-14 |
| FPL | 2026 HHS Poverty Guidelines | https://aspe.hhs.gov/sites/default/files/documents/b1bfa16b20ae9b89d525bc35de7c1643/detailed-guidelines-2026.pdf | 3 | 2026-09-14 |
| HPT-nyp | NewYork-Presbyterian standardcharges.json v3.0.0 (last_updated_on 2026-03-31) | https://www.nyp.org/patients-visitors/paying-for-care/hospital-price-transparency/standard-charges | 58 | 2026-09-14 |
| FAP-nyp | policy retrieved 2026-09-14 | https://www.nyp.org/billing/charity-care | 1 | 2026-09-14 |
| FAP-ccf | policy retrieved 2026-09-14 | https://my.clevelandclinic.org/-/scassets/files/org/patients-visitors/billing/financial-assistance/7-financial-assistance-program-policy.pdf | 1 | 2026-09-14 |

## Notes

### NCCI PTP deletion cutoff (2024-01-01)

The raw NCCI PTP practitioner and hospital/OPPS releases each carry well
over a decade of edit history, and the large majority of that history is
edits deleted long ago that no current or plausible-future date of service
could ever fall under. Keeping every historical row would multiply
`refdata.sqlite`'s size several times over for rows that can never match a
real audit. The builder (`refdata/build/__main__.py`, `_PTP_DELETION_CUTOFF`,
enforced via `refdata/build/ncci.py`'s `keep_ptp_edit`) drops a PTP edit row
only if it was deleted *before* 2024-01-01; a still-active edit
(`deleted is None`) is always kept, and an edit deleted on or after the
cutoff is kept in full (its `effective`/`deleted` window is preserved
as-is, not truncated).

**Correctness implication:** `ptp_edit()` looks up the edit active on a
bill's exact date of service. If a bill's date of service falls inside an
edit window that was superseded (deleted) before 2024-01-01, that row isn't
in the database and the lookup silently returns `None` -- a missed true
positive, never a false one. This is a size/coverage trade-off for dates of
service more than roughly two years old as of any given build; it never
causes an overclaim, only a conservative gap for older claims. See
`tests/test_build_parsers.py`'s `test_keep_ptp_edit_*` cases for the exact
boundary behavior (deleted 2023-12-31 dropped, deleted 2024-01-01 kept).
