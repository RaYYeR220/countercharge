"""Command-line entry point for ``countercharge_engine``.

    python -m countercharge_engine audit --bill bill.json [--eob eob.json] \\
        [--household household.json] [--gfe gfe.json] --refdata data/refdata.sqlite

    python -m countercharge_engine schema --out ../schemas/
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from countercharge_engine.audit import audit
from countercharge_engine.models import AuditReport, Bill, EOB, Finding, GFE, Household
from countercharge_engine.refdata.sqlite import SqliteRefData

_SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "Bill": Bill,
    "EOB": EOB,
    "Household": Household,
    "GFE": GFE,
    "Finding": Finding,
    "AuditReport": AuditReport,
}


def _load_model(path: str | None, model: type[BaseModel]) -> BaseModel | None:
    if path is None:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return model.model_validate(data)


def _cmd_audit(args: argparse.Namespace) -> int:
    bill = _load_model(args.bill, Bill)
    assert isinstance(bill, Bill)
    eob = _load_model(args.eob, EOB)
    household = _load_model(args.household, Household)
    gfe = _load_model(args.gfe, GFE)
    refdata = SqliteRefData(Path(args.refdata))

    report = audit(bill, refdata=refdata, eob=eob, household=household, gfe=gfe)
    print(report.model_dump_json(indent=2))
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, model in _SCHEMA_MODELS.items():
        schema = model.model_json_schema()
        (out_dir / f"{name}.json").write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="countercharge_engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="Audit a bill against refdata.")
    audit_parser.add_argument("--bill", required=True, help="Path to a Bill JSON file.")
    audit_parser.add_argument("--eob", default=None, help="Path to an EOB JSON file.")
    audit_parser.add_argument(
        "--household", default=None, help="Path to a Household JSON file."
    )
    audit_parser.add_argument("--gfe", default=None, help="Path to a GFE JSON file.")
    audit_parser.add_argument(
        "--refdata", required=True, help="Path to a refdata.sqlite database."
    )
    audit_parser.set_defaults(func=_cmd_audit)

    schema_parser = subparsers.add_parser("schema", help="Export model JSON Schemas.")
    schema_parser.add_argument(
        "--out", required=True, help="Directory to write JSON Schema files into."
    )
    schema_parser.set_defaults(func=_cmd_schema)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
