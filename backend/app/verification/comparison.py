"""Versioned, deterministic type/unit/value comparison primitives."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

UNIT_POLICY_VERSION = "units-v1"

_UNIT_FACTORS: dict[str, tuple[str, Decimal]] = {
    "b": ("bytes", Decimal(1)),
    "kib": ("bytes", Decimal(1024)),
    "mib": ("bytes", Decimal(1024) ** 2),
    "gib": ("bytes", Decimal(1024) ** 3),
    "tib": ("bytes", Decimal(1024) ** 4),
    "w": ("power", Decimal(1)),
    "kw": ("power", Decimal(1000)),
    "hz": ("frequency", Decimal(1)),
    "khz": ("frequency", Decimal(1000)),
    "mhz": ("frequency", Decimal(1000) ** 2),
    "ghz": ("frequency", Decimal(1000) ** 3),
    "s": ("time", Decimal(1)),
    "ms": ("time", Decimal("0.001")),
    "us": ("time", Decimal("0.000001")),
}


class ComparisonUnavailable(ValueError):
    pass


def _unit_key(value: str) -> str:
    return value.strip().lower().replace(" ", "")


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ComparisonUnavailable("boolean_is_not_numeric")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ComparisonUnavailable("invalid_numeric_value") from exc


def normalize_value_type(value: Any, value_type: str) -> Any:
    if value_type == "number":
        return _decimal(value)
    if value_type == "integer":
        numeric = _decimal(value)
        if numeric != numeric.to_integral_value():
            raise ComparisonUnavailable("invalid_integer_value")
        return int(numeric)
    if value_type == "boolean":
        if not isinstance(value, bool):
            raise ComparisonUnavailable("invalid_boolean_value")
        return value
    if value_type == "string":
        if not isinstance(value, str):
            raise ComparisonUnavailable("invalid_string_value")
        return value
    if value_type == "date":
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError as exc:
                raise ComparisonUnavailable("invalid_date_value") from exc
        raise ComparisonUnavailable("invalid_date_value")
    if value_type in {"json", "object", "array"}:
        if value_type == "object" and not isinstance(value, dict):
            raise ComparisonUnavailable("invalid_object_value")
        if value_type == "array" and not isinstance(value, list):
            raise ComparisonUnavailable("invalid_array_value")
        if not isinstance(value, (dict, list)):
            raise ComparisonUnavailable("invalid_json_value")
        return value
    raise ComparisonUnavailable("unknown_value_type")


def convert_unit(value: Any, source_unit: str | None, target_unit: str | None) -> Any:
    if target_unit is None:
        if source_unit is not None:
            raise ComparisonUnavailable("unexpected_unit")
        return value
    if source_unit is None:
        source_unit = target_unit
    source = _unit_key(source_unit)
    target = _unit_key(target_unit)
    if source == target:
        return value
    source_definition = _UNIT_FACTORS.get(source)
    target_definition = _UNIT_FACTORS.get(target)
    if source_definition is None or target_definition is None or source_definition[0] != target_definition[0]:
        raise ComparisonUnavailable("unit_conversion_not_registered")
    return _decimal(value) * source_definition[1] / target_definition[1]


def values_match(
    claimed: Any,
    canonical: Any,
    *,
    value_type: str,
    method: str,
    tolerance: Any | None = None,
) -> bool:
    if method == "text_only":
        raise ComparisonUnavailable("text_only_field")

    if method == "set_contains":
        if not isinstance(canonical, (list, tuple, set)):
            raise ComparisonUnavailable("canonical_value_is_not_a_set")
        requested = claimed if isinstance(claimed, (list, tuple, set)) else [claimed]
        canonical_tokens = {_stable_token(value) for value in canonical}
        return all(_stable_token(value) in canonical_tokens for value in requested)

    if method == "range":
        if not isinstance(canonical, dict) or not {"min", "max"} <= set(canonical):
            raise ComparisonUnavailable("canonical_range_invalid")
        low, high = _decimal(canonical["min"]), _decimal(canonical["max"])
        if isinstance(claimed, dict):
            if not {"min", "max"} <= set(claimed):
                raise ComparisonUnavailable("claimed_range_invalid")
            return low == _decimal(claimed["min"]) and high == _decimal(claimed["max"])
        value = _decimal(claimed)
        return low <= value <= high

    left = normalize_value_type(claimed, value_type)
    right = normalize_value_type(canonical, value_type)
    if method == "tolerance":
        allowed = _decimal(tolerance) if tolerance is not None else Decimal(0)
        return abs(_decimal(left) - _decimal(right)) <= allowed
    if method != "exact":
        raise ComparisonUnavailable("comparison_method_not_supported")
    if value_type in {"json", "object", "array"}:
        return _stable_token(left) == _stable_token(right)
    return left == right


def _stable_token(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "UNIT_POLICY_VERSION", "ComparisonUnavailable", "normalize_value_type", "convert_unit", "values_match",
]
