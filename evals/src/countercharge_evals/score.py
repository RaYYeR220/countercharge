"""Scorecard math shared by ``run_engine.py`` (and, later, ``run_live.py``).

Compares one engine :class:`~countercharge_engine.models.AuditReport`
against one pre-registered answer entry from ``key/answers.json``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CaseResult:
    case_id: str
    category: str
    expected_rules: list[str]
    actual_rules: list[str]
    expected_disputable_cents: int
    actual_disputable_cents: int
    expected_fap_tier: str | None
    actual_fap_tier: str | None

    @property
    def rules_match(self) -> bool:
        return set(self.expected_rules) == set(self.actual_rules)

    @property
    def amount_match(self) -> bool:
        return self.expected_disputable_cents == self.actual_disputable_cents

    @property
    def fap_match(self) -> bool:
        return self.expected_fap_tier == self.actual_fap_tier

    @property
    def false_positive_rules(self) -> list[str]:
        return sorted(set(self.actual_rules) - set(self.expected_rules))

    @property
    def false_negative_rules(self) -> list[str]:
        return sorted(set(self.expected_rules) - set(self.actual_rules))


@dataclass
class Scorecard:
    cases: list[CaseResult] = field(default_factory=list)

    def add(self, result: CaseResult) -> None:
        self.cases.append(result)

    # -- per-rule precision/recall --------------------------------------
    def per_rule(self) -> dict[str, dict[str, float | int]]:
        rule_ids = sorted({r for c in self.cases for r in (c.expected_rules + c.actual_rules)})
        out: dict[str, dict[str, float | int]] = {}
        for rule in rule_ids:
            tp = sum(1 for c in self.cases if rule in c.expected_rules and rule in c.actual_rules)
            fp = sum(1 for c in self.cases if rule not in c.expected_rules and rule in c.actual_rules)
            fn = sum(1 for c in self.cases if rule in c.expected_rules and rule not in c.actual_rules)
            precision = tp / (tp + fp) if (tp + fp) else None
            recall = tp / (tp + fn) if (tp + fn) else None
            out[rule] = {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall}
        return out

    def exact_amount_match_rate(self) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for c in self.cases if c.amount_match) / len(self.cases)

    def negative_control_false_positives(self) -> list[str]:
        """Case ids seeded with zero expected rules where the engine found
        at least one anyway (the ``CLEAN`` and clean ``ADVERSARIAL`` cases)."""
        return [
            c.case_id
            for c in self.cases
            if c.category in ("CLEAN", "ADVERSARIAL") and not c.expected_rules and c.actual_rules
        ]

    def fap_tier_accuracy(self) -> float:
        relevant = [c for c in self.cases if c.expected_fap_tier is not None or c.actual_fap_tier is not None]
        if not relevant:
            return 1.0
        return sum(1 for c in relevant if c.fap_match) / len(relevant)

    def mismatches(self) -> list[CaseResult]:
        return [c for c in self.cases if not (c.rules_match and c.amount_match and c.fap_match)]

    def to_dict(self) -> dict:
        neg_fps = self.negative_control_false_positives()
        return {
            "total_cases": len(self.cases),
            "rules_exact_match_cases": sum(1 for c in self.cases if c.rules_match),
            "exact_amount_match_rate": self.exact_amount_match_rate(),
            "fap_tier_accuracy": self.fap_tier_accuracy(),
            "negative_control_false_positives": neg_fps,
            "negative_control_false_positive_count": len(neg_fps),
            "per_rule": self.per_rule(),
            "mismatches": [
                {
                    "case_id": c.case_id,
                    "category": c.category,
                    "expected_rules": c.expected_rules,
                    "actual_rules": c.actual_rules,
                    "false_positive_rules": c.false_positive_rules,
                    "false_negative_rules": c.false_negative_rules,
                    "expected_disputable_cents": c.expected_disputable_cents,
                    "actual_disputable_cents": c.actual_disputable_cents,
                    "expected_fap_tier": c.expected_fap_tier,
                    "actual_fap_tier": c.actual_fap_tier,
                }
                for c in self.mismatches()
            ],
        }
