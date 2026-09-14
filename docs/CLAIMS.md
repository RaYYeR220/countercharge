# Claims ledger

Every claim this project makes, tagged by how you can check it:

- **REPRODUCIBLE** — run a command from this repo, no AWS account needed, and you get
  the same result.
- **VERIFIED-LIVE** — observed running against real AWS services in our account.
  Reproducing it yourself needs equivalent AWS access (an AgentCore Gateway, a Cedar
  Policy engine attached to it, Cognito, KMS); this repo doesn't currently ship a
  one-command live deploy.
- **MODELED** — simulated or synthetic for the demo: not a claim about production
  behavior.
- **NOT-CLAIMED** — explicitly out of scope, not implemented, or unknown. Listed so it's
  clear we know the gap exists.

## Engine

| Claim | Tag | How to verify |
|---|---|---|
| The audit engine is pure Python: no network call, no language model call, same input → same output | REPRODUCIBLE | Read `engine/src/countercharge_engine/audit.py` and `rules/*.py` — no `requests`/`boto3`/model imports. Run `cd engine && uv run python -m countercharge_engine audit --bill <file> --refdata data/refdata.sqlite` offline. |
| 45/45 synthetic corpus cases match a pre-registered rule set exactly; 100% exact disputable-cents match; 100% FAP tier accuracy; 0 false positives on 8 clean controls; 100%/100% precision/recall on all 9 rules | REPRODUCIBLE | `cd evals && uv run python -m countercharge_evals.run_engine`, compare to `evals/results/engine-scorecard.md`. The key (`evals/key/answers.json`) is hashed in `evals/key/key.sha256`, committed before the scorecard was generated, and built independently of `audit()` — see `evals/tests/test_seeds_independence.py`. |
| Reference data (NCCI PTP/MUE, PFS RVU, HCPCS Level II, HHS poverty guidelines, hospital price-transparency and charity-care files) is CMS/HHS/hospital source data, not invented, with exact version, URL, row count, and retrieval date per dataset | REPRODUCIBLE | `engine/data/REFDATA.md`; rebuild with `countercharge_engine.refdata.build` against the same source URLs. |
| NCCI PTP history older than 2024-01-01 (deleted before the cutoff) is dropped from `refdata.sqlite` for size, which can only cause a missed finding on an old claim, never a false one | REPRODUCIBLE | `engine/data/REFDATA.md` ("NCCI PTP deletion cutoff"); `engine/tests/test_build_parsers.py::test_keep_ptp_edit_*`. |

## Tests

| Claim | Tag | How to verify |
|---|---|---|
| 427 tests pass across the 6 Python packages (121 engine incl. 1 skipped, 82 core, 71 tools, 90 policies, 61 agent, 2 evals incl. 2 skipped) | REPRODUCIBLE | `uv run pytest -q` from inside each of `engine/`, `core/`, `tools/`, `policies/`, `agent/`, `evals/`. Measured 2026-09-15. |
| Every Lambda-layer deny path is covered by a test with a distinct reason (`bad_mac`, `expired`, `tool_mismatch`, `action_mismatch`, `case_mismatch`, `input_mismatch`, `empty_finding_ids`, `foreign_finding`, `bad_finding_signature`, `not_disputable`, `amount_exceeds_findings`, `recipient_not_allowed`) | REPRODUCIBLE | `tools/tests/test_action_tools.py`. |
| Cedar policy logic — default-deny, `forbid` beats `permit`, per-tool role/input conditions — behaves as specified, evaluated by the same Rust-backed `cedar-policy` engine AWS's Policy service is built on | REPRODUCIBLE | `cd policies && uv run pytest -q` (90 tests, using `cedarpy` — real `getTag`/`hasTag`/entity-tag evaluation, not an attribute-mapping approximation). |

## Gates and enforcement

