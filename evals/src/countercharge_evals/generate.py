"""CLI: build the pre-registered corpus + answer key from ``seeds.py``.

Usage::

    uv run python -m countercharge_evals.generate --seed 2026 \\
        --refdata C:/.../internal/refdata/refdata.sqlite \\
        --out corpus/cases --key key/answers.json --hash key/key.sha256

Deterministic: the same ``--seed`` and refdata file always produce
byte-identical case JSON and answer key files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from countercharge_engine.models import Bill, EOB, GFE, Household
from countercharge_engine.refdata.sqlite import SqliteRefData

from countercharge_evals.seeds import build_all_cases


def _canonical_dump(obj: dict) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _validate_schema(case: dict) -> None:
    """Sanity-check the seeded bill/eob/household/gfe against the engine's
    own pydantic models. This is schema validation, not rule evaluation --
    it never touches ``countercharge_engine.audit`` or ``.rules``."""
    Bill(**case["bill"])
    if case.get("eob") is not None:
        EOB(**case["eob"])
    if case.get("household") is not None:
        Household(**case["household"])
    if case.get("gfe") is not None:
        GFE(**case["gfe"])


def generate(seed: int, refdata_path: Path, out_dir: Path, key_path: Path, hash_path: Path) -> int:
    refdata = SqliteRefData(refdata_path)
    built = build_all_cases(seed, refdata)

    out_dir.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    answers: dict[str, dict] = {}
    seen_ids: set[str] = set()
    for item in built:
        case, answer = item["case"], item["answer"]
        case_id = case["case_id"]
        if case_id in seen_ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen_ids.add(case_id)

        _validate_schema(case)

        case_file = out_dir / f"{case_id}.json"
        case_file.write_text(_canonical_dump(case), encoding="utf-8", newline="\n")
        answers[case_id] = answer

    key_path.write_text(_canonical_dump(answers), encoding="utf-8", newline="\n")
    digest = hashlib.sha256(key_path.read_bytes()).hexdigest()
    hash_path.write_text(f"{digest}  {key_path.name}\n", encoding="utf-8", newline="\n")

    return len(built)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument(
        "--refdata",
        type=Path,
        default=Path(os.environ.get("COUNTERCHARGE_REFDATA", "")) if os.environ.get("COUNTERCHARGE_REFDATA") else None,
    )
    here = Path(__file__).resolve().parents[2]
    parser.add_argument("--out", type=Path, default=here / "corpus" / "cases")
    parser.add_argument("--key", type=Path, default=here / "key" / "answers.json")
    parser.add_argument("--hash", type=Path, default=here / "key" / "key.sha256")
    args = parser.parse_args()

    if args.refdata is None:
        raise SystemExit("pass --refdata or set COUNTERCHARGE_REFDATA")

    n = generate(args.seed, args.refdata, args.out, args.key, args.hash)
    print(f"wrote {n} cases to {args.out}, answer key to {args.key}")


if __name__ == "__main__":
    main()
