"""Run the real vision extractor over every rendered bill/EOB image in the
corpus and grade it against the case-JSON ground truth (Task B):

1. Field-level extraction accuracy per document -- line items (code, units,
   charge_cents, dos) exact, totals exact, provider name fuzzy (normalized),
   EOB fields exact. See ``extraction_score.py``.
2. End-to-end "photo -> findings" accuracy: run ``countercharge_engine.audit``
   on the *extracted* bill (ground-truth household/gfe, since those never
   come from a photo) and compare the rule set + disputable cents to the
   pre-registered key -- the same comparison ``run_engine.py`` makes against
   the ground-truth bill, just fed from the extractor's own output.

This module calls the real extractor (``countercharge_agent.subagents.
extract_document``, the same function the case agent's ``intake_extractor``
tool calls) with ``MODEL_PROVIDER=venice``. Every model call's structured
output is cached to ``evals/results/extraction-raw/`` (gitignored) keyed by
document id, so re-scoring after a code change replays the cache and makes
no further model calls -- delete a cache file to force one document to
re-run. Concurrency is capped at 4 in-flight model calls; each call retries
with exponential backoff before being recorded as a failure (never hidden).

Usage::

    uv run python -m countercharge_evals.run_extraction \\
        --refdata C:/.../internal/refdata/refdata.sqlite

    # stratified subset (e.g. if Venice rate-limits the full corpus):
    uv run python -m countercharge_evals.run_extraction --sample 20 --refdata ...
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from countercharge_engine.audit import audit
from countercharge_engine.models import EOB, Bill, GFE, Household
from countercharge_engine.refdata.sqlite import SqliteRefData

from countercharge_agent.subagents import extract_document

from countercharge_evals.extraction_score import DocResult, ExtractionScorecard, failed_doc, score_bill, score_eob
from countercharge_evals.score import CaseResult, Scorecard

HERE = Path(__file__).resolve().parents[2]  # .../evals


# -- secrets ------------------------------------------------------------------


def find_secrets_path(start: Path) -> Path | None:
    """Walk upward from ``start`` looking for ``internal/secrets.env`` -- works whether
    this runs from the main checkout or a git worktree nested a level deeper."""
    for ancestor in (start, *start.parents):
        candidate = ancestor / "internal" / "secrets.env"
        if candidate.exists():
            return candidate
    return None


def load_secrets(path: Path | None) -> None:
    """Load ``KEY=value`` lines into the environment (never overriding an already-set
    var, never printed). No-op if ``path`` is ``None`` or missing."""
    if path is None or not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value:
            os.environ.setdefault(key.strip(), value)


# -- corpus / tasks -------------------------------------------------------------


@dataclass
class DocTask:
    case_id: str
    kind: str  # "bill" | "eob"
    category: str
    image_path: Path


@dataclass
class CaseFixture:
    case_id: str
    category: str
    bill: Bill
    eob: EOB | None
    household: Household | None
    gfe: GFE | None


def load_cases(cases_dir: Path, case_ids: set[str] | None = None) -> list[dict]:
    cases = []
    for case_file in sorted(cases_dir.glob("*.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        if case_ids is not None and case["case_id"] not in case_ids:
            continue
        cases.append(case)
    return cases


def build_fixture(case: dict) -> CaseFixture:
    return CaseFixture(
        case_id=case["case_id"],
        category=case["category"],
        bill=Bill(**case["bill"]),
        eob=EOB(**case["eob"]) if case.get("eob") is not None else None,
        household=Household(**case["household"]) if case.get("household") is not None else None,
        gfe=GFE(**case["gfe"]) if case.get("gfe") is not None else None,
    )


def build_tasks(cases: list[dict], rendered_dir: Path) -> list[DocTask]:
    tasks: list[DocTask] = []
    for case in cases:
        case_id, category = case["case_id"], case["category"]
        tasks.append(DocTask(case_id, "bill", category, rendered_dir / f"{case_id}.png"))
        if case.get("eob") is not None:
            tasks.append(DocTask(case_id, "eob", category, rendered_dir / f"{case_id}_eob.png"))
    return tasks


def stratified_case_ids(cases: list[dict], n: int, seed: int) -> list[str]:
    """Deterministically pick ``n`` case ids spread across categories (round-robin
    over shuffled per-category groups), for a labeled-subset run when the full
    corpus can't be completed (e.g. Venice rate-limiting)."""
    rng = random.Random(seed)
    groups: dict[str, list[str]] = {}
    for case in cases:
        groups.setdefault(case["category"], []).append(case["case_id"])
    for ids in groups.values():
        rng.shuffle(ids)

    ordered_categories = sorted(groups)
    picked: list[str] = []
    while len(picked) < n and any(groups[cat] for cat in ordered_categories):
        for cat in ordered_categories:
            if groups[cat]:
                picked.append(groups[cat].pop())
            if len(picked) == n:
                break
    return picked


