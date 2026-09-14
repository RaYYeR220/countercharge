"""Run the real ``countercharge_engine.audit`` over every corpus case and
grade its findings against the pre-registered answer key.

This is the only module in this package allowed to import ``audit`` --
the corpus and the key were built in ``seeds.py``/``generate.py``
without it (see that module's docstring), so grading the engine here is
not circular: the key is independent of the thing being measured.

Mismatches are written to the scorecard and reported, never hidden or
patched away.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from countercharge_engine.audit import audit
from countercharge_engine.models import Bill, EOB, GFE, Household
from countercharge_engine.refdata.sqlite import SqliteRefData

from countercharge_evals.score import CaseResult, Scorecard


def _load_case(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(cases_dir: Path, key_path: Path, refdata_path: Path) -> Scorecard:
    refdata = SqliteRefData(refdata_path)
    answers = json.loads(key_path.read_text(encoding="utf-8"))

    scorecard = Scorecard()
    for case_file in sorted(cases_dir.glob("*.json")):
        case = _load_case(case_file)
        case_id = case["case_id"]
        answer = answers[case_id]

        bill = Bill(**case["bill"])
        eob = EOB(**case["eob"]) if case.get("eob") is not None else None
        household = Household(**case["household"]) if case.get("household") is not None else None
        gfe = GFE(**case["gfe"]) if case.get("gfe") is not None else None

        report = audit(bill, refdata=refdata, eob=eob, household=household, gfe=gfe)
        actual_rules = sorted({f.rule_id for f in report.findings})
        actual_fap_tier = report.fap.tier.value if report.fap is not None else None

        scorecard.add(
            CaseResult(
                case_id=case_id,
                category=case["category"],
                expected_rules=answer["expected_rules"],
                actual_rules=actual_rules,
                expected_disputable_cents=answer["expected_disputable_cents"],
                actual_disputable_cents=report.disputable_cents,
                expected_fap_tier=answer["expected_fap_tier"],
                actual_fap_tier=actual_fap_tier,
            )
        )

    return scorecard


def _write_markdown(scorecard_dict: dict, out_md: Path) -> None:
    lines = [
        "# Countercharge engine scorecard",
        "",
        f"- Cases: {scorecard_dict['total_cases']}",
        f"- Cases with an exact rule-set match: {scorecard_dict['rules_exact_match_cases']}/{scorecard_dict['total_cases']}",
        f"- Exact disputable-cents match rate: {scorecard_dict['exact_amount_match_rate']:.1%}",
        f"- FAP tier accuracy: {scorecard_dict['fap_tier_accuracy']:.1%}",
        f"- Negative-control false positives: {scorecard_dict['negative_control_false_positive_count']}"
        + (f" ({', '.join(scorecard_dict['negative_control_false_positives'])})"
           if scorecard_dict['negative_control_false_positives'] else ""),
        "",
        "## Per-rule precision / recall",
        "",
        "| Rule | TP | FP | FN | Precision | Recall |",
        "|---|---|---|---|---|---|",
    ]
    for rule, m in sorted(scorecard_dict["per_rule"].items()):
        prec = f"{m['precision']:.0%}" if m["precision"] is not None else "n/a"
        rec = f"{m['recall']:.0%}" if m["recall"] is not None else "n/a"
        lines.append(f"| {rule} | {m['tp']} | {m['fp']} | {m['fn']} | {prec} | {rec} |")

    lines += ["", "## Mismatches", ""]
    if not scorecard_dict["mismatches"]:
        lines.append("None.")
    else:
        for m in scorecard_dict["mismatches"]:
            lines.append(f"- **{m['case_id']}** ({m['category']})")
            if m["false_positive_rules"]:
                lines.append(f"  - unexpected rules fired: {m['false_positive_rules']}")
            if m["false_negative_rules"]:
                lines.append(f"  - expected rules missing: {m['false_negative_rules']}")
            if m["expected_disputable_cents"] != m["actual_disputable_cents"]:
                lines.append(
                    f"  - disputable_cents expected {m['expected_disputable_cents']}, "
                    f"got {m['actual_disputable_cents']}"
                )
            if m["expected_fap_tier"] != m["actual_fap_tier"]:
                lines.append(f"  - fap tier expected {m['expected_fap_tier']}, got {m['actual_fap_tier']}")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parents[2]
    parser.add_argument("--cases", type=Path, default=here / "corpus" / "cases")
    parser.add_argument("--key", type=Path, default=here / "key" / "answers.json")
    parser.add_argument(
        "--refdata",
        type=Path,
        default=Path(os.environ["COUNTERCHARGE_REFDATA"]) if os.environ.get("COUNTERCHARGE_REFDATA") else None,
    )
    parser.add_argument("--out-json", type=Path, default=here / "results" / "engine-scorecard.json")
    parser.add_argument("--out-md", type=Path, default=here / "results" / "engine-scorecard.md")
    args = parser.parse_args()

    if args.refdata is None:
        raise SystemExit("pass --refdata or set COUNTERCHARGE_REFDATA")

    scorecard = run(args.cases, args.key, args.refdata)
    result = scorecard.to_dict()

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    _write_markdown(result, args.out_md)

    print(
        f"{result['rules_exact_match_cases']}/{result['total_cases']} cases exact rule-set match, "
        f"{result['exact_amount_match_rate']:.1%} exact amount match, "
        f"{result['negative_control_false_positive_count']} negative-control false positives"
    )


if __name__ == "__main__":
    main()
