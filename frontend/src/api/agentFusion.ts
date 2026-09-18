
export type ConstraintKind = "hard" | "preference";
export type ConstraintTarget = "field" | "price" | "compatibility" | "runtime";
export type FieldOperator = "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "in" | "contains";

export interface BaseConstraint { constraint_id: string; kind: ConstraintKind; confirmed: true; weight?: number }
export interface FieldConstraint extends BaseConstraint { target: "field"; field_key: string; operator: FieldOperator; value: unknown; unit?: string; qualifier_key?: string }
export interface PriceConstraint extends BaseConstraint { target: "price"; operator: "eq" | "ne" | "gt" | "gte" | "lt" | "lte"; value: number; currency?: string; region?: string; condition?: string; price_type?: string }
export interface CompatibilityConstraint extends BaseConstraint { target: "compatibility"; other_entity_id: string; relation_key: string; direction?: "outgoing" | "incoming"; required_status?: string }
export interface RuntimeConstraint extends BaseConstraint { target: "runtime"; runtime_key: string; required_status?: string }
export type Constraint = FieldConstraint | PriceConstraint | CompatibilityConstraint | RuntimeConstraint;

export type CandidateType = "hardware" | "ai_model" | "model_variant";
export type VerificationStatus = "supported" | "conflict" | "missing" | "unverifiable";
export type ConstraintStatus = "satisfied" | "violated" | "unknown";

export interface Claim {
  claim_type: "fact" | "measurement" | "derived" | "preference" | "price";
  entity_id: string; field_key?: string | null; metric_key?: string | null; value: unknown;
  value_type?: string | null; unit?: string | null; qualifier_key?: string | null;
  statistic?: string | null; market_region?: string | null; condition?: string | null;
  price_type?: string | null; evidence_ids: string[]; benchmark_run_ids: string[]; rule_refs: string[];
}
export interface ClaimVerification {
  claim_index: number; claim: Claim; status: VerificationStatus; reason_code: string;
  canonical_value?: unknown; canonical_unit?: string | null; comparison_method?: string | null;
  valid_evidence_ids: string[]; valid_benchmark_run_ids: string[]; valid_rule_refs: string[];
  valid_price_snapshot_ids: string[];
  price_provenance?: { snapshot_id: string; source_id?: string | null; source_key?: string | null; source_url?: string | null; observed_at?: string | null; market_region?: string | null; condition?: string | null; price_type?: string | null } | null;
}
export interface RecommendationItem { candidate_id: string; candidate_type: CandidateType; name: string; score: number; model_confidence?: number | null; reasons: string[]; risks: string[]; claims: Claim[] }
export interface RecommendationSchema { schema_version: "3.0"; recommendations: RecommendationItem[]; insufficient_information: string[]; tool_summary: Record<string, unknown> }
export interface VerificationCandidate { candidate_id: string; candidate_type: string; canonical_name?: string | null; candidate_valid: boolean; candidate_reason_codes: string[]; claims: ClaimVerification[]; fact_support: number; proof_coverage: number; information_completeness: number; supported_count: number; conflict_count: number; missing_count: number; unverifiable_count: number }
export interface Verification { release_key: string; candidates: VerificationCandidate[] }
export interface AgentRun { agent_run_id: string; model_id: string; response_model?: string | null; status: "completed" | "failed"; recommendation?: RecommendationSchema | null; verification?: Verification | null; error_code?: string | null; error?: string | null; raw_output?: string | null }
export interface ConstraintVerification { constraint_id: string; kind: ConstraintKind; target: string; status: ConstraintStatus; reason_code: string; actual_value?: unknown; expected_value?: unknown; weight: number }
export interface FusedClaim { claim: Claim; verification: ClaimVerification; source_models: string[] }
export interface FusedCandidate {
  candidate_id: string; candidate_type: string; canonical_name: string; recommendation_score: number;
  credibility: number; fact_support: number; proof_coverage: number; information_completeness: number;
  constraint_validity: number; preference_utility: number; consensus: number; source_models: string[];
  source_ranks: Record<string, number>; agent_scores: Record<string, number>;
  model_confidences: Record<string, number | null>; constraints: ConstraintVerification[];
  claims: FusedClaim[]; elimination_reasons: string[];
}
export interface FusionTrace { policy_version: string; unit_policy_version: string; release_key: string; participating_models: string[]; failed_models: string[]; configured_agent_count: number; agent_coverage: number; formula: Record<string, unknown>; input_summary: Record<string, unknown>; tie_break_order: string[] }
export interface FusionResult { release_key: string; policy_version: string; top_k: FusedCandidate[]; eliminated: FusedCandidate[]; trace: FusionTrace }
export interface NarratorCandidateText { candidate_id: string; explanation: string }
export interface PrimaryRecommendation { candidate_id: string; name: string; reasons: string[] }
export interface AlternativeNote { candidate_id: string; name: string; note: string }
/**
 * The conclusion-shaped view of a fused result (Plan_V4.1).
 *
 * `headline` is the one sentence a user should be able to read on its own;
 * `primary.reasons` must tie verified facts to the stated need. The narrator may
 * rephrase but never re-decide, so this never contradicts `result`.
 */
export interface PresentationResult {
  selections?: Array<{ candidate_id: string; name: string; label: string; reasons: string[] }>;
  selection_notice?: string | null;
  headline: string;
  primary?: PrimaryRecommendation | null;
  evidence?: string[];
  sources?: { title: string; url: string; field?: string }[];
  alternatives?: AlternativeNote[];
  caveats?: string[];
  per_candidate?: NarratorCandidateText[];
  core_build?: CoreBuild | null;
}

/**
 * Where a supporting specification came from (Plan_V4.2).
 *
 * `database_requirement` — the Release states this requirement for the workload.
 * `database_derived` — the backend computed it from the two core components.
 * `not_in_catalogue` — nothing in the Catalogue decides it.
 *
 * None of the three is a verified fact, so the panel marks them differently.
 */
export type SupportBasis = "database_requirement" | "database_derived" | "not_in_catalogue" | "general_advice";
export interface CoreComponent { role: "cpu" | "gpu"; candidate_id: string; name: string; reasons: string[] }
export interface SupportingSpec { role: "platform" | "memory" | "storage" | "psu" | "display" | "cooler" | "case"; spec: string; basis: SupportBasis; note?: string | null }
/**
 * The core-build view of a whole-machine answer (Plan_V4.2).
 *
 * Two concrete core components plus supporting parts stated only as the specification
 * tiers the Catalogue holds — it carries no assembled machine and no compatibility
 * relation, so anything outside `core` is a derived suggestion, never a verified fact.
 */
export interface CoreBuild { core: CoreComponent[]; supporting: SupportingSpec[]; gaps: string[]; required_core_roles?: ("cpu" | "gpu")[]; status?: "draft" | "partial" }
/** Superset of PresentationResult: `overview`/`per_candidate` keep their meaning. */
export interface NaturalLanguage extends PresentationResult {
  headline?: string | null;
  overview: string;
  per_candidate: NarratorCandidateText[];
}

export interface AgentFusionResponse {
  answer_purpose?: "recommendation" | "explanation"; request_id: string; conversation_id: string; status: "completed" | "failed"; release_key?: string | null; policy_version: string; agents: AgentRun[]; result?: FusionResult | null; natural_language?: NaturalLanguage | null; presentation?: PresentationResult | null; error?: string | null }
