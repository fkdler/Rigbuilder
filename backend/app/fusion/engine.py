"""Pure, deterministic fusion-v1 implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.fusion.constraints import ConstraintEvaluator
from app.schemas.fusion import (
    ConfirmedConstraint,
    FusedCandidate,
    FusedClaim,
    FusionResult,
    FusionTrace,
)
from app.schemas.recommendation import Recommendation, RecommendationItem
from app.schemas.verification import CandidateVerification, ClaimVerification, RecommendationVerification
from app.verification.comparison import UNIT_POLICY_VERSION
from app.verification.repository import TruthRepository

FUSION_POLICY_VERSION = "fusion-v1"


class FusionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AgentFusionInput:
    model_id: str
    recommendation: Recommendation
    verification: RecommendationVerification
    agent_run_id: UUID | None = None


@dataclass
class _Occurrence:
    model_id: str
    rank: int
    item: RecommendationItem
    verification: CandidateVerification


class FusionEngine:
    def __init__(self, repository: TruthRepository):
        self.repository = repository
        self.constraints = ConstraintEvaluator(repository)

    def fuse(
        self,
        inputs: list[AgentFusionInput],
        constraints: list[ConfirmedConstraint],
        *,
        top_k: int,
        configured_agent_count: int,
        failed_models: list[str] | None = None,
    ) -> FusionResult:
        if not inputs:
            raise FusionError("no_valid_agent_results", "At least one verified Agent result is required.")
        if not 1 <= top_k <= 10:
            raise FusionError("invalid_top_k", "top_k must be between 1 and 10.")
        input_models = [item.model_id for item in inputs]
        if len(set(input_models)) != len(input_models):
            raise FusionError("duplicate_agent_model", "Each model may contribute at most one Agent result.")
        if configured_agent_count < len(inputs) or configured_agent_count < 1:
            raise FusionError("invalid_agent_count", "Configured Agent count cannot be smaller than valid inputs.")
        release_keys = {item.verification.release_key for item in inputs}
        if len(release_keys) != 1:
            raise FusionError("release_mismatch", "All Agent results must use the same Truth DB release.")
        release_key = next(iter(release_keys))

        grouped: dict[tuple[str, UUID], list[_Occurrence]] = {}
        for agent in inputs:
            verification_by_key = {
                (candidate.candidate_type, candidate.candidate_id): candidate
                for candidate in agent.verification.candidates
            }
            seen: set[tuple[str, UUID]] = set()
            for rank, item in enumerate(agent.recommendation.recommendations, start=1):
                key = (item.candidate_type, item.candidate_id)
                if key in seen:
                    continue
                seen.add(key)
                verification = verification_by_key.get(key)
                if verification is None:
                    raise FusionError("verification_alignment_error", "Recommendation candidate has no verification result.")
                grouped.setdefault(key, []).append(_Occurrence(agent.model_id, rank, item, verification))

        valid_models = sorted({item.model_id for item in inputs})
        failed = sorted(set(failed_models or []) - set(valid_models))
        agent_coverage = len(valid_models) / configured_agent_count
        fused = [
            self._candidate(key, occurrences, constraints, len(valid_models), agent_coverage)
            for key, occurrences in grouped.items()
        ]
        active = [candidate for candidate in fused if not candidate.elimination_reasons]
        eliminated = [candidate for candidate in fused if candidate.elimination_reasons]
        active.sort(key=_sort_key)
        eliminated.sort(key=_sort_key)
        trace = FusionTrace(
            policy_version=FUSION_POLICY_VERSION,
            unit_policy_version=UNIT_POLICY_VERSION,
            release_key=release_key,
            participating_models=valid_models,
            failed_models=failed,
            configured_agent_count=configured_agent_count,
            agent_coverage=agent_coverage,
            formula={
                "recommendation_with_preferences": "100*(0.70*preference_utility+0.30*consensus)",
                "recommendation_without_preferences": "100*consensus",
                "credibility": "100*(0.40*fact_support+0.25*proof_coverage+0.20*information_completeness+0.15*consensus*agent_coverage)",
                "constraint_gate": "all hard constraints must be satisfied",
                "model_weights": "equal",
            },
            input_summary={
                "valid_agent_results": len(inputs),
                "failed_agent_results": len(failed),
                "unique_candidates": len(grouped),
                "constraint_count": len(constraints),
                "constraint_inputs": [constraint.model_dump(mode="json") for constraint in constraints],
                "agent_rankings": {
                    agent.model_id: [
                        {"candidate_type": item.candidate_type, "candidate_id": str(item.candidate_id), "rank": rank}
                        for rank, item in enumerate(agent.recommendation.recommendations, start=1)
                    ]
                    for agent in inputs
                },
                "requested_top_k": top_k,
            },
            tie_break_order=[
                "recommendation_score_desc", "credibility_desc", "consensus_desc",
                "candidate_type_asc", "candidate_id_asc",
            ],
        )
        return FusionResult(
            release_key=release_key,
            policy_version=FUSION_POLICY_VERSION,
            top_k=active[:top_k],
            eliminated=eliminated,
            trace=trace,
        )

    def _candidate(
        self,
        key: tuple[str, UUID],
        occurrences: list[_Occurrence],
        constraints: list[ConfirmedConstraint],
        valid_agent_count: int,
        agent_coverage: float,
    ) -> FusedCandidate:
        candidate_type, candidate_id = key
        source_models = sorted({item.model_id for item in occurrences})
        source_ranks = {item.model_id: item.rank for item in occurrences}
        consensus = sum(1 / item.rank for item in occurrences) / valid_agent_count
        constraint_results = [self.constraints.evaluate(candidate_id, constraint) for constraint in constraints]
        preference_results = [result for result in constraint_results if result.kind == "preference"]
        if preference_results:
            denominator = sum(result.weight for result in preference_results)
            preference_utility = sum(
                result.weight for result in preference_results if result.status == "satisfied"
            ) / denominator
            recommendation_score = 100 * (0.70 * preference_utility + 0.30 * consensus)
        else:
            preference_utility = 0.5
            recommendation_score = 100 * consensus

        claims = _merge_claims(occurrences)
        factual = [item.verification for item in claims if item.claim.claim_type != "preference"]
        total = len(factual)
        supported = sum(item.status == "supported" for item in factual)
        proven = sum(
            item.status == "supported" and bool(
                item.valid_evidence_ids or item.valid_price_snapshot_ids
                or item.valid_benchmark_run_ids or item.valid_rule_refs
            )
            for item in factual
        )
        complete = sum(item.status in {"supported", "conflict"} for item in factual)
        fact_support = supported / total if total else 0
        proof_coverage = proven / total if total else 0
        information_completeness = complete / total if total else 0
        credibility = 100 * (
            0.40 * fact_support
            + 0.25 * proof_coverage
            + 0.20 * information_completeness
            + 0.15 * consensus * agent_coverage
        )

        reasons: list[str] = []
        for occurrence in occurrences:
            reasons.extend(occurrence.verification.candidate_reason_codes)
        hard_results = [result for result in constraint_results if result.kind == "hard"]
        constraint_validity = (
            sum(result.status == "satisfied" for result in hard_results) / len(hard_results)
            if hard_results else 1
        )
        for result in hard_results:
            if result.status != "satisfied":
                reasons.append(f"hard_constraint_{result.status}:{result.constraint_id}")
        critical_fields = {
            constraint.field_key
            for constraint in constraints
            if constraint.kind == "hard" and getattr(constraint, "target", None) == "field"
        }
        for claim in claims:
            if claim.claim.field_key in critical_fields and claim.verification.status == "conflict":
                reasons.append(f"critical_claim_conflict:{claim.claim.field_key}")

        canonical_name = next(
            (item.verification.canonical_name for item in occurrences if item.verification.canonical_name),
            occurrences[0].item.name,
        )
        return FusedCandidate(
            candidate_id=candidate_id,
            candidate_type=candidate_type,
            canonical_name=canonical_name,
            recommendation_score=round(_clamp(recommendation_score), 2),
            credibility=round(_clamp(credibility), 2),
            fact_support=fact_support,
            proof_coverage=proof_coverage,
            information_completeness=information_completeness,
            constraint_validity=constraint_validity,
            preference_utility=preference_utility,
            consensus=consensus,
            source_models=source_models,
            source_ranks=source_ranks,
            agent_scores={item.model_id: item.item.score for item in occurrences},
            model_confidences={item.model_id: item.item.model_confidence for item in occurrences},
            constraints=constraint_results,
            claims=claims,
            elimination_reasons=sorted(set(reasons)),
        )


def _claim_key(verification: ClaimVerification) -> str:
    claim = verification.claim
    payload = {
        "claim_type": claim.claim_type,
        "entity_id": str(claim.entity_id),
        "field_key": claim.field_key,
        "metric_key": claim.metric_key,
        "value": claim.value,
        "value_type": claim.value_type,
        "unit": claim.unit,
        "qualifier_key": claim.qualifier_key,
        "statistic": claim.statistic,
        "market_region": claim.market_region,
        "condition": claim.condition,
        "price_type": claim.price_type,
        "rule_refs": sorted(claim.rule_refs),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _merge_claims(occurrences: list[_Occurrence]) -> list[FusedClaim]:
    grouped: dict[str, list[tuple[str, ClaimVerification]]] = {}
    for occurrence in occurrences:
        for verification in occurrence.verification.claims:
            grouped.setdefault(_claim_key(verification), []).append((occurrence.model_id, verification))
    priority = {"supported": 3, "conflict": 2, "missing": 1, "unverifiable": 0}
    merged: list[FusedClaim] = []
    for key in sorted(grouped):
        values = grouped[key]
        selected = max((value for _, value in values), key=lambda value: priority[value.status])
        merged.append(FusedClaim(
            claim=selected.claim,
            verification=selected,
            source_models=sorted({model for model, _ in values}),
        ))
    return merged


def _clamp(value: float) -> float:
    return max(0, min(100, value))


def _sort_key(candidate: FusedCandidate) -> tuple[Any, ...]:
    return (
        -candidate.recommendation_score,
        -candidate.credibility,
        -candidate.consensus,
        candidate.candidate_type,
        str(candidate.candidate_id),
    )


__all__ = ["FUSION_POLICY_VERSION", "FusionError", "AgentFusionInput", "FusionEngine"]
