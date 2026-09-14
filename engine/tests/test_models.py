import json
from datetime import date

import pytest
from pydantic import ValidationError

from countercharge_engine.canonical import canonical_json, finding_id, with_id
from countercharge_engine.models import Bill, Citation, CodeType, Finding, LineItem


def _make_finding(**overrides):
    citation = Citation(
        dataset="BILL", version="n/a", url="https://example.org", record={"a": 1, "b": "x"}
    )
    defaults = dict(
        rule_id="R1",
        disputable=True,
        line_ids=["l1", "l2"],
        amount_cents=1000,
        title="Duplicate charge",
        detail="lines are identical",
        citation=citation,
        evidence={},
    )
    defaults.update(overrides)
    return Finding(**defaults)


def test_finding_id_stable_across_dict_key_order_and_reinstantiation():
    f1 = _make_finding()
    f2 = _make_finding()
    assert finding_id(f1) == finding_id(f2)

    # Re-instantiating from a serialized/deserialized copy gives the same id.
    data = f1.model_dump(mode="json")
    f3 = Finding(**data)
    assert finding_id(f1) == finding_id(f3)

    # Key order inside an arbitrary citation record dict must not matter.
    citation_a = Citation(
        dataset="BILL", version="n/a", url="https://example.org", record={"a": 1, "b": "x"}
    )
    citation_b = Citation(
        dataset="BILL", version="n/a", url="https://example.org", record={"b": "x", "a": 1}
    )
    fa = _make_finding(citation=citation_a)
    fb = _make_finding(citation=citation_b)
    assert finding_id(fa) == finding_id(fb)


def test_finding_id_changes_with_amount_cents():
    f1 = _make_finding(amount_cents=1000)
    f2 = _make_finding(amount_cents=2000)
    assert finding_id(f1) != finding_id(f2)


def test_canonical_json_excludes_finding_id():
    f_no_id = _make_finding()
    f_with_id = _make_finding(finding_id="f_deadbeefdeadbeef")
    assert canonical_json(f_no_id) == canonical_json(f_with_id)
    assert b"finding_id" not in canonical_json(f_with_id)


def test_with_id_sets_stable_id():
    f = _make_finding()
    tagged = with_id(f)
    assert tagged.finding_id == finding_id(f)
    assert tagged.finding_id.startswith("f_")
    assert len(tagged.finding_id) == len("f_") + 16


def test_money_fields_reject_floats():
    with pytest.raises(ValidationError):
        LineItem(
            line_id="l1",
            dos=date(2026, 1, 1),
            code="99213",
            code_type=CodeType.CPT,
            charge_cents=1.5,
        )


def test_bill_json_schema_round_trips():
    schema = Bill.model_json_schema()
    round_tripped = json.loads(json.dumps(schema))
    assert round_tripped == schema
