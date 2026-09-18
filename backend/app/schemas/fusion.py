"""Contracts for confirmed constraints and deterministic multi-Agent fusion."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.recommendation import Claim, Recommendation
from app.schemas.verification import ClaimVerification, RecommendationVerification

ConstraintKind = Literal["hard", "preference"]
ConstraintStatus = Literal["satisfied", "violated", "unknown"]
ScalarOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains"]


class _ConstraintBase(BaseModel):
    constraint_id: str = Field(min_length=1, max_length=120)
    kind: ConstraintKind
    confirmed: Literal[True] = True
    weight: float = Field(default=1, gt=0, le=1)


class FieldConstraint(_ConstraintBase):
    target: Literal["field"] = "field"
    field_key: str = Field(min_length=1, max_length=255)
    operator: ScalarOperator
    value: Any
    unit: str | None = Field(default=None, max_length=64)
    qualifier_key: str = Field(default="default", min_length=1, max_length=120)

    @model_validator(mode="after")
    def non_null_value(self) -> "FieldConstraint":
        if self.value is None:
            raise ValueError("field constraint value must not be null")
        return self


class PriceConstraint(_ConstraintBase):
    target: Literal["price"] = "price"
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: float = Field(ge=0)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    region: str | None = Field(default="CN", max_length=32)
    condition: str | None = Field(default="new", max_length=24)
    price_type: str | None = Field(default="current_new", max_length=32)


class CompatibilityConstraint(_ConstraintBase):
    target: Literal["compatibility"] = "compatibility"
    other_entity_id: UUID
    relation_key: str = Field(min_length=1, max_length=80)
    direction: Literal["outgoing", "incoming"] = "outgoing"
    required_status: str = Field(default="compatible", min_length=1, max_length=32)


class RuntimeConstraint(_ConstraintBase):
    target: Literal["runtime"] = "runtime"
    runtime_key: str = Field(min_length=1, max_length=120)
    required_status: str = Field(default="supported", min_length=1, max_length=24)


ConfirmedConstraint = Annotated[
    FieldConstraint | PriceConstraint | CompatibilityConstraint | RuntimeConstraint,
    Field(discriminator="target"),
]


class ConstraintVerification(BaseModel):
    constraint_id: str
    kind: ConstraintKind
    target: str
    status: ConstraintStatus
    reason_code: str
    actual_value: Any | None = None
    expected_value: Any | None = None
    weight: float


class FusedClaim(BaseModel):
    claim: Claim
    verification: ClaimVerification
    source_models: list[str]


class FusedCandidate(BaseModel):
    candidate_id: UUID
    candidate_type: str
    canonical_name: str
    recommendation_score: float = Field(ge=0, le=100)
    credibility: float = Field(ge=0, le=100)
    fact_support: float = Field(ge=0, le=1)
    proof_coverage: float = Field(ge=0, le=1)
    information_completeness: float = Field(ge=0, le=1)
    constraint_validity: float = Field(ge=0, le=1)
    preference_utility: float = Field(ge=0, le=1)
    consensus: float = Field(ge=0, le=1)
    source_models: list[str]
    source_ranks: dict[str, int]
    agent_scores: dict[str, int]
    model_confidences: dict[str, float | None]
    constraints: list[ConstraintVerification]
    claims: list[FusedClaim]
    elimination_reasons: list[str] = Field(default_factory=list)


class FusionTrace(BaseModel):
    policy_version: Literal["fusion-v1"] = "fusion-v1"
    unit_policy_version: Literal["units-v1"] = "units-v1"
    release_key: str
    participating_models: list[str]
    failed_models: list[str]
    configured_agent_count: int = Field(ge=1)
    agent_coverage: float = Field(ge=0, le=1)
    formula: dict[str, Any]
    input_summary: dict[str, Any]
    tie_break_order: list[str]


class FusionResult(BaseModel):
    release_key: str
    policy_version: Literal["fusion-v1"] = "fusion-v1"
    top_k: list[FusedCandidate]
    eliminated: list[FusedCandidate]
    trace: FusionTrace


class FusionAgentResult(BaseModel):
    agent_run_id: UUID
    model_id: str
    response_model: str | None = None
    status: Literal["completed", "failed"]
    recommendation: Recommendation | None = None
    verification: RecommendationVerification | None = None
    error_code: str | None = None
    error: str | None = None
    # Raw model text for local debugging/audit. Clients may hide this
    # by default; it is never used as verified evidence.
    raw_output: str | None = Field(default=None, max_length=50_000)


class NarratorCandidateText(BaseModel):
    """One candidate's natural-language explanation produced by the Narrator."""

    candidate_id: UUID
    explanation: str = Field(min_length=1, max_length=2_000)


class PrimaryRecommendation(BaseModel):
    """The one candidate the narrator commits to, with its reasons.

    ``candidate_id`` must belong to the returned ``top_k`` and ``name`` must equal
    that candidate's ``canonical_name``; both are checked by the narrator before a
    model-authored presentation is accepted.
    """

    candidate_id: UUID
    name: str = Field(min_length=1, max_length=300)
    reasons: list[str] = Field(default_factory=list, max_length=4)