| Claim | Tag | How to verify |
|---|---|---|
| An action tool cannot be called by the agent without a human approval token — there is no code path around the interrupt | REPRODUCIBLE | `agent/src/countercharge_agent/hooks.py` (`ApprovalHook` on `BeforeToolCallEvent`); `agent/tests/`. |
| An approval token is bound to one exact tool call: changing the recipient, amount, or finding list after approval invalidates it | REPRODUCIBLE | `core/src/countercharge_core/approval.py`; `tools/tests/test_action_tools.py` (`input_mismatch` cases). |
| AgentCore Gateway + a Cedar-based AgentCore Policy engine (ENFORCE mode) sit in front of the Lambda tool targets, deny by default, and return `{"error":{"code":-32002,...}}` for a denied call; `tools/list` is filtered by the same policy | VERIFIED-LIVE | Confirmed against a live AgentCore Gateway + Policy engine in our AWS account using Cedar policies equivalent to `policies/`'s (tag-based principal, `AgentCore::Action`/`AgentCore::Gateway` entities, the exact deny response shape above). This repo's own `agentcore/` CDK stack is scaffolded but not deployed as of this writing, so the *specific* 7 policies in `policies/` haven't been exercised against a live Gateway yet — deploying `agentcore/` attaches `countercharge_policies.render()`'s output unchanged. |
| Findings and approval tokens are signed with KMS `GenerateMac`/verified with `VerifyMac` (`HMAC_SHA_256`) in production, falling back to a local HMAC secret for dev/test | REPRODUCIBLE (signing logic) / NOT-CLAIMED (exercised against a real KMS key) | `core/src/countercharge_core/signing.py` (`Signer` protocol, `LocalHmacSigner`/`KmsHmacSigner` share one interface). All 427 measured tests run against `LocalHmacSigner`; `KmsHmacSigner` is the same boto3 `generate_mac`/`verify_mac` calls but hasn't been run against a live KMS key in this repo's test suite. |
| AgentCore Identity (Cognito-issued JWTs carrying `role`/`org` tags, validated against the pool's JWKS) authenticates every caller of the Gateway and the agent | REPRODUCIBLE (validation logic) / VERIFIED-LIVE (against a real Cognito pool) | `agent/src/countercharge_agent/auth.py`; unit-tested against a fixed local JWKS, exercised against a real Cognito user pool in our account during the AgentCore spike. |

## Hosting

| Claim | Tag | How to verify |
|---|---|---|
| The agent container implements the AgentCore Runtime contract (`BedrockAgentCoreApp`, `POST /invocations`, SSE `data:` lines, session header) | REPRODUCIBLE | `agent/src/countercharge_agent/app.py`; run it locally per the README quick start and POST to `/invocations`. |
| The agent currently runs on plain container infrastructure rather than inside the managed AgentCore Runtime service | NOT-CLAIMED (Runtime hosting itself) | Our AWS account's AgentCore Runtime/Memory/Browser/Code-Interpreter quotas are 0 (support-only increase, not yet granted) and Bedrock invoke is blocked account-wide — see `agent/Dockerfile`'s ECS-oriented comments. Gateway, Policy, and Identity have no such restriction and are used as designed (see above). |
| Model provider is pluggable at runtime via `MODEL_PROVIDER` (`venice`\|`openrouter`\|`bedrock`) through one Strands `Model` interface, no branching in application logic | REPRODUCIBLE | `agent/src/countercharge_agent/models.py::build_model`. Set the env var and an API key per README's env table. |

## Demo scope

| Claim | Tag | How to verify |
|---|---|---|
| Dispute letters, FAP applications, itemized-bill requests, and escalations are all sent to a demo inbox we control, never a real hospital or regulator | MODELED | `engine/data/hospitals.json`'s `demo_billing_email`; every `actions` Lambda tool sends there regardless of the caller-supplied recipient's domain checks passing. |
| Patients and bills in the eval corpus are synthetic | MODELED | `evals/corpus/` generator (`countercharge_evals.generate`); no real patient data anywhere in this repo. |
| CPT procedure-code descriptor text (AMA-licensed) is never displayed — only the code, the public HCPCS Level II description where one exists, and our own plain-language category label | NOT-CLAIMED (showing AMA descriptor text) — by design | `engine/data/REFDATA.md`'s `hcpcs2` dataset (public HCPCS II descriptions only); grep the repo for CPT descriptor strings and find none. |
| Every finding cites a specific CMS/HHS/hospital source record and rule id — never a bare LLM assertion | REPRODUCIBLE | `Finding.citation` in `engine/src/countercharge_engine/models.py`; any `AuditReport` produced by the CLI or the corpus runner includes it. |
| This is not legal or medical advice | NOT-CLAIMED | Stated once, here and in the README; findings are citations and dollar amounts, not legal conclusions. |
| NewYork-Presbyterian's charity-care income thresholds (`free_max_fpl`/`discount_max_fpl`) | NOT-CLAIMED — UNKNOWN | `engine/data/hospitals.json`'s `nyp` entry: both fields are `null` with a note explaining the source PDF isn't resolvable without client-side JS. The engine reports FAP eligibility as unknown for NYP rather than guessing a threshold. |
| Inpatient (DRG) billing is audited | NOT-CLAIMED | The engine's `Bill.setting` covers `ER`\|`OUTPATIENT`\|`PROFESSIONAL`; no inpatient rule exists. |
| Gmail read-only intake (pulling a forwarded EOB from the patient's own inbox via AgentCore Identity 3LO) | NOT-CLAIMED | `agent/src/countercharge_agent/identity.py`'s `NotConfiguredGmailIdentityProvider` raises `NotImplementedError` by design; all demo mail goes through SES. |
| US-only scope | NOT-CLAIMED (non-US billing) | All reference data is CMS/HHS; no other country's billing rules are modeled. |
