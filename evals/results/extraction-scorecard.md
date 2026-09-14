# Countercharge extraction scorecard

- Model provider: `venice`
- Documents evaluated: 55 (45 bill, 10 eob)
- Documents with an exact field match: 49/55
- Bill line exact-match rate: 100.0%
- Bill totals exact-match rate: 86.7%
- Provider name fuzzy-match rate: 100.0%
- EOB line exact-match rate: 100.0%
- EOB scalar field exact-match rate: 100.0%

## End-to-end: photo -> findings

- Cases with an exact rule-set match (engine run on the EXTRACTED bill): 35/45
- Exact disputable-cents match rate: 86.7%
- FAP tier accuracy: 0.0%
- Negative-control false positives: 0

## Cost

- Token usage across 55 extraction calls: 138168 in / 23295 out / 161463 total

## Per-category field accuracy

| Category | Documents | Exact match | Rate |
|---|---|---|---|
| ADVERSARIAL | 6 | 6 | 100.0% |
| ARITHMETIC | 3 | 2 | 66.7% |
| CASH_PRICE | 3 | 3 | 100.0% |
| CLEAN | 10 | 9 | 90.0% |
| DUPLICATE | 3 | 3 | 100.0% |
| EOB_BALANCE_BILLING | 6 | 3 | 50.0% |
| FAP_501R | 3 | 3 | 100.0% |
| MUE | 3 | 3 | 100.0% |
| MULTI | 6 | 5 | 83.3% |
| NCCI_PTP | 3 | 3 | 100.0% |
| NSA_EMERGENCY | 6 | 6 | 100.0% |
| NSA_GFE | 3 | 3 | 100.0% |

## Field-level mismatches

- **arithmetic_balance_01** (bill, ARITHMETIC)
  - totals mismatch: expected {'charges_cents': 126000, 'adjustments_cents': 20000, 'payments_cents': 50000, 'patient_balance_cents': 74000}, got {'charges_cents': 126000, 'adjustments_cents': -20000, 'payments_cents': -50000, 'patient_balance_cents': 74000}
- **clean_eob_balance_equal** (bill, CLEAN)
  - totals mismatch: expected {'charges_cents': 123000, 'adjustments_cents': 70000, 'payments_cents': 33000, 'patient_balance_cents': 20000}, got {'charges_cents': 123000, 'adjustments_cents': -70000, 'payments_cents': -33000, 'patient_balance_cents': 20000}
- **eob_bb_01** (bill, EOB_BALANCE_BILLING)
  - totals mismatch: expected {'charges_cents': 194000, 'adjustments_cents': 100000, 'payments_cents': 60000, 'patient_balance_cents': 34000}, got {'charges_cents': 194000, 'adjustments_cents': -100000, 'payments_cents': -60000, 'patient_balance_cents': 34000}
- **eob_bb_02** (bill, EOB_BALANCE_BILLING)
  - totals mismatch: expected {'charges_cents': 123000, 'adjustments_cents': 70000, 'payments_cents': 33000, 'patient_balance_cents': 20000}, got {'charges_cents': 123000, 'adjustments_cents': -70000, 'payments_cents': -33000, 'patient_balance_cents': 20000}
- **eob_bb_03** (bill, EOB_BALANCE_BILLING)
  - totals mismatch: expected {'charges_cents': 107000, 'adjustments_cents': 60000, 'payments_cents': 27000, 'patient_balance_cents': 20000}, got {'charges_cents': 107000, 'adjustments_cents': -60000, 'payments_cents': -27000, 'patient_balance_cents': 20000}
- **multi_02_dedupe_max** (bill, MULTI)
  - totals mismatch: expected {'charges_cents': 318400, 'adjustments_cents': 100000, 'payments_cents': 150000, 'patient_balance_cents': 68400}, got {'charges_cents': 318400, 'adjustments_cents': -100000, 'payments_cents': -150000, 'patient_balance_cents': 68400}

## End-to-end mismatches (extracted bill vs pre-registered key)

- **arithmetic_balance_01** (ARITHMETIC)
  - expected rules missing: ['ARITHMETIC']
  - disputable_cents expected 18000, got 0
- **cash_price_01** (CASH_PRICE)
  - expected rules missing: ['CASH_PRICE']
  - disputable_cents expected 32900, got 0
- **cash_price_02** (CASH_PRICE)
  - expected rules missing: ['CASH_PRICE']
  - disputable_cents expected 8100, got 0
- **cash_price_03** (CASH_PRICE)
  - expected rules missing: ['CASH_PRICE']
  - disputable_cents expected 31100, got 0
- **clean_fap_none** (CLEAN)
  - fap tier expected NONE, got None
- **fap_discount_01** (FAP_501R)
  - expected rules missing: ['FAP_501R']
  - fap tier expected DISCOUNT, got None
- **fap_discount_02** (FAP_501R)
  - expected rules missing: ['FAP_501R']
  - fap tier expected DISCOUNT, got None
- **fap_free_01** (FAP_501R)
  - expected rules missing: ['FAP_501R']
  - fap tier expected FREE, got None
- **multi_01_selfpay_nyp** (MULTI)
  - expected rules missing: ['CASH_PRICE']
  - disputable_cents expected 159200, got 126300
- **multi_03_fap_plus_disputes** (MULTI)
  - expected rules missing: ['FAP_501R']
  - fap tier expected FREE, got None
- **nsa_gfe_03** (NSA_GFE)
  - expected rules missing: ['NSA_GFE']
  - disputable_cents expected 500000, got 0

