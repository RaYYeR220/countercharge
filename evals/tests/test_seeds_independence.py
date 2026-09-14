"""The corpus's answer key must come from seeding logic, never from
running the engine -- otherwise grading the engine against it would be
circular. This is a static, structural guarantee: ``seeds.py`` (and its
``refcheck.py`` helper) must never import the audit orchestrator or any
rule module."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "countercharge_evals"
FORBIDDEN_MODULES = {"countercharge_engine.audit"}
FORBIDDEN_PACKAGE_PREFIX = "countercharge_engine.rules"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_seeds_module_does_not_import_audit_or_rules():
    for filename in ("seeds.py", "refcheck.py"):
        imports = _imported_modules(SRC / filename)
        bad = imports & FORBIDDEN_MODULES
        bad |= {m for m in imports if m.startswith(FORBIDDEN_PACKAGE_PREFIX)}
        assert not bad, f"{filename} must not import the engine under test, found: {bad}"