# -- caching + retry ------------------------------------------------------------


def cache_path(cache_dir: Path, case_id: str, kind: str) -> Path:
    name = case_id if kind == "bill" else f"{case_id}_{kind}"
    return cache_dir / f"{name}.json"


def load_cached_record(cache_dir: Path, case_id: str, kind: str) -> dict | None:
    path = cache_path(cache_dir, case_id, kind)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_cached_record(cache_dir: Path, case_id: str, kind: str, record: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, case_id, kind)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def extract_with_retry(
    task: DocTask,
    *,
    max_attempts: int = 4,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Call the real extractor for one document, retrying transient failures with
    exponential backoff + jitter. Returns a JSON-serializable record; a failure
    after ``max_attempts`` is recorded with ``error`` set, never raised."""
    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            image_bytes = task.image_path.read_bytes()
            result = extract_document([image_bytes], task.kind, image_format="png")
            return {
                "case_id": task.case_id,
                "kind": task.kind,
                "payload": result.payload,
                "usage": result.usage,
                "stop_reason": result.stop_reason,
                "attempts": attempt,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 -- network/model failures vary by provider
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max_attempts:
                sleep(base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1))

    return {
        "case_id": task.case_id,
        "kind": task.kind,
        "payload": None,
        "usage": {},
        "stop_reason": None,
        "attempts": max_attempts,
        "error": last_error,
    }


def run_task(
    task: DocTask,
    cache_dir: Path,
    *,
    force: bool = False,
    max_attempts: int = 4,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Resolve one document's extraction record, from cache when possible.

    Never calls the model when a cache entry already exists (unless ``force``) --
    this is what lets re-scoring run entirely offline.
    """
    if not force:
        cached = load_cached_record(cache_dir, task.case_id, task.kind)
        if cached is not None:
            return cached

    record = extract_with_retry(task, max_attempts=max_attempts, base_delay=base_delay, sleep=sleep)
    save_cached_record(cache_dir, task.case_id, task.kind, record)
    return record


def run_all_tasks(
    tasks: list[DocTask],
    cache_dir: Path,
    *,
    concurrency: int = 4,
    force: bool = False,
    max_attempts: int = 4,
    base_delay: float = 2.0,
) -> dict[tuple[str, str], dict]:
    """Run (or replay from cache) every task, at most ``concurrency`` (capped at 4)
    model calls in flight at once."""
    workers = max(1, min(concurrency, 4))
    records: dict[tuple[str, str], dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_task, task, cache_dir, force=force, max_attempts=max_attempts, base_delay=base_delay): task
            for task in tasks
        }
        for future, task in futures.items():
            records[(task.case_id, task.kind)] = future.result()
    return records


# -- scoring orchestration -------------------------------------------------------


def _rebuild(model_cls, record: dict):
    if record.get("error") is not None or record.get("payload") is None:
        return None
    return model_cls(**record["payload"])


