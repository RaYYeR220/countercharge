"""Smoke test: the renderer produces a non-empty PDF and PNG for one
case per bill layout (plus its EOB, when it has one)."""

import asyncio
import json
from pathlib import Path

import pytest

from countercharge_evals.render import render_all

CASES_DIR = Path(__file__).resolve().parents[1] / "corpus" / "cases"


def _one_case_per_layout(tmp_path: Path) -> Path:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    seen: set[str] = set()
    picked = 0
    for case_file in sorted(CASES_DIR.glob("*.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        layout = case["layout"]
        if layout in seen:
            continue
        seen.add(layout)
        (cases_dir / case_file.name).write_text(case_file.read_text(encoding="utf-8"), encoding="utf-8")
        picked += 1
    assert picked >= 3, "expected at least one case per bill layout in the corpus"
    return cases_dir


def test_renderer_produces_nonempty_pdf_and_png(tmp_path):
    if not CASES_DIR.exists() or not any(CASES_DIR.glob("*.json")):
        pytest.skip("run countercharge_evals.generate first")

    cases_dir = _one_case_per_layout(tmp_path)
    out_dir = tmp_path / "rendered"

    written = asyncio.run(render_all(cases_dir, out_dir))
    assert written

    for name in written:
        pdf = out_dir / f"{name}.pdf"
        png = out_dir / f"{name}.png"
        assert pdf.exists() and pdf.stat().st_size > 1000, f"{pdf} missing or too small"
        assert png.exists() and png.stat().st_size > 1000, f"{png} missing or too small"
