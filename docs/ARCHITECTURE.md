# Architecture

This is the deeper reference behind the diagram in [`README.md`](../README.md). It
covers one case end to end, the exact approval-token format, the Cedar policy list,
DynamoDB's key design, and the SSE event protocol between the agent and its caller.

## Data flow for one case

1. **Intake.** A bill (and optionally an EOB, household income, and a good-faith
   estimate) reaches the case agent. `case___save_extraction` (a Gateway tool, Lambda
   target `case`) validates the payload against the engine's pydantic models and writes
   it as a `DOC#<doc_id>` item.
2. **Audit.** The agent calls `engine___audit_case(case_id)` (Lambda target `engine`).
   The Lambda loads the case's docs from DynamoDB, loads `refdata.sqlite` (from
   `REFDATA_PATH` or downloaded once per warm container from `REFDATA_S3_URI`), and runs
   `countercharge_engine.audit.audit(bill, eob, household, gfe, refdata)` — pure Python,
   no network, no model call. Every `Finding` it returns is hashed to a `finding_id`
   (sha256 of its canonical JSON) and signed with KMS `GenerateMac` (or an HMAC secret
   locally); both the finding and its signature are written as
   `FINDING#<finding_id>` items, and the case's `ORG#<org>`/`CASE#<id>` summary row is
   updated with `disputable_cents`.
3. **Explain.** A sub-agent turns findings into plain-language text (English/Spanish),
   citing the same `finding_id`s the engine produced — it never invents a finding.
4. **Draft.** `case___draft_action` builds a letter/application draft referencing
   specific `finding_id`s and a `disputed_amount_cents`. A grounding check
   (`countercharge_core.grounding`) rejects the draft if any dollar amount or code in the
   letter text doesn't trace back to a cited finding or the bill itself. An accepted
   draft is written as an `ACTION#<action_id>` item with `status=pending_approval`.
5. **Approve.** The `ApprovalHook` fires an `event.interrupt("approval_required", ...)`
   on the `BeforeToolCallEvent` for any action tool. The turn ends with an `interrupt`
   event carrying the exact `tool` name and `tool_input` the human is being asked to
   approve. A human approver issues an approval token (below) for that exact input.
6. **Gate 2: Policy.** The agent resumes and calls the qualified action tool
   (`actions___send_dispute_letter`, etc.) through the AgentCore Gateway, carrying the
   caller's own Cognito JWT. AgentCore Policy evaluates the matching Cedar policy
   (role, action, resource, `context.input.*`) before the call ever reaches Lambda.
   No matching policy is a deny by default.
7. **Gate 3: Lambda.** The `actions` Lambda re-verifies the approval token (KMS
   `VerifyMac`, expiry, `tool`/`action_id`/`case_id`/`input_hash` all matching this exact
   call), re-loads every cited finding and checks its signature and `disputable` flag,
   and checks the recipient against the hospital's allowed billing domain. Any failure
   raises `ToolDenied("DENIED:lambda:<reason>")`, which the router never catches, and a
   `DECISION#<ts>` item is written either way (`layer="lambda"`, `outcome`, `reason`).
8. **Act.** On success: SES sends the letter, the action's `status` becomes `sent`, an
   `EVENT#<ts>` item is appended to the case timeline, and (for `schedule_followup`) an
   EventBridge Scheduler one-time schedule is created targeting the `followup` Lambda.
9. **Follow up.** When the schedule fires, `followup.handler` reads an M2M Cognito
   client credential from Secrets Manager, exchanges it for a token, and POSTs a
   `mode=followup` invocation back to the running agent — the same Runtime contract as
   any other caller, just with a system principal instead of a human's JWT.

## Approval token format

