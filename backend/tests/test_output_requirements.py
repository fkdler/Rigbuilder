"""End-to-end contract regressions for explicit recommendation requirements."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import request_analysis, request_scope
from app.services.hardware_intent import discovery_queries
from app.services.hardware_requirements import parse_requirements
from app.services.narrator import deterministic_narration
from app.services.routing import resolve_query_mode

PAIR = '给我推荐两张 NVIDIA 显卡，一张偏性价比，一张偏高性能。'
MINIMUM = '推荐一款显存至少 16GB 的 NVIDIA 显卡'


@pytest.mark.parametrize('phrase', ['不要搜索数据库', '不要检索数据库', '不要 搜索 数据库', '禁止查询数据库', "don't search the database"])
@pytest.mark.parametrize('mode', ['auto', 'fusion'])
def test_explicit_opt_out_skips_classifier_and_verified_route(monkeypatch, phrase, mode):
    monkeypatch.setattr(request_analysis, 'get_settings', lambda: SimpleNamespace(routing_analysis_enabled=True))
    async def forbidden(*args, **kwargs):
        pytest.fail('Explicit opt-out must not call the classifier')
    monkeypatch.setattr(request_analysis, 'complete_chat', forbidden)
    message = phrase + '，回答：' + MINIMUM
    assert resolve_query_mode(message, mode, True, has_recommendation=True) == 'chat'
    resolved, _, details = asyncio.run(request_analysis.analyze_request(message, mode, True, original='推荐游戏配置'))
    assert resolved == 'chat' and not details['requires_database']


def test_discovery_filters_before_limit_and_covers_both_priorities():
    from app.core.config import Settings
    from app.tools.validator import validate_sql, ValidationSuccess
    settings = Settings(_env_file=None, database_url='postgresql+psycopg://u:p@localhost/db')
    queries = discovery_queries(PAIR, {'gpu'})
    assert len(queries) == 2
    assert 'index_100 ASC' in queries[0] and 'index_100 DESC' in queries[1]
    assert all("ILIKE '%NVIDIA%'" in q for q in queries)
    queries += discovery_queries(MINIMUM, {'gpu'})
    assert 'c.vram_gib >= 16.0' in queries[-1]
    assert all(isinstance(validate_sql(q, settings), ValidationSuccess) for q in queries)


@pytest.mark.parametrize('capacity,name,evidence,accepted', [
    (12, 'Radeon RX 6700 XT', True, False),
    (24, 'Radeon RX 7900 XTX', True, False),
    (12, 'GeForce RTX 4070', True, False),
    (16, 'GeForce RTX 4060 Ti', True, True),
    (24, 'GeForce RTX 4090', True, True),
    (16, 'GeForce RTX 4060 Ti', False, False),
])
def test_truth_of_a_spec_does_not_imply_satisfaction(monkeypatch, capacity, name, evidence, accepted):
    from test_truth_verification import FakeRepository, fact, recommendation
    from app.verification import TruthVerifier
    repo = FakeRepository()
    repo.candidate_record = replace(repo.candidate_record, canonical_name=name)
    repo.stored_value = replace(repo.stored_value, value=capacity)
    repo.evidence_records = [replace(repo.evidence_records[0], normalized_value=capacity)]
    checked = TruthVerifier(repo).verify(recommendation([fact(value=capacity)] if evidence else []))
    monkeypatch.setattr(request_scope, '_category_by_entity', lambda *_: {
        str(checked.candidates[0].candidate_id): {'category': 'gpu', 'entity_key': 'gpu:test'}})
    gated = request_scope.check_requested_categories(checked, MINIMUM, object())
    assert gated.candidates[0].candidate_valid is accepted
    if not accepted:
        assert any(code.startswith(('requested_brand', 'required_vram')) for code in gated.candidates[0].candidate_reason_codes)


def pair_result(monkeypatch, count=2):
    from test_gaming_balance_gate import _Candidate, _Result
    candidates = [_Candidate('GeForce RTX 4060'), _Candidate('GeForce RTX 5090')][:count]
    for c in candidates:
        c.claims, c.elimination_reasons = [], []
        c.source_models, c.fact_support, c.credibility = [], 1, 100
    categories = {str(c.candidate_id): {'category': 'gpu', 'entity_key': c.canonical_name} for c in candidates}
    monkeypatch.setattr(request_scope, '_category_by_entity', lambda *_: categories)
    monkeypatch.setattr(request_scope, '_performance_indices', lambda *_: {c.canonical_name: i + 1 for i, c in enumerate(candidates)})
    monkeypatch.setattr(request_scope, '_mainstream_keys', lambda *_: {'GeForce RTX 4060'})
    result = request_scope.select_role_coverage(_Result(candidates), PAIR, 1, object())
    result.trace.agent_coverage = 1
    return result


def test_two_distinct_picks_survive_top_one_and_presentation_projection(monkeypatch):
    from app.services.fusion import presentation_of
    result = pair_result(monkeypatch)
    assert len(result.top_k) == 2
    narration = deterministic_narration(result, PAIR)
    presentation = presentation_of(narration)
    assert len(presentation.selections) == 2
    assert '性价比' in presentation.selections[0].label
    assert presentation.selections[1].label == '高性能方向'
    assert '缺少当前售价' in ' '.join(presentation.selections[0].reasons)
    assert presentation.selection_notice is None


def test_shortage_is_explicit_without_duplicate_or_filler(monkeypatch):
    result = pair_result(monkeypatch, 1)
    narration = deterministic_narration(result, PAIR)
    assert len(narration.selections) == 1
    assert '要求 2 款' in narration.selection_notice
    assert '只能提供 1 款' in narration.selection_notice


def test_flagship_only_is_not_mislabeled_value(monkeypatch):
    result = pair_result(monkeypatch)
    monkeypatch.setattr(request_scope, '_mainstream_keys', lambda *_: set())
    selected = request_scope.select_role_coverage(result, PAIR, 2, object())
    selected.trace.agent_coverage = 1
    narration = deterministic_narration(selected, PAIR)
    assert len(narration.selections) == 1
    assert narration.selections[0].name == 'GeForce RTX 5090'
    assert narration.selections[0].label == '高性能方向'


def test_single_pick_with_two_preferences_stays_single():
    assert parse_requirements('推荐一张性价比好且高性能的显卡').count == 1


def test_unbranded_split_does_not_limit_performance_query_to_mainstream():
    queries = discovery_queries(PAIR.replace('NVIDIA', ''), {'gpu'})
    assert "tier_label IN ('mid', 'high')" in queries[0]
    assert 'tier_label' not in queries[1]


def test_observed_pool_survives_model_omission_before_truth_verification():
    from test_truth_verification import recommendation, fact
    from app.agents.selection import preserve_requested_pool
    initial = recommendation([fact()])
    identity = str(uuid4())
    observed = fact(entity_id=identity, value=24).model_dump(mode='json')
    rows = {identity: {'name': 'GeForce RTX 4090', 'role': 'gpu', 'facts': {'gpu.vram_gib': observed}}}
    expanded = preserve_requested_pool(initial, rows, PAIR)
    assert len(expanded.recommendations) == 2
    assert expanded.recommendations[1].claims[0].evidence_ids == fact().evidence_ids
    assert expanded.recommendations[1].score == 0


def test_vram_is_not_a_local_ai_workload(monkeypatch):
    from app.services.narrator import _fallback_reasons
    from test_narrator import _fake_result
    from app.services import narrator
    monkeypatch.setattr(narrator, '_supported_facts', lambda _: [{'field_key': 'gpu.vram_gib', 'value': 16, 'unit': 'GiB'}])
    text = ' '.join(_fallback_reasons(_fake_result(uuid4()).top_k[0], MINIMUM))
    assert '16 GiB' in text and '本地模型' not in text and '排序最优' not in text


def test_protocol_preserves_two_query_results_when_model_submits_only_one(monkeypatch):
    from app.agents import loop
    from app.schemas.recommendation import Recommendation
    from test_quality_speed import observation, pool
    from test_terminal_tool import FakeHandle, _database_only_profile, _settings, _text_only
    from app.agents.selection import materialize_selection
    obs = observation(count=2)
    obs.rows[0][1], obs.rows[1][1] = 'GeForce RTX 4060', 'GeForce RTX 5090'
    rows = pool(obs)
    import json
    submitted = materialize_selection(json.dumps({'recommendations': [{
        'candidate_id': obs.rows[0][0], 'score': 80, 'reasons': ['候选']
    }]}), rows, {'gpu'})
    async def budget(*args):
        return SimpleNamespace(exceeds=False, completion_limit=lambda requested: requested)
    monkeypatch.setattr(loop, '_prompt_budget', budget)
    monkeypatch.setattr(loop, 'query_database', lambda *args, **kwargs: obs)
    handle = FakeHandle([_text_only(submitted.model_dump_json())])
    answer = asyncio.run(loop.run_agent_loop_protocol(handle=handle, profile=_database_only_profile(5),
        messages=[{'role': 'user', 'content': PAIR}], settings=_settings()))
    assert answer.status == 'completed'
    assert answer.sql_calls_used == 2
    result = Recommendation.model_validate(answer.recommendation)
    assert {item.name for item in result.recommendations} == {'GeForce RTX 4060', 'GeForce RTX 5090'}


@pytest.mark.parametrize('message', ['推荐显存不低于16GiB的显卡', '推荐至少16384MiB显存的显卡', '推荐16GB以上显存的显卡'])
def test_capacity_units_and_phrasings(message):
    assert parse_requirements(message).min_vram == 16