def score_documents(cases: list[dict], records: dict[tuple[str, str], dict]) -> ExtractionScorecard:
    scorecard = ExtractionScorecard()
    for case in cases:
        case_id, category = case["case_id"], case["category"]

        bill_record = records.get((case_id, "bill"))
        if bill_record is not None:
            if bill_record["error"] is not None:
                scorecard.add(failed_doc(case_id, "bill", category, bill_record["error"]))
            else:
                expected_bill = Bill(**case["bill"])
                actual_bill = Bill(**bill_record["payload"])
                scorecard.add(score_bill(case_id, category, expected_bill, actual_bill))

        if case.get("eob") is not None:
            eob_record = records.get((case_id, "eob"))
            if eob_record is not None:
                if eob_record["error"] is not None:
                    scorecard.add(failed_doc(case_id, "eob", category, eob_record["error"]))
                else:
                    expected_eob = EOB(**case["eob"])
                    actual_eob = EOB(**eob_record["payload"])
                    scorecard.add(score_eob(case_id, category, expected_eob, actual_eob))

    return scorecard


def score_end_to_end(
    cases: list[dict],
    records: dict[tuple[str, str], dict],
    answers: dict,
    refdata: SqliteRefData,
) -> Scorecard:
    """Run the engine on the EXTRACTED bill (+ extracted EOB, when the document
    extracted cleanly) against ground-truth household/gfe (never photographed),
    and compare to the pre-registered key -- the true photo-to-findings check."""
    scorecard = Scorecard()
    for case in cases:
        case_id, category = case["case_id"], case["category"]
        answer = answers[case_id]

        bill_record = records.get((case_id, "bill"))
        extracted_bill = _rebuild(Bill, bill_record) if bill_record else None

        if extracted_bill is None:
            actual_rules: list[str] = []
            actual_disputable_cents = 0
            actual_fap_tier = None
        else:
            eob_record = records.get((case_id, "eob"))
            extracted_eob = _rebuild(EOB, eob_record) if eob_record else None
            household = Household(**case["household"]) if case.get("household") is not None else None
            gfe = GFE(**case["gfe"]) if case.get("gfe") is not None else None

            report = audit(extracted_bill, refdata=refdata, eob=extracted_eob, household=household, gfe=gfe)
            actual_rules = sorted({f.rule_id for f in report.findings})
            actual_disputable_cents = report.disputable_cents
            actual_fap_tier = report.fap.tier.value if report.fap is not None else None

        scorecard.add(
            CaseResult(
                case_id=case_id,
                category=category,
                expected_rules=answer["expected_rules"],
                actual_rules=actual_rules,
                expected_disputable_cents=answer["expected_disputable_cents"],
                actual_disputable_cents=actual_disputable_cents,
                expected_fap_tier=answer["expected_fap_tier"],
                actual_fap_tier=actual_fap_tier,
            )
        )
    return scorecard