Issued by `countercharge_core.approval.issue_token`, verified by `verify_token` — both
used identically by the agent (issuing, on the human's behalf) and by the Lambda action
tools (verifying, before acting):

```
<base64url(payload_json)>.<base64url(hmac_or_kms_mac(payload_json))>
```

`payload_json` is the canonical JSON encoding of:

```json
{
  "v": 1,
  "action_id": "<the action being approved>",
  "case_id": "<the case it belongs to>",
  "tool": "actions___send_dispute_letter",
  "input_hash": "sha256(canonical_json(tool_input minus approval_token))",
  "approver_sub": "<Cognito sub of the human who approved>",
  "exp": 1234567890
}
```

Verification fails closed on any mismatch, with a distinct reason per case:
`malformed`, `bad_mac`, `expired`, `tool_mismatch`, `action_mismatch`, `case_mismatch`,
`input_mismatch`. Because `input_hash` covers every field of `tool_input` except the
token itself, changing the recipient, the disputed amount, or the finding list after
approval — even by one cent — invalidates the token; the agent (or an attacker with the
token but not a live signer) cannot reuse it against different inputs.

## Cedar policies

Rendered by `countercharge_policies.render.render(gateway_arn, hospitals)` from
templates in `policies/src/countercharge_policies/templates/`, one `.cedar` file per
policy:

| Policy | Effect | Scope |
|---|---|---|
| `read_engine` | permit | All six `engine___*` tools, any role with a `role` tag |
| `case_tools` | permit | `case___get_case`/`save_extraction`/`draft_action`, non-integrator roles |
| `send_dispute_letter` | permit | Requires non-empty `approval_token` and `finding_ids`, recipient on the hospital's allowlist, and `disputed_amount_cents <= 1,000,000` unless role is `advocate` |
| `fap_and_itemized` | permit | `submit_fap_application` + `request_itemized_bill`, sharing one rule (token + recipient) |
| `file_escalation` | permit | `advocate`/`system` roles only, non-empty token and `finding_ids` |
| `schedule_followup` | permit | `patient`/`advocate`/`system` roles, `1 <= days <= 90` |
| `forbid_integrator_writes` | forbid | Every case/action tool, role `integrator` — makes the public MCP surface read-only regardless of what any permit above would otherwise allow |

Example — `send_dispute_letter.cedar.tmpl`:

```cedar
permit(
  principal,
  action == AgentCore::Action::"actions___send_dispute_letter",
  resource == AgentCore::Gateway::"${gateway_arn}"
)
when {
  principal.hasTag("role") &&
  ["patient", "advocate", "system"].contains(principal.getTag("role")) &&
  context.input.approval_token != "" &&
  !context.input.finding_ids.isEmpty() &&
  ${recipient_allowed} &&
  (
    context.input.disputed_amount_cents <= 1000000 ||
    principal.getTag("role") == "advocate"
  )
};
```

`${recipient_allowed}` is generated per hospital from `engine/data/hospitals.json`'s
`billing_email_domain`, so onboarding a new hospital regenerates the policy rather than
editing Cedar by hand. There is no policy that matches an `integrator` role against a
`permit` for any case/action tool, and `forbid_integrator_writes` removes any doubt —
Cedar's semantics mean the tightest applicable rule wins, and a `forbid` always beats a
`permit`.

## DynamoDB key design

Single table (default name `cc-cases`), partition key `PK`, sort key `SK`, one GSI
(`GSI1PK`/`GSI1SK`) for status-filtered case listing:

| PK | SK | Item |
|---|---|---|
| `ORG#<org>` | `CASE#<case_id>` | Case summary (status, patient display name, `billed_cents`, `disputable_cents`, `recovered_cents`); `GSI1PK=ORG#<org>#STATUS#<status>` |
| `CASE#<case_id>` | `META` | Case metadata: patient, hospital, household, GFE |
| `CASE#<case_id>` | `DOC#<doc_id>` | An uploaded/extracted document (bill, EOB, ...) |
| `CASE#<case_id>` | `FINDING#<finding_id>` | A signed audit finding |
| `CASE#<case_id>` | `ACTION#<action_id>` | A drafted or sent action |
| `CASE#<case_id>` | `DECISION#<iso_ts>#<rand>` | A policy/Lambda decision log entry |
| `CASE#<case_id>` | `EVENT#<iso_ts>#<rand>` | A case timeline event |
| `CASE#<case_id>` | `FOLLOWUP#<name>` | A scheduled follow-up |

Everything about one case lives under one `CASE#<id>` partition (a single `Query` reads
the whole case), while `ORG#<org>` rows give a cheap, separately-scaled listing of an
org's caseload without scanning every case's full item set. `GSI1` exists purely to
answer "which of this org's cases are in status X" without a table scan.

## SSE event protocol

The agent's `@app.entrypoint` streams one JSON object per line, `data: <json>\n\n`
(`countercharge_core.events.to_sse`), each tagged by `type`:

| `type` | Carries | When |
|---|---|---|
| `text` | `delta` | Streamed model text |
| `tool_start` | `tool`, `tool_use_id`, `input` | A tool call begins |
| `tool_end` | `tool`, `tool_use_id`, `status` (`success`\|`error`\|`denied`), `output` | A tool call finishes |
| `policy_denied` | `tool`, `layer` (`hook`\|`policy`\|`lambda`), `reason` | Any of the three gates denies a call |
| `finding` | `finding` | A new audit finding is available |
| `interrupt` | `interrupt_id`, `name` (`approval_required`), `action` (`action_id`, `tool`, `tool_input`, `preview_markdown`) | The agent is paused waiting for human approval |
| `case_state` | `case` | A full case snapshot, for clients that want to resync |
| `done` | `stop_reason` | The turn is over |
| `error` | `message` | Something failed outside the tool-denial paths above |

A caller resumes an interrupted turn by sending
`{"interruptResponse": {"interruptId": ..., "response": {"approval_token": "..."}}}`
back into the same session; the agent injects the token into the paused tool call and
continues from gate 2.
