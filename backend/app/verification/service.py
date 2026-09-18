"""Deterministic Truth Verification for Recommendation 3.0 Claims."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.schemas.recommendation import Claim, Recommendation
from app.schemas.verification import CandidateVerification, ClaimVerification, RecommendationVerification
from app.verification.comparison import ComparisonUnavailable, convert_unit, values_match
from app.verification.repository import FieldRecord, SqlAlchemyTruthRepository, StoredValue, TruthRepository
from app.verification.rules import RuleEvaluatorRegistry, rule_evaluators


class TruthVerificationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _Comparison:
    matches: bool
    claimed_value: Any
    canonical_unit: str | None


class TruthVerifier:
    def __init__(self, repository: TruthRepository, rules: RuleEvaluatorRegistry | None = None):
        self.repository = repository
        self.rules = rules or rule_evaluators

    @classmethod
    def from_session(cls, session: Any) -> "TruthVerifier":
        return cls(SqlAlchemyTruthRepository(session))

    def verify(self, recommendation: Recommendation) -> RecommendationVerification:
        release_key = self.repository.latest_release_key()
        if release_key is None:
            raise TruthVerificationError("truth_release_unavailable", "No accepted Truth DB release is available.")
        candidates = [self._verify_candidate(item) for item in recommendation.recommendations]
        return RecommendationVerification(release_key=release_key, candidates=candidates)

    def _verify_candidate(self, item: Any) -> CandidateVerification:
        entity = self.repository.candidate(item.candidate_id)
        reason_codes: list[str] = []
        if entity is None:
            reason_codes.append("candidate_not_found")
        else:
            if entity.entity_type != item.candidate_type:
                reason_codes.append("candidate_type_mismatch")
            if not entity.recommendable:
                reason_codes.append("candidate_not_recommendable")

        claims = [self._verify_claim(index, claim) for index, claim in enumerate(item.claims)]
        factual = [result for result in claims if result.claim.claim_type != "preference"]
        supported = sum(result.status == "supported" for result in factual)
        conflict = sum(result.status == "conflict" for result in factual)
        missing = sum(result.status == "missing" for result in factual)
        unverifiable = sum(result.status == "unverifiable" for result in factual)
        total = len(factual)
        proven = sum(
            result.status == "supported" and bool(
                result.valid_evidence_ids or result.valid_price_snapshot_ids
                or result.valid_benchmark_run_ids or result.valid_rule_refs
            )
            for result in factual
        )
        complete = supported + conflict
        if supported == 0:
            reason_codes.append("no_supported_truth_claim")
        return CandidateVerification(
            candidate_id=item.candidate_id,
            candidate_type=item.candidate_type,
            canonical_name=entity.canonical_name if entity else None,
            candidate_valid=not reason_codes,
            candidate_reason_codes=reason_codes,
            claims=claims,
            fact_support=supported / total if total else 0,
            proof_coverage=proven / total if total else 0,
            information_completeness=complete / total if total else 0,
            supported_count=supported,
            conflict_count=conflict,
            missing_count=missing,
            unverifiable_count=unverifiable,
        )

    def _verify_claim(self, index: int, claim: Claim) -> ClaimVerification:
        if claim.claim_type == "fact":
            return self._verify_fact(index, claim)
        if claim.claim_type == "measurement":
            return self._verify_measurement(index, claim)
        if claim.claim_type == "derived":
            return self._verify_derived(index, claim)
        if claim.claim_type == "price":
            return self._verify_price(index, claim)
        return self._result(index, claim, "unverifiable", "preference_not_truth_claim")

    def _verify_fact(self, index: int, claim: Claim) -> ClaimVerification:
        field = self.repository.field(claim.field_key or "")
        if field is None and claim.field_key and claim.evidence_ids:
            # Small local models sometimes copy the SQL column name (``vram_gib``)
            # instead of the namespaced field key returned beside its evidence
            # (``gpu.vram_gib``).  Recover only when the cited, accepted evidence
            # for this exact entity proves one unambiguous matching suffix.  This
            # is normalization, not fuzzy matching: a wrong/mixed evidence id still
            # fails closed below.
            cited_rows = [
                row for row in self.repository.evidence(set(claim.evidence_ids))
                if row.review_status == "accepted" and row.entity_id == claim.entity_id
            ]
            evidence_fields = {row.field_key for row in cited_rows}
            if len(cited_rows) == len(set(claim.evidence_ids)) and len(evidence_fields) == 1:
                resolved_key = next(iter(evidence_fields))
                if resolved_key.rsplit(".", 1)[-1] == claim.field_key:
                    resolved_field = self.repository.field(resolved_key)
                    if resolved_field is not None:
                        field = resolved_field
                        claim = claim.model_copy(update={"field_key": resolved_key})
        if field is None:
            return self._result(index, claim, "unverifiable", "field_definition_not_found")
        entity = self.repository.candidate(claim.entity_id)
        if entity is None:
            return self._result(index, claim, "missing", "claim_entity_not_found", field=field)
        if field.applies_to_entity_type not in {"entity", entity.entity_type}:
            return self._result(index, claim, "unverifiable", "field_entity_type_mismatch", field=field)
        if not field.active:
            return self._result(index, claim, "unverifiable", "field_inactive", field=field)
        if not field.claimable or field.comparison_method == "text_only":
            return self._result(index, claim, "unverifiable", "field_not_claimable", field=field)
        try:
            stored = self.repository.field_value(claim.entity_id, field, claim.qualifier_key or "default")
        except ValueError as exc:
            return self._result(index, claim, "unverifiable", str(exc), field=field)
        if not stored.found or stored.value is None:
            return self._result(index, claim, "missing", "truth_value_missing", field=field)
        comparison = self._compare_claim(claim, stored, field)
        if isinstance(comparison, str):
            return self._result(index, claim, "unverifiable", comparison, field=field, canonical=stored.value)
        if not comparison.matches:
            return self._result(index, claim, "conflict", "truth_value_conflict", field=field, canonical=stored.value)

        cited = set(claim.evidence_ids)
        evidence_rows = {row.id: row for row in self.repository.evidence(cited)}
        valid: list[UUID] = []
        contradictory = False
        for evidence_id in sorted(cited, key=str):
            row = evidence_rows.get(evidence_id)
            if row is None or row.review_status != "accepted" or row.entity_id != claim.entity_id or row.field_key != field.field_key:
                continue
            try:
                evidence_value = convert_unit(row.normalized_value, row.unit, comparison.canonical_unit)
                if values_match(
                    evidence_value, stored.value, value_type=field.value_type,
                    method=field.comparison_method, tolerance=field.tolerance,
                ):
                    valid.append(evidence_id)
                else:
                    contradictory = True
            except ComparisonUnavailable:
                continue
        if contradictory:
            return self._result(index, claim, "conflict", "evidence_value_conflict", field=field, canonical=stored.value)
        if len(valid) != len(cited):
            return self._result(index, claim, "missing", "evidence_not_accepted_or_mismatched", field=field, canonical=stored.value, evidence=valid)
        return self._result(index, claim, "supported", "truth_and_evidence_match", field=field, canonical=stored.value, evidence=valid)

    def _compare_claim(self, claim: Claim, stored: StoredValue, field: FieldRecord) -> _Comparison | str:
        canonical_unit = stored.unit or field.canonical_unit
        try:
            claimed = convert_unit(claim.value, claim.unit, canonical_unit)
            matches = values_match(
                claimed, stored.value, value_type=field.value_type,
                method=field.comparison_method, tolerance=field.tolerance,
            )
            return _Comparison(matches, claimed, canonical_unit)
        except ComparisonUnavailable as exc:
            return str(exc)

    def _verify_measurement(self, index: int, claim: Claim) -> ClaimVerification:
        metric = self.repository.metric(claim.metric_key or "")
        if metric is None:
            return self._result(index, claim, "unverifiable", "metric_definition_not_found")
        if self.repository.candidate(claim.entity_id) is None:
            return self._result(index, claim, "missing", "claim_entity_not_found", canonical_unit=metric.canonical_unit)
        if not metric.active:
            return self._result(index, claim, "unverifiable", "metric_inactive", canonical_unit=metric.canonical_unit)
        statistic = claim.statistic or "reported"
        valid: list[UUID] = []
        for run_id in sorted(set(claim.benchmark_run_ids), key=str):
            row = self.repository.benchmark_value(run_id, claim.entity_id, metric, statistic)
            if not row.run_exists:
                return self._result(index, claim, "missing", "benchmark_run_missing", canonical_unit=metric.canonical_unit, benchmark=valid)
            if row.run_status == "invalid":
                return self._result(index, claim, "unverifiable", "benchmark_run_invalid", canonical_unit=metric.canonical_unit, benchmark=valid)
            if not row.subject_matches:
                return self._result(index, claim, "conflict", "benchmark_subject_mismatch", canonical_unit=metric.canonical_unit, benchmark=valid)
            if not row.metric_found:
                return self._result(index, claim, "missing", "benchmark_metric_missing", canonical_unit=metric.canonical_unit, benchmark=valid)
            try:
                claimed = convert_unit(claim.value, claim.unit, row.unit or metric.canonical_unit)
                if not values_match(claimed, row.value, value_type=metric.value_type, method="exact"):
                    return self._result(index, claim, "conflict", "benchmark_value_conflict", canonical=row.value, canonical_unit=row.unit or metric.canonical_unit, benchmark=valid)
            except ComparisonUnavailable as exc:
                return self._result(index, claim, "unverifiable", str(exc), canonical=row.value, canonical_unit=row.unit or metric.canonical_unit, benchmark=valid)
            valid.append(run_id)
        return self._result(
            index, claim, "supported", "benchmark_measurement_matches",
            canonical=claim.value, canonical_unit=metric.canonical_unit, comparison_method="exact", benchmark=valid,
        )

    def _verify_derived(self, index: int, claim: Claim) -> ClaimVerification:
        valid_refs: list[str] = []
        canonical: Any | None = None
        canonical_unit: str | None = None
        for reference in claim.rule_refs:
            if "@" not in reference:
                return self._result(index, claim, "unverifiable", "rule_ref_invalid", rules=valid_refs)
            rule_key, version = reference.rsplit("@", 1)
            rule = self.repository.rule(rule_key, version)
            if rule is None or not rule.active:
                return self._result(index, claim, "missing", "rule_definition_missing", rules=valid_refs)
            evaluator = self.rules.get(rule.implementation_ref)
            if evaluator is None:
                return self._result(index, claim, "unverifiable", "rule_evaluator_not_registered", rules=valid_refs)
            evaluation = evaluator(claim, self.repository)
            if evaluation.missing_inputs:
                return self._result(index, claim, "missing", "rule_inputs_missing", rules=valid_refs)
            try:
                asserted = convert_unit(claim.value, claim.unit, evaluation.unit)
                if not values_match(asserted, evaluation.value, value_type=claim.value_type or _infer_type(evaluation.value), method="exact"):
                    return self._result(index, claim, "conflict", "derived_value_conflict", canonical=evaluation.value, canonical_unit=evaluation.unit, rules=valid_refs)
            except ComparisonUnavailable as exc:
                return self._result(index, claim, "unverifiable", str(exc), canonical=evaluation.value, canonical_unit=evaluation.unit, rules=valid_refs)
            valid_refs.append(reference)
            canonical, canonical_unit = evaluation.value, evaluation.unit
        return self._result(index, claim, "supported", "derived_rule_matches", canonical=canonical, canonical_unit=canonical_unit, rules=valid_refs)

    def _verify_price(self, index: int, claim: Claim) -> ClaimVerification:
        """Verify a price claim against the newest PriceSnapshot matching context.

        Prices are observed snapshots, not permanent facts: currency is carried
        in ``unit`` (ISO 4217) and ``market_region`` / ``condition`` /
        ``price_type`` qualify the observation window.  Provenance points at the
        PriceSnapshot row id, never at a static FieldDefinition/evidence claim.
        """
        if self.repository.candidate(claim.entity_id) is None:
            return self._result(index, claim, "missing", "claim_entity_not_found")
        currency = (claim.unit or "").strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            return self._result(index, claim, "unverifiable", "price_currency_invalid")
        snapshot = self.repository.latest_price(
            claim.entity_id,
            currency,
            region=claim.market_region,
            condition=claim.condition,
            price_type=claim.price_type,
        )
        if not snapshot.found:
            return self._result(
                index, claim, "missing", "price_snapshot_not_found",
                canonical_unit=currency,
            )
        try:
            matches = values_match(claim.value, snapshot.amount, value_type="number", method="exact")
        except ComparisonUnavailable as exc:
            return self._result(
                index, claim, "unverifiable", str(exc),
                canonical=snapshot.amount, canonical_unit=currency,
            )
        if not matches:
            return self._result(
                index, claim, "conflict", "price_snapshot_conflict",
                canonical=snapshot.amount, canonical_unit=currency,
            )
        price_snapshots = [snapshot.snapshot_id] if snapshot.snapshot_id is not None else []
        provenance = None
        if snapshot.snapshot_id is not None:
            provenance = {
                "snapshot_id": snapshot.snapshot_id,
                "source_id": snapshot.source_id,
                "source_key": snapshot.source_key,
                "source_url": snapshot.source_url,
                "observed_at": snapshot.observed_at,
                "market_region": snapshot.market_region,
                "condition": snapshot.condition,
                "price_type": snapshot.price_type,
            }
        return self._result(
            index, claim, "supported", "price_snapshot_matches",
            canonical=snapshot.amount, canonical_unit=currency,
            price_snapshots=price_snapshots, price_provenance=provenance,
        )

    @staticmethod
    def _result(
        index: int,
        claim: Claim,
        status: str,
        reason: str,
        *,
        field: FieldRecord | None = None,
        canonical: Any | None = None,
        canonical_unit: str | None = None,
        comparison_method: str | None = None,
        evidence: list[UUID] | None = None,
        price_snapshots: list[UUID] | None = None,
        price_provenance: dict[str, Any] | None = None,
        benchmark: list[UUID] | None = None,
        rules: list[str] | None = None,
    ) -> ClaimVerification:
        return ClaimVerification(
            claim_index=index,
            claim=claim,
            status=status,
            reason_code=reason,
            canonical_value=canonical,
            canonical_unit=canonical_unit if canonical_unit is not None else (field.canonical_unit if field else None),
            comparison_method=comparison_method if comparison_method is not None else (field.comparison_method if field else None),
            valid_evidence_ids=evidence or [],
            valid_price_snapshot_ids=price_snapshots or [],
            price_provenance=price_provenance,
            valid_benchmark_run_ids=benchmark or [],
            valid_rule_refs=rules or [],
        )


def _infer_type(value: Any) -> str:
    if isinstance(value, bool): return "boolean"
    if isinstance(value, int): return "integer"
    if isinstance(value, float): return "number"
    if isinstance(value, str): return "string"
    if isinstance(value, dict): return "object"
    if isinstance(value, list): return "array"
    return "json"


__all__ = ["TruthVerificationError", "TruthVerifier"]