class SelectedRecommendation(PrimaryRecommendation):
    label: str = ''


class AlternativeNote(BaseModel):
    """A runner-up and how it differs from the primary."""

    candidate_id: UUID
    name: str = Field(min_length=1, max_length=300)
    note: str = Field(min_length=1, max_length=1_000)


SupportBasis = Literal["database_requirement", "database_derived", "not_in_catalogue", "general_advice"]


class CoreComponent(BaseModel):
    """One core component of a build: a concrete, verified catalogue row."""

    role: Literal["cpu", "gpu"]
    candidate_id: UUID
    name: str = Field(min_length=1, max_length=300)
    reasons: list[str] = Field(default_factory=list, max_length=4)


class SupportingSpec(BaseModel):
    """A supporting part, stated as the specification tier the Catalogue holds.

    ``basis`` is what keeps this honest and is never optional:

    * ``database_requirement`` - the Release states this requirement for the workload;
    * ``database_derived`` - the backend computed it from the two core components
      (the CPU socket, the combined board power);
    * ``not_in_catalogue`` - the Catalogue carries nothing that decides it.

    None of the three is a verified fact, and a client renders them differently.
    """

    role: Literal["platform", "memory", "storage", "psu", "display", "cooler", "case"]
    spec: str = Field(min_length=1, max_length=200)
    basis: SupportBasis
    note: str | None = Field(default=None, max_length=300)


class CoreBuild(BaseModel):
    """The core-build view of a fused result (Plan_V4.2).

    A whole-machine request is answered as two concrete core components plus supporting
    parts described only as specifications, because this Catalogue holds no assembled
    machine entity and no compatibility relation.
    """

    core: list[CoreComponent] = Field(default_factory=list)
    supporting: list[SupportingSpec] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    required_core_roles: list[Literal["cpu", "gpu"]] = Field(default_factory=lambda: ["cpu", "gpu"])
    status: Literal["draft", "partial"] = "draft"


class PresentationResult(BaseModel):
    """The conclusion-shaped view of a fused result (Plan_V4.1).

    The narrator may rephrase language but must never change candidates, ranking,
    scores, facts, risks or evidence status.  ``headline`` is the single sentence
    a user should be able to read on its own.
    """

    headline: str = Field(min_length=1, max_length=1_000)
    primary: PrimaryRecommendation | None = None
    selections: list[SelectedRecommendation] = Field(default_factory=list)
    selection_notice: str | None = None
    # Short, user-facing facts copied only from claims that passed Truth DB
    # verification.  Keeping this separate from prose lets the client show a
    # compact answer without exposing Agent identities, scores or execution trace.
    evidence: list[str] = Field(default_factory=list, max_length=8)
    sources: list[dict[str, str]] = Field(default_factory=list)
    alternatives: list[AlternativeNote] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    per_candidate: list[NarratorCandidateText] = Field(default_factory=list)
    # Additive (Plan_V4.2).  Declared on the parent so NaturalLanguage, which is a
    # superset of this model, carries it too and both client entry points see it.
    core_build: CoreBuild | None = None


class NaturalLanguage(PresentationResult):
    """Human-readable narration of a fused result (additive, never None for ok).

    A superset of :class:`PresentationResult`: ``overview`` and ``per_candidate``
    keep their exact original meaning, because callers and tests already depend on
    them, while the conclusion fields are additive.  ``headline`` is relaxed to
    optional here so a pre-V4.1 payload (overview + per_candidate only) still
    validates; the production path always fills it.
    """

    headline: str | None = Field(default=None, max_length=1_000)
    overview: str = Field(default="", max_length=3_000)
    per_candidate: list[NarratorCandidateText] = Field(default_factory=list)


class FusionRunResponse(BaseModel):
    answer_purpose: Literal["recommendation", "explanation"] = "recommendation"
    request_id: UUID
    conversation_id: UUID
    status: Literal["completed", "failed"]
    release_key: str | None = None
    policy_version: Literal["fusion-v1"] = "fusion-v1"
    agents: list[FusionAgentResult]
    result: FusionResult | None = None
    natural_language: NaturalLanguage | None = None
    # The conclusion fields of `natural_language`, exposed on their own so a
    # client can render the answer without depending on the compatibility field.
    presentation: PresentationResult | None = None
    error: str | None = None


__all__ = [
    "ConfirmedConstraint", "FieldConstraint", "PriceConstraint", "CompatibilityConstraint", "RuntimeConstraint",
    "ConstraintVerification", "FusedClaim", "FusedCandidate", "FusionTrace", "FusionResult",
    "FusionAgentResult", "FusionRunResponse",
    "NarratorCandidateText", "NaturalLanguage",
    "PresentationResult", "PrimaryRecommendation", "AlternativeNote",
    "CoreBuild", "CoreComponent", "SupportingSpec", "SupportBasis",
]
