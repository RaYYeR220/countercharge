"""Single-table DynamoDB case store (contract C3).

Table ``cc-cases``, partition key ``PK``, sort key ``SK``, with a ``GSI1``
(``GSI1PK``/``GSI1SK``) for status-filtered case listing. Item shapes:

- ``ORG#<org>`` / ``CASE#<case_id>``        -- case summary row
- ``CASE#<id>`` / ``META``                  -- case metadata
- ``CASE#<id>`` / ``DOC#<doc_id>``          -- uploaded/extracted document
- ``CASE#<id>`` / ``FINDING#<finding_id>``  -- signed audit finding
- ``CASE#<id>`` / ``ACTION#<action_id>``    -- drafted/sent action
- ``CASE#<id>`` / ``DECISION#<iso_ts>#<r>`` -- policy/lambda decision log
- ``CASE#<id>`` / ``EVENT#<iso_ts>#<r>``    -- case timeline event
- ``CASE#<id>`` / ``FOLLOWUP#<name>``       -- scheduled follow-up
"""

from decimal import Decimal

from boto3.dynamodb.conditions import Key

from countercharge_engine.models import Finding

from countercharge_core.ids import iso_now, new_action_id, new_case_id, new_doc_id, sort_key

_RESERVED_KEYS = ("PK", "SK", "GSI1PK", "GSI1SK")


