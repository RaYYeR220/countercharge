import re

from countercharge_core.ids import new_action_id, new_case_id, new_doc_id, rand4, sort_key


def test_new_case_id_prefixed_and_unique():
    a, b = new_case_id(), new_case_id()
    assert a.startswith("case_")
    assert a != b


def test_new_doc_id_prefixed():
    assert new_doc_id().startswith("doc_")


def test_new_action_id_prefixed():
    assert new_action_id().startswith("action_")


def test_rand4_is_four_hex_chars():
    assert re.fullmatch(r"[0-9a-f]{4}", rand4())


def test_sort_key_shape():
    sk = sort_key("DECISION")
    assert re.fullmatch(r"DECISION#\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z#[0-9a-f]{4}", sk)


def test_sort_key_monotonic_and_unique():
    a = sort_key("EVENT")
    b = sort_key("EVENT")
    assert a != b
    assert a <= b
