"""V3 structured recommendation and auditable claim contract."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

CandidateType = Literal["hardware", "model_variant", "ai_model"]
EntityType = Literal["hardware", "ai_model", "model_variant", "workload"]
ValueType = Literal["number", "integer", "string", "boolean", "date", "object", "array"]
ClaimType = Literal["fact", "measurement", "derived", "preference", "price"]


def _json_type(value: Any) -> str:
    if isinstance(value, bool): return "boolean"
    if isinstance(value, int): return "integer"
    if isinstance(value, float): return "number"
    if isinstance(value, str): return "string"
    if isinstance(value, dict): return "object"
    if isinstance(value, list): return "array"
    return "unknown"


class Claim(BaseModel):
    claim_type: ClaimType
    entity_id: UUID
    field_key: str | None = Field(default=None, min_length=1, max_length=255)
    metric_key: str | None = Field(default=None, min_length=1, max_length=255)
    value: Any
    value_type: ValueType | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    qualifier_key: str | None = Field(default=None, min_length=1, max_length=120)
    statistic: str | None = Field(default=None, min_length=1, max_length=24)
    # V3.3 price-snapshot qualifiers: only valid on claim_type == "price".
    market_region: str | None = Field(default=None, min_length=1, max_length=32)
    condition: str | None = Field(default=None, min_length=1, max_length=24)
    price_type: str | None = Field(default=None, min_length=1, max_length=32)
    evidence_ids: list[UUID] = Field(default_factory=list)
    benchmark_run_ids: list[UUID] = Field(default_factory=list)
    rule_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_proof(self) -> "Claim":
        if self.value is None: raise ValueError("claim value must not be null")
        if self.value_type is not None:
            actual = _json_type(self.value)
            compatible = self.value_type == actual or (self.value_type == "number" and actual == "integer")
            if self.value_type == "date" and isinstance(self.value, str):
                compatible = True
            if not compatible: raise ValueError("value_type does not match value")
        if self.claim_type == "fact" and (not self.field_key or not self.evidence_ids): raise ValueError("fact requires field_key and evidence_ids")
        if self.claim_type == "measurement" and (not self.metric_key or not self.benchmark_run_ids): raise ValueError("measurement requires metric_key and benchmark_run_ids")
        if self.claim_type == "derived" and not self.rule_refs: raise ValueError("derived requires rule_refs")
        if self.claim_type == "price":
            if self.field_key or self.metric_key or self.statistic or self.benchmark_run_ids or self.rule_refs \
                    or self.qualifier_key or self.evidence_ids:
                raise ValueError("price claim cannot reuse fact/measurement/derived fields")
            if not self.unit or len(self.unit) != 3 or not self.unit.isalpha():
                raise ValueError("price claim requires an ISO 4217 currency code in unit")
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError("price claim value must be numeric")
            if self.value_type not in (None, "number", "integer"):
                raise ValueError("price claim value_type must be number or integer")
        else:
            if self.market_region or self.condition or self.price_type:
                raise ValueError("market_region/condition/price_type are only valid for price claims")
        if self.field_key and self.metric_key: raise ValueError("a claim cannot use both field_key and metric_key")
        if self.qualifier_key and not self.field_key: raise ValueError("qualifier_key requires field_key")
        if self.statistic and not self.metric_key: raise ValueError("statistic requires metric_key")
        return self


class RecommendationItem(BaseModel):
    candidate_id: UUID
    candidate_type: CandidateType
    name: str = Field(min_length=1, max_length=255)
    score: int = Field(ge=0, le=100)
    model_confidence: float | None = Field(default=None, ge=0, le=1)
    reasons: list[str] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list, max_length=6)

    @field_validator("score", mode="before")
    @classmethod
    def coerce_score(cls, value: Any) -> int:
        if isinstance(value, bool): raise ValueError("score must be a number")
        try: return int(float(value))
        except (TypeError, ValueError) as exc: raise ValueError("score must be a number") from exc


class Recommendation(BaseModel):
    schema_version: Literal["3.0"] = "3.0"
    recommendations: list[RecommendationItem] = Field(min_length=1)
    insufficient_information: list[str] = Field(default_factory=list)
    tool_summary: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_candidates(self) -> "Recommendation":
        keys = [(item.candidate_type, item.candidate_id) for item in self.recommendations]
        if len(keys) != len(set(keys)):
            raise ValueError("recommendations must not contain duplicate candidates")
        return self


EXAMPLE_RECOMMENDATION = {"schema_version": "3.0", "recommendations": [{"candidate_id": "36e7d6f4-9c3a-5a1c-a48a-6cbb31ee9c54",
    "candidate_type": "hardware", "name": "Example GPU", "score": 82, "model_confidence": 0.72,
    "reasons": ["VRAM satisfies the requirement"], "risks": ["No local benchmark"], "claims": [{
        "claim_type": "fact", "entity_id": "36e7d6f4-9c3a-5a1c-a48a-6cbb31ee9c54", "field_key": "gpu.vram_gib", "value": 12,
        "value_type": "number", "evidence_ids": ["882a4ccb-2c5c-5fd7-8f38-c11e603f977e"], "benchmark_run_ids": [], "rule_refs": []}]}],
    "insufficient_information": ["No matching benchmark"], "tool_summary": {"sql_calls": 1, "tables_queried": ["gpu_catalog"]}}


def format_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    return [{"loc": list(e.get("loc", [])), "type": e.get("type", "unknown"), "message": e.get("msg", "")} for e in exc.errors()]


__all__ = ["CandidateType", "EntityType", "ValueType", "ClaimType", "Claim", "RecommendationItem", "Recommendation", "EXAMPLE_RECOMMENDATION", "format_validation_errors"]
