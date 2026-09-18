"""Model-facing decision metadata contract; unknown is represented explicitly."""
from typing import Any, Literal
from pydantic import BaseModel, Field


class EvidenceFact(BaseModel):
    value: Any
    unit: str | None
    evidence_ids: list[str]
    observed_at: str | None
    confidence: float = Field(ge=0, le=1)


class PositionDimension(BaseModel):
    rank: int = Field(ge=1)
    percentile: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=2)
    direction: Literal["higher_value"]
    calculation_id: str


class PerformanceRanking(BaseModel):
    """A policy-anchored index, kept separate from facts and measurements.

    The raw score and publisher-rank-derived percentiles are third-party
    benchmark metadata.
    ``index_100`` and ``tier_label`` are this project's versioned product policy:
    hand-selected anchors with linear interpolation.  They are neither a publisher
    percentile nor a measurement this project performed.  A claim resting on them
    is ``derived``, never a ``fact`` claim.
    """

    basis: Literal["policy_anchored_index"]
    policy_version: Literal["performance-anchor-v1"]
    source_key: str | None
    score: float
    index_100: float = Field(ge=0, le=100)
    tier_label: Literal["flagship", "high", "mid", "entry", "bottom"]
    population_size: float | None = Field(default=None, ge=1)
    cohort_size: int = Field(ge=1)
    market_percentile: float | None = Field(default=None, ge=0, le=100)
    class_percentile: float | None = Field(default=None, ge=0, le=100)
    meaning: str


class SpecificationPosition(BaseModel):
    basis: Literal["specification_position"]
    cohort: list[str | None]
    generation_basis: Literal["architecture_proxy"]
    cohort_size: int = Field(ge=0)
    dimensions: dict[str, PositionDimension]
    label: str
    omitted_reason: str | None = None


class FactCoverage(BaseModel):
    meaning: str
    coverage: float = Field(ge=0, le=1)


class OfficeFit(BaseModel):
    policy_version: Literal["office-fit-v1"]
    score: float | None = Field(default=None, ge=0, le=100)
    meaning: str


class DecisionCandidate(BaseModel):
    entity_id: str
    category: Literal["cpu", "gpu"]
    facts: dict[str, EvidenceFact]
    facts_reference: Literal["row_bindings"] | None = None
    position: SpecificationPosition
    performance: PerformanceRanking | None = None
    office_fit: OfficeFit
    missing_fields: list[str]
    confidence: FactCoverage


class CalculationProof(BaseModel):
    field_key: str
    input_evidence_ids: list[str]


class DecisionContext(BaseModel):
    policy_version: Literal["catalogue-position-v2"]
    release_key: str | None
    snapshot_applied_at: str | None = None
    data_cutoff: str | None
    candidates: list[DecisionCandidate]
    calculations: dict[str, CalculationProof] = Field(default_factory=dict)
    omitted_reason: str | None = None
