"""Build static demo data for the Countercharge claims-desk website.

Runs the real audit engine (`countercharge_engine.audit`) against a fixed
set of showcase cases from `evals/corpus/cases/`, using the real refdata
sqlite bundle -- no invented numbers. Copies real rendered bill PNGs, the
real engine scorecard, and renders the real Cedar policies (via the
`countercharge_policies` package) into `web/data/`.

Run from the countercharge repo root:

    uv run web/scripts/build_data.py

The Cedar policy render step shells out to `uv run --project policies`
since `policies/` is its own uv project, not a member of the root
workspace.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"
REFDATA_PATH = Path(r"C:\Users\egori\Desktop\projects\agents-for-humans\internal\refdata\refdata.sqlite")
AGENT_TRANSCRIPT_SRC = Path(r"C:\Users\egori\Desktop\projects\agents-for-humans\internal\deploy\agent-transcript.json")
PLACEHOLDER_GATEWAY_ARN = "arn:aws:bedrock-agentcore:us-east-1:ACCOUNT:gateway/cc-gateway"

CASE_IDS = [
    "multi_01_selfpay_nyp",     # multi-error bill
    "fap_free_01",              # charity-care FAP FREE case
    "nsa_emergency_01",         # No Surprises Act case
    "adversarial_exfil_email",  # adversarial prompt-injection case
]

sys.path.insert(0, str(REPO_ROOT / "engine" / "src"))

from countercharge_engine.audit import audit  # noqa: E402
from countercharge_engine.models import Bill, EOB, GFE, Household  # noqa: E402
from countercharge_engine.refdata.sqlite import SqliteRefData  # noqa: E402


def build_cases(refdata: SqliteRefData) -> None:
    cases_dir = REPO_ROOT / "evals" / "corpus" / "cases"
    rendered_dir = REPO_ROOT / "evals" / "corpus" / "rendered"
    out_cases = WEB_DIR / "data" / "cases"
    out_bills = WEB_DIR / "assets" / "bills"
    out_cases.mkdir(parents=True, exist_ok=True)
    out_bills.mkdir(parents=True, exist_ok=True)

    for case_id in CASE_IDS:
        case = json.loads((cases_dir / f"{case_id}.json").read_text(encoding="utf-8"))

        bill = Bill(**case["bill"])
        eob = EOB(**case["eob"]) if case.get("eob") is not None else None
        household = Household(**case["household"]) if case.get("household") is not None else None
        gfe = GFE(**case["gfe"]) if case.get("gfe") is not None else None

        report = audit(bill, refdata=refdata, eob=eob, household=household, gfe=gfe)

        payload = {
            "case": case,
            "report": json.loads(report.model_dump_json()),
        }
        (out_cases / f"{case_id}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

        for suffix in ("", "_eob"):
            src = rendered_dir / f"{case_id}{suffix}.png"
            if src.exists():
                shutil.copyfile(src, out_bills / src.name)

        print(f"  {case_id}: {len(report.findings)} findings, "
              f"${report.disputable_cents / 100:,.2f} disputable, "
              f"fap={report.fap.tier.value if report.fap else None}")


def build_scorecard() -> None:
    src = REPO_ROOT / "evals" / "results" / "engine-scorecard.json"
    dst = WEB_DIR / "data" / "engine-scorecard.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"  copied {src} -> {dst}")


_REFDATA_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$"
)


def build_refdata_versions() -> None:
    md = (REPO_ROOT / "engine" / "data" / "REFDATA.md").read_text(encoding="utf-8")
    rows = []
    for line in md.splitlines():
        m = _REFDATA_ROW_RE.match(line.strip())
        if not m:
            continue
        dataset, version, url, rows_str, retrieved = (g.strip() for g in m.groups())
        if dataset in ("dataset", "---"):
            continue
        rows.append(
            {
                "dataset": dataset,
                "version": version,
                "url": url,
                "rows": rows_str,
                "retrieved": retrieved,
            }
        )
    dst = WEB_DIR / "data" / "refdata.json"
    dst.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"  wrote {len(rows)} refdata rows -> {dst}")


def build_policies() -> None:
    policies_dir = REPO_ROOT / "policies"
    hospitals_path = REPO_ROOT / "engine" / "data" / "hospitals.json"
    script = (
        "import json, sys\n"
        "from countercharge_policies import render\n"
        f"hospitals = json.loads(open(r'{hospitals_path}', encoding='utf-8').read())\n"
        f"policies = render({PLACEHOLDER_GATEWAY_ARN!r}, hospitals)\n"
        "sys.stdout.write(json.dumps(policies))\n"
    )
    result = subprocess.run(
        ["uv", "run", "--project", str(policies_dir), "python", "-c", script],
        cwd=str(policies_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"policy render failed:\n{result.stderr}")
    policies = json.loads(result.stdout)
    dst = WEB_DIR / "data" / "policies.json"
    dst.write_text(
        json.dumps(
            {"gateway_arn": PLACEHOLDER_GATEWAY_ARN, "policies": policies},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  rendered {len(policies)} Cedar policies -> {dst}")


def build_agent_transcript() -> None:
    dst = WEB_DIR / "data" / "agent-transcript.json"
    if AGENT_TRANSCRIPT_SRC.exists():
        shutil.copyfile(AGENT_TRANSCRIPT_SRC, dst)
        print(f"  copied real agent transcript -> {dst}")
    else:
        if dst.exists():
            dst.unlink()
        print("  no recorded agent transcript found -- replay panel will show 'recording pending'")


def main() -> None:
    print("Countercharge web data build")
    print(f"  refdata: {REFDATA_PATH}")
    refdata = SqliteRefData(str(REFDATA_PATH))

    print("Auditing showcase cases...")
    build_cases(refdata)

    print("Copying engine scorecard...")
    build_scorecard()

    print("Extracting refdata versions...")
    build_refdata_versions()

    print("Rendering Cedar policies...")
    build_policies()

    print("Agent transcript...")
    build_agent_transcript()

    print("Done.")


if __name__ == "__main__":
    main()
