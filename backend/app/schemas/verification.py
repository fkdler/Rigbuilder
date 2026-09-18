"""Public contracts for deterministic Recommendation truth verification."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.recommendation import Claim

VerificationStatus = Literal["supported", "conflict", "missing", "unverifiable"]


class PriceProvenance(BaseModel):
    snapshot_id: UUID
    source_id: UUID | None = None
    source_key: str | None = None
    source_url: str | None = None
    observed_at: datetime | None = None
    market_region: str | None = None
    condition: str | None = None
    price_type: str | None = None


class ClaimVerification(BaseModel):
    claim_index: int = Field(ge=0)
    claim: Claim
    status: VerificationStatus
    reason_code: str
    canonical_value: Any | None = None
    canonical_unit: str | None = None
    comparison_method: str | None = None
    valid_evidence_ids: list[UUID] = Field(default_factory=list)
    valid_price_snapshot_ids: list[UUID] = Field(default_factory=list)
    price_provenance: PriceProvenance | None = None
    valid_benchmark_run_ids: list[UUID] = Field(default_factory=list)
    valid_rule_refs: list[str] = Field(default_factory=list)


class CandidateVerification(BaseModel):
    candidate_id: UUID
    candidate_type: str
    canonical_name: str | None = None
    candidate_valid: bool
    candidate_reason_codes: list[str] = Field(default_factory=list)
    claims: list[ClaimVerification] = Field(default_factory=list)
    fact_support: float = Field(ge=0, le=1)
    proof_coverage: float = Field(ge=0, le=1)
    information_completeness: float = Field(ge=0, le=1)
    supported_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    unverifiable_count: int = Field(ge=0)


class RecommendationVerification(BaseModel):
    release_key: str
    candidates: list[CandidateVerification]


__all__ = [
    "VerificationStatus",
    "PriceProvenance",
    "ClaimVerification",
    "CandidateVerification",
    "RecommendationVerification",
]