def token_totals(records: dict[tuple[str, str], dict]) -> dict:
    input_tokens = output_tokens = total_tokens = 0
    model_calls = 0
    cache_hits = 0
    for record in records.values():
        usage = record.get("usage") or {}
        if usage:
            input_tokens += usage.get("inputTokens", 0)
            output_tokens += usage.get("outputTokens", 0)
            total_tokens += usage.get("totalTokens", 0)
        if record.get("attempts", 0) > 0:
            model_calls += 1
    return {
        "documents": len(records),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


# -- top-level run --------------------------------------------------------------


def run(
    *,
    cases_dir: Path,
    rendered_dir: Path,
    key_path: Path,
    refdata_path: Path,
    cache_dir: Path,
    case_ids: set[str] | None = None,
    concurrency: int = 4,
    force: bool = False,
    max_attempts: int = 4,
    base_delay: float = 2.0,
) -> dict:
    all_cases = load_cases(cases_dir)
    cases = [c for c in all_cases if case_ids is None or c["case_id"] in case_ids]
    answers = json.loads(key_path.read_text(encoding="utf-8"))
    refdata = SqliteRefData(refdata_path)

    tasks = build_tasks(cases, rendered_dir)
    records = run_all_tasks(tasks, cache_dir, concurrency=concurrency, force=force, max_attempts=max_attempts, base_delay=base_delay)

    doc_scorecard = score_documents(cases, records)
    e2e_scorecard = score_end_to_end(cases, records, answers, refdata)
    cost = token_totals(records)

    is_subset = len(cases) < len(all_cases)
    return {
        "run": {
            "subset": is_subset,
            "corpus_size": len(all_cases),
            "cases_evaluated": len(cases),
            "documents_evaluated": len(tasks),
            "model_provider": os.environ.get("MODEL_PROVIDER", ""),
        },
        "field_accuracy": doc_scorecard.to_dict(),
        "end_to_end": e2e_scorecard.to_dict(),
        "cost": cost,
    }


def _write_markdown(result: dict, out_md: Path) -> None:
    run_info = result["run"]
    field = result["field_accuracy"]
    e2e = result["end_to_end"]
    cost = result["cost"]

    lines = ["# Countercharge extraction scorecard", ""]
    if run_info["subset"]:
        lines.append(
            f"**SUBSET RUN** -- {run_info['cases_evaluated']}/{run_info['corpus_size']} cases "
            f"({run_info['documents_evaluated']} documents). Not the full corpus."
        )
        lines.append("")
    exact_docs = field["documents_total"] - len(field["mismatches"])
    lines += [
        f"- Model provider: `{run_info['model_provider']}`",
        f"- Documents evaluated: {run_info['documents_evaluated']} "
        f"({field['bill_documents']} bill, {field['eob_documents']} eob)",
        f"- Documents with an exact field match: {exact_docs}/{field['documents_total']}",
    ]
    lines.append(f"- Bill line exact-match rate: {_pct(field['bill_line_exact_match_rate'])}")
    lines.append(f"- Bill totals exact-match rate: {_pct(field['bill_totals_exact_match_rate'])}")
    lines.append(f"- Provider name fuzzy-match rate: {_pct(field['provider_name_fuzzy_match_rate'])}")
    lines.append(f"- EOB line exact-match rate: {_pct(field['eob_line_exact_match_rate'])}")
    lines.append(f"- EOB scalar field exact-match rate: {_pct(field['eob_scalar_exact_match_rate'])}")
    lines.append("")
    lines.append("## End-to-end: photo -> findings")
    lines.append("")
    lines.append(
        f"- Cases with an exact rule-set match (engine run on the EXTRACTED bill): "
        f"{e2e['rules_exact_match_cases']}/{run_info['cases_evaluated']}"
    )
    lines.append(f"- Exact disputable-cents match rate: {_pct(e2e['exact_amount_match_rate'])}")
    lines.append(f"- FAP tier accuracy: {_pct(e2e['fap_tier_accuracy'])}")
    lines.append(f"- Negative-control false positives: {e2e['negative_control_false_positive_count']}")
    lines.append("")
    lines.append("## Cost")
    lines.append("")
    lines.append(
        f"- Token usage across {cost['documents']} extraction calls: "
        f"{cost['input_tokens']} in / {cost['output_tokens']} out / {cost['total_tokens']} total"
    )
    lines.append("")
    lines.append("## Per-category field accuracy")
    lines.append("")
    lines.append("| Category | Documents | Exact match | Rate |")
    lines.append("|---|---|---|---|")
    for cat, m in sorted(field["per_category"].items()):
        lines.append(f"| {cat} | {m['documents']} | {m['exact_match_documents']} | {_pct(m['exact_match_rate'])} |")
    lines.append("")

    lines.append("## Field-level mismatches")
    lines.append("")
    if not field["mismatches"]:
        lines.append("None.")
    else:
        for m in field["mismatches"]:
            lines.append(f"- **{m['case_id']}** ({m['kind']}, {m['category']})")
            for issue in m["issues"]:
                lines.append(f"  - {issue}")
    lines.append("")

    lines.append("## End-to-end mismatches (extracted bill vs pre-registered key)")
    lines.append("")
    if not e2e["mismatches"]:
        lines.append("None.")
    else:
        for m in e2e["mismatches"]:
            lines.append(f"- **{m['case_id']}** ({m['category']})")
            if m["false_positive_rules"]:
                lines.append(f"  - unexpected rules fired: {m['false_positive_rules']}")
            if m["false_negative_rules"]:
                lines.append(f"  - expected rules missing: {m['false_negative_rules']}")
            if m["expected_disputable_cents"] != m["actual_disputable_cents"]:
                lines.append(
                    f"  - disputable_cents expected {m['expected_disputable_cents']}, got {m['actual_disputable_cents']}"
                )
            if m["expected_fap_tier"] != m["actual_fap_tier"]:
                lines.append(f"  - fap tier expected {m['expected_fap_tier']}, got {m['actual_fap_tier']}")
    lines.append("")

    if field["extraction_failures"]:
        lines.append("## Extraction failures (exhausted retries)")
        lines.append("")
        for f in field["extraction_failures"]:
            lines.append(f"- **{f['case_id']}** ({f['kind']}): {f['error']}")
        lines.append("")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _pct(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "n/a"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=HERE / "corpus" / "cases")
    parser.add_argument("--rendered", type=Path, default=HERE / "corpus" / "rendered")
    parser.add_argument("--key", type=Path, default=HERE / "key" / "answers.json")
    parser.add_argument(
        "--refdata",
        type=Path,
        default=Path(os.environ["COUNTERCHARGE_REFDATA"]) if os.environ.get("COUNTERCHARGE_REFDATA") else None,
    )
    parser.add_argument("--cache-dir", type=Path, default=HERE / "results" / "extraction-raw")
    parser.add_argument("--out-json", type=Path, default=HERE / "results" / "extraction-scorecard.json")
    parser.add_argument("--out-md", type=Path, default=HERE / "results" / "extraction-scorecard.md")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-attempts", type=int, default=4)
    parser.add_argument("--base-delay", type=float, default=2.0)
    parser.add_argument("--force", action="store_true", help="ignore cached raw outputs and re-call the model")
    parser.add_argument("--case-ids", type=str, default="", help="comma-separated case_ids to restrict the run to")
    parser.add_argument("--sample", type=int, default=0, help="stratified subset size (0 = full corpus)")
    parser.add_argument("--sample-seed", type=int, default=2026)
    args = parser.parse_args()

    if args.refdata is None:
        raise SystemExit("pass --refdata or set COUNTERCHARGE_REFDATA")

    load_secrets(find_secrets_path(HERE))
    os.environ.setdefault("MODEL_PROVIDER", "venice")

    case_ids: set[str] | None = None
    if args.case_ids:
        case_ids = {c.strip() for c in args.case_ids.split(",") if c.strip()}
    elif args.sample:
        all_cases = load_cases(args.cases)
        case_ids = set(stratified_case_ids(all_cases, args.sample, args.sample_seed))

    result = run(
        cases_dir=args.cases,
        rendered_dir=args.rendered,
        key_path=args.key,
        refdata_path=args.refdata,
        cache_dir=args.cache_dir,
        case_ids=case_ids,
        concurrency=args.concurrency,
        force=args.force,
        max_attempts=args.max_attempts,
        base_delay=args.base_delay,
    )

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    _write_markdown(result, args.out_md)

    field, e2e, run_info = result["field_accuracy"], result["end_to_end"], result["run"]
    subset_note = " (SUBSET)" if run_info["subset"] else ""
    print(
        f"{run_info['documents_evaluated']} documents{subset_note}: "
        f"{field['documents_total'] - len(field['mismatches'])}/{field['documents_total']} exact field match, "
        f"{e2e['rules_exact_match_cases']}/{run_info['cases_evaluated']} e2e rule-set match, "
        f"{e2e['exact_amount_match_rate']:.1%} e2e exact amount match"
    )


if __name__ == "__main__":
    main()