def _to_ddb(value):
    """Recursively convert Python values into DynamoDB-safe types (float -> Decimal)."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_ddb(v) for v in value]
    return value


def _from_ddb(value):
    """Recursively convert DynamoDB values back into plain Python (Decimal -> int/float)."""
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if as_int == value else float(value)
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_ddb(v) for v in value]
    return value


def _strip_keys(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in _RESERVED_KEYS}


def _clean(item: dict) -> dict:
    return _from_ddb(_strip_keys(item))


class CaseStore:
    def __init__(self, table, s3=None, bucket: str = ""):
        self._table = table
        self._s3 = s3
        self._bucket = bucket

    # ---- internal query helpers -----------------------------------------

    def _query_case_partition(self, case_id: str) -> list[dict]:
        return self._paginate(KeyConditionExpression=Key("PK").eq(f"CASE#{case_id}"))

    def _query_prefix(self, case_id: str, prefix: str) -> list[dict]:
        return self._paginate(
            KeyConditionExpression=Key("PK").eq(f"CASE#{case_id}") & Key("SK").begins_with(prefix)
        )

    def _paginate(self, **kwargs) -> list[dict]:
        items: list[dict] = []
        while True:
            response = self._table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            kwargs["ExclusiveStartKey"] = last_key

    # ---- case lifecycle ---------------------------------------------------

    def create_case(
        self,
        *,
        org: str,
        patient_name: str,
        patient_email: str,
        patient_state: str,
        reply_to_email: str,
        hospital_id: str,
        household: dict | None = None,
        gfe: dict | None = None,
        case_id: str | None = None,
        status: str = "intake",
    ) -> str:
        case_id = case_id or new_case_id()
        now = iso_now()
        meta_item = {
            "PK": f"CASE#{case_id}",
            "SK": "META",
            "org": org,
            "patient": {"name": patient_name, "email": patient_email, "state": patient_state},
            "reply_to_email": reply_to_email,
            "hospital_id": hospital_id,
            "household": household,
            "gfe": gfe,
            "created_at": now,
        }
        summary_item = {
            "PK": f"ORG#{org}",
            "SK": f"CASE#{case_id}",
            "GSI1PK": f"ORG#{org}#STATUS#{status}",
            "GSI1SK": f"CASE#{case_id}",
            "status": status,
            "patient_display": patient_name,
            "hospital_id": hospital_id,
            "billed_cents": 0,
            "disputable_cents": 0,
            "recovered_cents": 0,
            "updated_at": now,
        }
        self._table.put_item(Item=_to_ddb(meta_item))
        self._table.put_item(Item=_to_ddb(summary_item))
        return case_id

    def get_case(self, case_id: str) -> dict:
        result: dict = {"case_id": case_id, "meta": None, "docs": [], "findings": [], "actions": []}
        for item in self._query_case_partition(case_id):
            sk = item["SK"]
            clean = _clean(item)
            if sk == "META":
                result["meta"] = clean
            elif sk.startswith("DOC#"):
                result["docs"].append(clean)
            elif sk.startswith("FINDING#"):
                result["findings"].append(clean)
            elif sk.startswith("ACTION#"):
                result["actions"].append(clean)
        return result

    # ---- docs ---------------------------------------------------------------

    def put_doc(
        self,
        case_id: str,
        *,
        kind: str,
        payload: dict,
        confidence: float,
        s3_key: str | None = None,
        doc_id: str | None = None,
    ) -> str:
        doc_id = doc_id or new_doc_id()
        item = {
            "PK": f"CASE#{case_id}",
            "SK": f"DOC#{doc_id}",
            "doc_id": doc_id,
            "kind": kind,
            "payload": payload,
            "confidence": confidence,
            "s3_key": s3_key,
        }
        self._table.put_item(Item=_to_ddb(item))
        return doc_id

    def list_docs(self, case_id: str) -> list[dict]:
        return [_clean(item) for item in self._query_prefix(case_id, "DOC#")]

    # ---- findings -------------------------------------------------------------

    def put_findings(self, case_id: str, findings: list[Finding], signatures: list[str]) -> None:
        if len(findings) != len(signatures):
            raise ValueError("findings and signatures must be the same length")
        for finding, signature in zip(findings, signatures):
            item = {
                "PK": f"CASE#{case_id}",
                "SK": f"FINDING#{finding.finding_id}",
                "finding": finding.model_dump(mode="json"),
                "signature": signature,
            }
            self._table.put_item(Item=_to_ddb(item))

    def get_findings(self, case_id: str) -> list[tuple[Finding, str]]:
        results = []
        for item in self._query_prefix(case_id, "FINDING#"):
            clean = _clean(item)
            results.append((Finding(**clean["finding"]), clean["signature"]))
        return results

    # ---- actions --------------------------------------------------------------

    def put_action(
        self,
        case_id: str,
        *,
        type: str,
        status: str,
        tool: str,
        tool_input: dict,
        input_hash: str,
        recipient_email: str,
        finding_ids: list[str],
        disputed_amount_cents: int,
        letter_markdown: str = "",
        action_id: str | None = None,
    ) -> str:
        action_id = action_id or new_action_id()
        item = {
            "PK": f"CASE#{case_id}",
            "SK": f"ACTION#{action_id}",
            "action_id": action_id,
            "type": type,
            "status": status,
            "tool": tool,
            "tool_input": tool_input,
            "input_hash": input_hash,
            "recipient_email": recipient_email,
            "finding_ids": finding_ids,
            "disputed_amount_cents": disputed_amount_cents,
            "letter_markdown": letter_markdown,
            "created_at": iso_now(),
        }
        self._table.put_item(Item=_to_ddb(item))
        return action_id

    def get_action(self, case_id: str, action_id: str) -> dict | None:
        response = self._table.get_item(Key={"PK": f"CASE#{case_id}", "SK": f"ACTION#{action_id}"})
        item = response.get("Item")
        return _clean(item) if item is not None else None

    def update_action_status(self, case_id: str, action_id: str, status: str, **extra) -> None:
        fields = {"status": status, **extra}
        names, values, parts = _update_expression_parts(fields)
        self._table.update_item(
            Key={"PK": f"CASE#{case_id}", "SK": f"ACTION#{action_id}"},
            UpdateExpression="SET " + ", ".join(parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=_to_ddb(values),
        )

    # ---- decisions & events -----------------------------------------------------

    def add_decision(
        self, case_id: str, layer: str, tool: str, outcome: str, reason: str, action_id: str | None = None
    ) -> None:
        item = {
            "PK": f"CASE#{case_id}",
            "SK": sort_key("DECISION"),
            "layer": layer,
            "tool": tool,
            "outcome": outcome,
            "reason": reason,
        }
        if action_id is not None:
            item["action_id"] = action_id
        self._table.put_item(Item=_to_ddb(item))

    def add_event(self, case_id: str, kind: str, text: str, data: dict | None = None) -> None:
        item = {
            "PK": f"CASE#{case_id}",
            "SK": sort_key("EVENT"),
            "kind": kind,
            "text": text,
            "data": data or {},
        }
        self._table.put_item(Item=_to_ddb(item))

    def list_decisions(self, case_id: str) -> list[dict]:
        return [_clean(item) for item in self._query_prefix(case_id, "DECISION#")]

    def list_events(self, case_id: str) -> list[dict]:
        return [_clean(item) for item in self._query_prefix(case_id, "EVENT#")]

    # ---- org case listing & summary --------------------------------------------

    def list_cases(self, org: str, status: str | None = None) -> list[dict]:
        if status is not None:
            response = self._table.query(
                IndexName="GSI1",
                KeyConditionExpression=Key("GSI1PK").eq(f"ORG#{org}#STATUS#{status}"),
            )
        else:
            response = self._table.query(
                KeyConditionExpression=Key("PK").eq(f"ORG#{org}") & Key("SK").begins_with("CASE#")
            )
        return [_clean(item) for item in response.get("Items", [])]

    def update_summary(
        self,
        case_id: str,
        org: str,
        *,
        status: str | None = None,
        patient_display: str | None = None,
        hospital_id: str | None = None,
        billed_cents: int | None = None,
        disputable_cents: int | None = None,
        recovered_cents: int | None = None,
    ) -> None:
        fields = {
            "status": status,
            "patient_display": patient_display,
            "hospital_id": hospital_id,
            "billed_cents": billed_cents,
            "disputable_cents": disputable_cents,
            "recovered_cents": recovered_cents,
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        fields["updated_at"] = iso_now()
        if status is not None:
            fields["GSI1PK"] = f"ORG#{org}#STATUS#{status}"

        names, values, parts = _update_expression_parts(fields)
        self._table.update_item(
            Key={"PK": f"ORG#{org}", "SK": f"CASE#{case_id}"},
            UpdateExpression="SET " + ", ".join(parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=_to_ddb(values),
        )


def _update_expression_parts(fields: dict) -> tuple[dict, dict, list[str]]:
    names: dict = {}
    values: dict = {}
    parts: list[str] = []
    for key, value in fields.items():
        pname, vname = f"#{key}", f":{key}"
        names[pname] = key
        values[vname] = value
        parts.append(f"{pname} = {vname}")
    return names, values, parts
