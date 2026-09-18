"""Deterministic evaluation of already-confirmed user constraints."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.schemas.fusion import (
    CompatibilityConstraint,
    ConfirmedConstraint,
    ConstraintVerification,
    FieldConstraint,
    PriceConstraint,
    RuntimeConstraint,
)
from app.verification.comparison import ComparisonUnavailable, convert_unit, values_match
from app.verification.repository import TruthRepository


class ConstraintEvaluator:
    def __init__(self, repository: TruthRepository):
        self.repository = repository

    def evaluate(self, entity_id: UUID, constraint: ConfirmedConstraint) -> ConstraintVerification:
        if isinstance(constraint, FieldConstraint):
            return self._field(entity_id, constraint)
        if isinstance(constraint, PriceConstraint):
            return self._price(entity_id, constraint)
        if isinstance(constraint, CompatibilityConstraint):
            return self._compatibility(entity_id, constraint)
        if isinstance(constraint, RuntimeConstraint):
            return self._runtime(entity_id, constraint)
        return self._result(constraint, "unknown", "constraint_type_not_supported")

    def _field(self, entity_id: UUID, constraint: FieldConstraint) -> ConstraintVerification:
        definition = self.repository.field(constraint.field_key)
        if definition is None or not definition.active or not definition.filterable:
            return self._result(constraint, "unknown", "field_not_filterable")
        if definition.comparison_method == "text_only":
            return self._result(constraint, "unknown", "field_text_only")
        try:
            stored = self.repository.field_value(entity_id, definition, constraint.qualifier_key)
        except ValueError:
            return self._result(constraint, "unknown", "field_storage_unsupported")
        if not stored.found or stored.value is None:
            return self._result(constraint, "unknown", "field_value_missing")
        target_unit = stored.unit or definition.canonical_unit
        try:
            expected = convert_unit(constraint.value, constraint.unit, target_unit)
            matched = _apply_operator(
                stored.value, expected, constraint.operator,
                value_type=definition.value_type,
                comparison_method=definition.comparison_method,
                tolerance=definition.tolerance,
            )
        except ComparisonUnavailable as exc:
            return self._result(constraint, "unknown", str(exc), actual=stored.value)
        return self._result(
            constraint, "satisfied" if matched else "violated", "constraint_compared",
            actual=stored.value, expected=expected,
        )

    def _price(self, entity_id: UUID, constraint: PriceConstraint) -> ConstraintVerification:
        row = self.repository.latest_price(
            entity_id, constraint.currency, constraint.region, constraint.condition, constraint.price_type
        )
        if not row.found:
            return self._result(constraint, "unknown", "price_missing")
        try:
            matched = _numeric_operator(row.amount, constraint.value, constraint.operator)
        except ComparisonUnavailable as exc:
            return self._result(constraint, "unknown", str(exc), actual=row.amount)
        return self._result(
            constraint, "satisfied" if matched else "violated", "price_compared",
            actual=row.amount, expected=constraint.value,
        )

    def _compatibility(self, entity_id: UUID, constraint: CompatibilityConstraint) -> ConstraintVerification:
        row = self.repository.compatibility(
            entity_id, constraint.other_entity_id, constraint.relation_key, constraint.direction
        )
        if not row.found:
            return self._result(constraint, "unknown", "compatibility_missing")
        matched = row.status == constraint.required_status
        return self._result(
            constraint, "satisfied" if matched else "violated", "compatibility_compared",
            actual=row.status, expected=constraint.required_status,
        )

    def _runtime(self, entity_id: UUID, constraint: RuntimeConstraint) -> ConstraintVerification:
        row = self.repository.runtime_support(entity_id, constraint.runtime_key)
        if not row.found:
            return self._result(constraint, "unknown", "runtime_support_missing")
        matched = row.status == constraint.required_status
        return self._result(
            constraint, "satisfied" if matched else "violated", "runtime_support_compared",
            actual=row.status, expected=constraint.required_status,
        )

    @staticmethod
    def _result(
        constraint: ConfirmedConstraint,
        status: str,
        reason: str,
        *,
        actual: Any | None = None,
        expected: Any | None = None,
    ) -> ConstraintVerification:
        if expected is None:
            expected = getattr(constraint, "value", None)
        return ConstraintVerification(
            constraint_id=constraint.constraint_id,
            kind=constraint.kind,
            target=constraint.target,
            status=status,
            reason_code=reason,
            actual_value=actual,
            expected_value=expected,
            weight=constraint.weight,
        )


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ComparisonUnavailable("boolean_is_not_numeric")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ComparisonUnavailable("invalid_numeric_value") from exc


def _numeric_operator(actual: Any, expected: Any, operator: str) -> bool:
    left, right = _decimal(actual), _decimal(expected)
    return {
        "eq": left == right,
        "ne": left != right,
        "gt": left > right,
        "gte": left >= right,
        "lt": left < right,
        "lte": left <= right,
    }[operator]


def _apply_operator(
    actual: Any,
    expected: Any,
    operator: str,
    *,
    value_type: str,
    comparison_method: str,
    tolerance: Any | None,
) -> bool:
    if operator in {"gt", "gte", "lt", "lte"}:
        return _numeric_operator(actual, expected, operator)
    if operator == "in":
        if not isinstance(expected, (list, tuple, set)):
            raise ComparisonUnavailable("constraint_expected_collection")
        return any(values_match(actual, value, value_type=value_type, method="exact") for value in expected)
    if operator == "contains":
        return values_match(expected, actual, value_type="json", method="set_contains")
    matched = values_match(
        actual, expected, value_type=value_type,
        method=comparison_method if comparison_method in {"exact", "tolerance"} else "exact",
        tolerance=tolerance,
    )
    return not matched if operator == "ne" else matched


__all__ = ["ConstraintEvaluator"]
