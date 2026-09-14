"""Same seed + same refdata must produce byte-identical corpus and key
files every time -- the whole point of a pre-registered eval."""

import hashlib
import os
from pathlib import Path

import pytest

from countercharge_evals.generate import generate

REFDATA_ENV = "COUNTERCHARGE_REFDATA"


def _refdata_path() -> Path:
    raw = os.environ.get(REFDATA_ENV)
    if not raw:
        pytest.skip(f"set {REFDATA_ENV} to internal/refdata/refdata.sqlite to run this test")
    path = Path(raw)
    if not path.exists():
        pytest.skip(f"refdata not found at {path}")
    return path


def _hash_tree(directory: Path) -> dict[str, str]:
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*.json"))
    }


def test_generate_is_deterministic(tmp_path):
    refdata = _refdata_path()

    out_a = tmp_path / "a"
    key_a = tmp_path / "a_key" / "answers.json"
    hash_a = tmp_path / "a_key" / "key.sha256"
    n_a = generate(2026, refdata, out_a, key_a, hash_a)

    out_b = tmp_path / "b"
    key_b = tmp_path / "b_key" / "answers.json"
    hash_b = tmp_path / "b_key" / "key.sha256"
    n_b = generate(2026, refdata, out_b, key_b, hash_b)

    assert n_a == n_b
    assert _hash_tree(out_a) == _hash_tree(out_b)
    assert key_a.read_bytes() == key_b.read_bytes()
    assert hash_a.read_text() == hash_b.read_text()


def test_generate_key_hash_matches_file():
    refdata = _refdata_path()
    key_path = Path(__file__).resolve().parents[1] / "key" / "answers.json"
    hash_path = Path(__file__).resolve().parents[1] / "key" / "key.sha256"
    if not key_path.exists():
        pytest.skip("run countercharge_evals.generate first")

    digest = hashlib.sha256(key_path.read_bytes()).hexdigest()
    recorded = hash_path.read_text().split()[0]
    assert digest == recorded
