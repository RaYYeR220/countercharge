"""Tests for the extraction-eval runner (Task B) -- no network. Model calls are
always faked or served from a pre-populated cache; ``extract_document`` is
monkeypatched wherever a real call would otherwise happen."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from countercharge_engine.audit import audit

from countercharge_evals import run_extraction as re

from conftest import make_bill as make_bill_


# -- secrets -------------------------------------------------------------------


def test_find_secrets_path_walks_upward(tmp_path):
    root = tmp_path / "agents-for-humans"
    (root / "internal").mkdir(parents=True)
    (root / "internal" / "secrets.env").write_text("VENICE_API_KEY=abc\n", encoding="utf-8")
    start = root / "countercharge" / "worktrees" / "evals-extraction"
    start.mkdir(parents=True)

    found = re.find_secrets_path(start)
    assert found == root / "internal" / "secrets.env"


def test_find_secrets_path_returns_none_when_missing(tmp_path):
    assert re.find_secrets_path(tmp_path / "nowhere") is None


def test_load_secrets_sets_env_without_overriding(tmp_path, monkeypatch):
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.setenv("ALREADY_SET", "keep-me")
    path = tmp_path / "secrets.env"
    path.write_text("VENICE_API_KEY=real-key\nALREADY_SET=overwritten\nEMPTY_VAR=\n# comment\n\n", encoding="utf-8")

    re.load_secrets(path)

    assert __import__("os").environ["VENICE_API_KEY"] == "real-key"
    assert __import__("os").environ["ALREADY_SET"] == "keep-me"
    assert "EMPTY_VAR" not in __import__("os").environ


def test_load_secrets_noop_when_path_missing(tmp_path):
    re.load_secrets(tmp_path / "does-not-exist.env")  # must not raise


# -- stratified sampling ---------------------------------------------------------


def _fake_cases(categories: dict[str, int]) -> list[dict]:
    cases = []
    for cat, n in categories.items():
        for i in range(n):
            cases.append({"case_id": f"{cat.lower()}_{i}", "category": cat})
    return cases


def test_stratified_case_ids_is_deterministic():
    cases = _fake_cases({"A": 5, "B": 5, "C": 5})
    first = re.stratified_case_ids(cases, 6, seed=2026)
    second = re.stratified_case_ids(cases, 6, seed=2026)
    assert first == second


def test_stratified_case_ids_spans_categories():
    cases = _fake_cases({"A": 5, "B": 5, "C": 5})
    picked = re.stratified_case_ids(cases, 6, seed=2026)
    assert len(picked) == 6
    picked_categories = {p.split("_")[0] for p in picked}
    assert picked_categories == {"a", "b", "c"}


def test_stratified_case_ids_caps_at_available_cases():
    cases = _fake_cases({"A": 2})
    picked = re.stratified_case_ids(cases, 10, seed=1)
    assert len(picked) == 2


# -- cache ------------------------------------------------------------------------


def test_cache_round_trip(tmp_path):
    record = {"case_id": "c1", "kind": "bill", "payload": {"x": 1}, "usage": {}, "stop_reason": "end_turn", "attempts": 1, "error": None}
    re.save_cached_record(tmp_path, "c1", "bill", record)
    assert re.load_cached_record(tmp_path, "c1", "bill") == record
    assert re.load_cached_record(tmp_path, "c1", "eob") is None


def test_cache_path_names_eob_distinctly(tmp_path):
    assert re.cache_path(tmp_path, "case_1", "bill").name == "case_1.json"
    assert re.cache_path(tmp_path, "case_1", "eob").name == "case_1_eob.json"


# -- run_task never calls the model on a cache hit --------------------------------


def test_run_task_uses_cache_and_never_calls_model(tmp_path, monkeypatch):
    cached = {"case_id": "c1", "kind": "bill", "payload": {"cached": True}, "usage": {}, "stop_reason": "end_turn", "attempts": 1, "error": None}
    re.save_cached_record(tmp_path, "c1", "bill", cached)

    def _boom(*args, **kwargs):
        raise AssertionError("extract_document must not be called when a cache entry exists")

    monkeypatch.setattr(re, "extract_document", _boom)

    task = re.DocTask("c1", "bill", "CLEAN", Path("unused.png"))
    result = re.run_task(task, tmp_path)
    assert result == cached


def test_run_task_force_ignores_cache_and_calls_model(tmp_path, monkeypatch):
    stale = {"case_id": "c1", "kind": "bill", "payload": {"stale": True}, "usage": {}, "stop_reason": "end_turn", "attempts": 1, "error": None}
    re.save_cached_record(tmp_path, "c1", "bill", stale)

    class _FakeResult:
        payload = {"fresh": True}
        usage = {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}
        stop_reason = "end_turn"

    monkeypatch.setattr(re, "extract_document", lambda pages, kind, image_format="png": _FakeResult())

    img = tmp_path / "img.png"
    img.write_bytes(b"fake-png")
    task = re.DocTask("c1", "bill", "CLEAN", img)
    result = re.run_task(task, tmp_path, force=True)
    assert result["payload"] == {"fresh": True}
    assert re.load_cached_record(tmp_path, "c1", "bill")["payload"] == {"fresh": True}


# -- retry with backoff ------------------------------------------------------------


def test_extract_with_retry_retries_then_succeeds(tmp_path, monkeypatch):
    img = tmp_path / "img.png"
    img.write_bytes(b"fake-png")
    task = re.DocTask("c1", "bill", "CLEAN", img)

    calls = {"n": 0}

    class _FakeResult:
        payload = {"ok": True}
        usage = {"inputTokens": 3, "outputTokens": 4, "totalTokens": 7}
        stop_reason = "end_turn"

    def _flaky(pages, kind, image_format="png"):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("rate limited")
        return _FakeResult()

    monkeypatch.setattr(re, "extract_document", _flaky)
    sleeps: list[float] = []

    record = re.extract_with_retry(task, max_attempts=4, base_delay=0.01, sleep=sleeps.append)

    assert record["error"] is None
    assert record["payload"] == {"ok": True}
    assert record["attempts"] == 3
    assert len(sleeps) == 2  # slept before attempt 2 and attempt 3, not after success


def test_extract_with_retry_records_failure_after_max_attempts(tmp_path, monkeypatch):
    img = tmp_path / "img.png"
    img.write_bytes(b"fake-png")
    task = re.DocTask("c1", "eob", "EOB_BALANCE_BILLING", img)

    def _always_fails(pages, kind, image_format="png"):
        raise RuntimeError("still rate limited")

    monkeypatch.setattr(re, "extract_document", _always_fails)
    sleeps: list[float] = []

    record = re.extract_with_retry(task, max_attempts=3, base_delay=0.01, sleep=sleeps.append)

    assert record["error"] == "RuntimeError: still rate limited"
    assert record["payload"] is None
    assert record["attempts"] == 3
    assert len(sleeps) == 2  # never sleeps after the last attempt


# -- concurrency cap ----------------------------------------------------------------


def test_run_all_tasks_never_exceeds_concurrency_cap(tmp_path, monkeypatch):
    tasks = []
    for i in range(8):
        img = tmp_path / f"img_{i}.png"
        img.write_bytes(b"fake-png")
        tasks.append(re.DocTask(f"c{i}", "bill", "CLEAN", img))

    lock = threading.Lock()
    state = {"current": 0, "max_seen": 0}

    class _FakeResult:
        payload = {"ok": True}
        usage = {}
        stop_reason = "end_turn"

    def _tracked(pages, kind, image_format="png"):
        with lock:
            state["current"] += 1
            state["max_seen"] = max(state["max_seen"], state["current"])
        time.sleep(0.05)
        with lock:
            state["current"] -= 1
        return _FakeResult()

    monkeypatch.setattr(re, "extract_document", _tracked)

    re.run_all_tasks(tasks, tmp_path, concurrency=10)  # requesting 10 must still cap at 4

    assert state["max_seen"] <= 4


# -- scoring orchestration ------------------------------------------------------------


def test_score_documents_uses_cases_and_records():
    bill = make_bill_(units=1)
    case = {"case_id": "c1", "category": "CLEAN", "bill": bill.model_dump(mode="json"), "eob": None}
    records = {
        ("c1", "bill"): {"case_id": "c1", "kind": "bill", "payload": bill.model_dump(mode="json"), "error": None, "usage": {}, "attempts": 1}
    }
    scorecard = re.score_documents([case], records)
    result = scorecard.to_dict()
    assert result["documents_total"] == 1
    assert result["mismatches"] == []


def test_score_documents_records_extraction_failure():
    bill = make_bill_(units=1)
    case = {"case_id": "c1", "category": "CLEAN", "bill": bill.model_dump(mode="json"), "eob": None}
    records = {("c1", "bill"): {"case_id": "c1", "kind": "bill", "payload": None, "error": "boom", "usage": {}, "attempts": 4}}
    scorecard = re.score_documents([case], records)
    result = scorecard.to_dict()
    assert result["extraction_failures"] == [{"case_id": "c1", "kind": "bill", "error": "boom"}]


def test_score_end_to_end_matches_when_extraction_is_perfect(refdata):
    bill = make_bill_(units=1)  # 99213 x1, at the MUE limit -- no findings
    report = audit(bill, refdata=refdata)
    case = {"case_id": "c1", "category": "CLEAN", "bill": bill.model_dump(mode="json"), "eob": None, "household": None, "gfe": None}
    answers = {
        "c1": {
            "expected_rules": sorted({f.rule_id for f in report.findings}),
            "expected_disputable_cents": report.disputable_cents,
            "expected_fap_tier": report.fap.tier.value if report.fap else None,
        }
    }
    records = {
        ("c1", "bill"): {"payload": bill.model_dump(mode="json"), "error": None},
    }

    scorecard = re.score_end_to_end([case], records, answers, refdata)
    result = scorecard.to_dict()
    assert result["rules_exact_match_cases"] == 1
    assert result["mismatches"] == []


def test_score_end_to_end_reports_mismatch_when_extraction_changes_the_findings(refdata):
    ground_truth_bill = make_bill_(units=1)
    extracted_bill = make_bill_(units=5)  # now exceeds the MUE limit of 1

    report = audit(ground_truth_bill, refdata=refdata)
    case = {
        "case_id": "c2",
        "category": "MUE",
        "bill": ground_truth_bill.model_dump(mode="json"),
        "eob": None,
        "household": None,
        "gfe": None,
    }
    answers = {
        "c2": {
            "expected_rules": sorted({f.rule_id for f in report.findings}),
            "expected_disputable_cents": report.disputable_cents,
            "expected_fap_tier": None,
        }
    }
    records = {("c2", "bill"): {"payload": extracted_bill.model_dump(mode="json"), "error": None}}

    scorecard = re.score_end_to_end([case], records, answers, refdata)
    result = scorecard.to_dict()
    assert result["mismatches"] != []
    assert result["mismatches"][0]["case_id"] == "c2"
    assert "MUE" in result["mismatches"][0]["false_positive_rules"]


def test_score_end_to_end_treats_failed_extraction_as_no_findings(refdata):
    ground_truth_bill = make_bill_(units=5)  # would trigger MUE if extracted correctly
    report = audit(ground_truth_bill, refdata=refdata)
    assert report.findings  # sanity: this ground-truth bill does have findings

    case = {
        "case_id": "c3",
        "category": "MUE",
        "bill": ground_truth_bill.model_dump(mode="json"),
        "eob": None,
        "household": None,
        "gfe": None,
    }
    answers = {
        "c3": {
            "expected_rules": sorted({f.rule_id for f in report.findings}),
            "expected_disputable_cents": report.disputable_cents,
            "expected_fap_tier": None,
        }
    }
    records = {("c3", "bill"): {"payload": None, "error": "exhausted retries"}}

    scorecard = re.score_end_to_end([case], records, answers, refdata)
    result = scorecard.to_dict()
    assert result["mismatches"][0]["actual_rules"] == []
    assert result["mismatches"][0]["actual_disputable_cents"] == 0


# -- cost / token accounting -----------------------------------------------------------


def test_token_totals_sums_usage_across_records():
    records = {
        ("c1", "bill"): {"usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}, "attempts": 1},
        ("c1", "eob"): {"usage": {"inputTokens": 20, "outputTokens": 8, "totalTokens": 28}, "attempts": 2},
        ("c2", "bill"): {"usage": {}, "attempts": 4},  # failed extraction: no usage
    }
    totals = re.token_totals(records)
    assert totals["documents"] == 3
    assert totals["input_tokens"] == 30
    assert totals["output_tokens"] == 13
    assert totals["total_tokens"] == 43


# -- end-to-end run() wiring with a fully-cached corpus (no network) -------------------


def test_run_uses_cache_for_every_document_and_never_calls_the_model(tmp_path, monkeypatch):
    bill = make_bill_(units=1)
    case = {"case_id": "c1", "category": "CLEAN", "bill": bill.model_dump(mode="json"), "eob": None, "household": None, "gfe": None}

    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "c1.json").write_text(json.dumps(case), encoding="utf-8")

    rendered_dir = tmp_path / "rendered"
    rendered_dir.mkdir()
    (rendered_dir / "c1.png").write_bytes(b"fake-png")

    key_path = tmp_path / "answers.json"
    key_path.write_text(json.dumps({"c1": {"expected_rules": [], "expected_disputable_cents": 0, "expected_fap_tier": None}}), encoding="utf-8")

    cache_dir = tmp_path / "raw"
    re.save_cached_record(
        cache_dir, "c1", "bill",
        {"case_id": "c1", "kind": "bill", "payload": bill.model_dump(mode="json"), "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "stop_reason": "end_turn", "attempts": 1, "error": None},
    )

    def _boom(*args, **kwargs):
        raise AssertionError("must not call the model when everything is cached")

    monkeypatch.setattr(re, "extract_document", _boom)

    # build a tiny refdata db inline (avoid depending on fixture injection into this test)
    import sqlite3
    from countercharge_engine.refdata.sqlite import SCHEMA_SQL

    refdata_path = tmp_path / "refdata.sqlite"
    conn = sqlite3.connect(refdata_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT INTO meta VALUES (?, ?)", ("version", "test-fixture"))
    conn.commit()
    conn.close()

    result = re.run(
        cases_dir=cases_dir,
        rendered_dir=rendered_dir,
        key_path=key_path,
        refdata_path=refdata_path,
        cache_dir=cache_dir,
    )

    assert result["run"]["subset"] is False
    assert result["run"]["documents_evaluated"] == 1
    assert result["field_accuracy"]["mismatches"] == []
    assert result["end_to_end"]["rules_exact_match_cases"] == 1
