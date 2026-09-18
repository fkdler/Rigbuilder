"""Backend-only registry for versioned derived-claim evaluators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.schemas.recommendation import Claim
from app.verification.repository import TruthRepository


@dataclass(frozen=True)
class RuleEvaluation:
    value: Any | None
    unit: str | None = None
    missing_inputs: bool = False


RuleEvaluator = Callable[[Claim, TruthRepository], RuleEvaluation]


class RuleEvaluatorRegistry:
    def __init__(self) -> None:
        self._evaluators: dict[str, RuleEvaluator] = {}

    def register(self, implementation_ref: str, evaluator: RuleEvaluator) -> None:
        if not implementation_ref or implementation_ref in self._evaluators:
            raise ValueError("implementation_ref must be non-empty and unique")
        self._evaluators[implementation_ref] = evaluator

    def get(self, implementation_ref: str | None) -> RuleEvaluator | None:
        return None if implementation_ref is None else self._evaluators.get(implementation_ref)


rule_evaluators = RuleEvaluatorRegistry()

__all__ = ["RuleEvaluation", "RuleEvaluator", "RuleEvaluatorRegistry", "rule_evaluators"]
