"""Replacement must preserve requirements while excluding already shown IDs."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.services import request_analysis, request_scope, model_advice
from app.services.execution_intent import ExecutionIntent, execution_intent, planned_messages
from app.services.local_models import discovery_queries
from app.services.model_choices import (excluded_model_ids, wants_another, reference_from_job,
    load_model_choice_reference, next_choice_state)
from app.services.requirements import continue_request

ORIGINAL = '推荐适合12GB显存的Qwen量化聊天模型，使用Q4_K_M'
PLAN = ExecutionIntent(domain='ai_model', roles=['model'], workload='local_ai')
A, B, C = [str(UUID(int=i)) for i in (1, 2, 3)]


def job_result(selected, *, state=None, message=ORIGINAL):
    return SimpleNamespace(request_payload={'effective_message': message,
        'routing_analysis': {'intent': PLAN.model_dump()}, 'model_choice_state': state or {}},
        result_payload={'kind': 'fusion', 'status': 'completed', 'result': {'top_k': [
            {'candidate_id': identity, 'candidate_type': 'ai_model'} for identity in selected]}})


@pytest.mark.parametrize('message', ['换一个', '换个', '换一款模型', '再推荐一个', '还有别的吗',
    '不要重复，推荐其他模型', '推荐一个不同的模型', '换一个模型，显卡和显存不变', 'another model'])
@pytest.mark.parametrize('mode', ['auto', 'fusion'])
def test_replacement_is_model_continuation_without_classifier(monkeypatch, message, mode):
    monkeypatch.setattr(request_analysis, 'get_settings', lambda: SimpleNamespace(routing_analysis_enabled=False))
    mode, continuation, detail = asyncio.run(request_analysis.analyze_request(message, mode, original=ORIGINAL))
    assert mode == 'fusion' and continuation
    assert detail['intent']['domain'] == 'ai_model' and detail['intent']['roles'] == ['model']
    effective = continue_request(message, ORIGINAL, continuation)
    assert '12GB' in effective and 'Qwen' in effective and 'Q4_K_M' in effective and '聊天' in effective


@pytest.mark.parametrize('message', ['换成Q8_0，其他不变', '不要换一个', '换个话题', '换一个问题'])
def test_non_replacement_is_not_exclusion(message):
    assert not wants_another(message)


@pytest.mark.parametrize('mode,message', [('chat', '换一个'), ('fusion', '不要搜索数据库，换一个')])
def test_no_database_and_fast_take_precedence(monkeypatch, mode, message):
    monkeypatch.setattr(request_analysis, 'get_settings', lambda: SimpleNamespace(routing_analysis_enabled=False))
    assert asyncio.run(request_analysis.analyze_request(message, mode, original=ORIGINAL))[0] == 'chat'


def test_consecutive_replacements_do_not_cycle_and_fresh_requests_reset():
    first = reference_from_job(job_result([A, C]))
    assert first['seen_ids'] == [A]  # C is an unshown runner-up.
    state = next_choice_state('换一个', first, PLAN, True)
    assert state['excluded_ids'] == [A]
    second = reference_from_job(job_result([B], state=state))
    again = next_choice_state('再推荐一个', second, PLAN, True)
    assert again['excluded_ids'] == [A, B]
    assert next_choice_state('推荐一个新的聊天模型', second, PLAN, False)['excluded_ids'] == []
    assert next_choice_state('换一个显卡', second, ExecutionIntent(roles=['gpu']), True) == {}
    assert next_choice_state('换成Q8_0', second, PLAN, True)['excluded_ids'] == [A]


def test_gap_retains_exclusions_and_chat_clears_reference():
    row = job_result([], state={'seen_ids': [A, B], 'excluded_ids': [A, B]})
    row.result_payload = {'kind': 'chat_fallback', 'answer': '没有其他候选'}
    assert reference_from_job(row)['excluded_ids'] == [A, B]
    row.result_payload = {'kind': 'chat', 'answer': '其他话题'}
    assert reference_from_job(row) is None


def test_capability_advice_records_all_shown_ids_and_scope_is_local():
    row = job_result([])
    row.result_payload = {'kind': 'catalogue_advice', 'selected_model_ids': [A, B]}
    conversation = uuid4()
    class Session:
        def scalar(self, statement):
            assert conversation in statement.compile().params.values()
            assert 'completed' in statement.compile().params.values()
            return row
    assert load_model_choice_reference(Session(), conversation)['seen_ids'] == [A, B]
    assert load_model_choice_reference(None, None) is None


def test_exclusions_precede_limit_and_never_contaminate_family_or_memory_filters():
    from app.core.config import Settings
    from app.tools.validator import validate_sql, ValidationSuccess
    token = excluded_model_ids.set((A, B))
    try:
        query = discovery_queries(ORIGINAL)[0]
        assert query.index('m.entity_id NOT IN') < query.index('LIMIT 3')
        assert A in query and B in query and '12.0' in query and 'Q4_K_M' in query
        assert "ILIKE '%Qwen%'" in query
        settings = Settings(_env_file=None, database_url='postgresql+psycopg://u:p@localhost/db')
        assert isinstance(validate_sql(query, settings), ValidationSuccess)
    finally:
        excluded_model_ids.reset(token)
    assert A not in discovery_queries(ORIGINAL)[0]


def test_old_model_is_rejected_even_if_agent_ignores_exclusion():
    from test_truth_verification import FakeRepository, fact, recommendation, CANDIDATE_ID
    from app.verification import TruthVerifier
    checked = TruthVerifier(FakeRepository()).verify(recommendation([fact()]))
    checked.candidates[0] = checked.candidates[0].model_copy(update={'candidate_type': 'ai_model'})
    plan_token = execution_intent.set(PLAN)
    token = excluded_model_ids.set((str(CANDIDATE_ID),))
    try:
        gated = request_scope.check_requested_categories(checked, ORIGINAL, None)
        assert not gated.candidates[0].candidate_valid
        assert 'previously_recommended_model' in gated.candidates[0].candidate_reason_codes
        messages = planned_messages([{'role': 'system', 'content': 'protocol'}], ORIGINAL)
        assert any(str(CANDIDATE_ID) in m['content'] for m in messages if m['role'] == 'system')
    finally:
        excluded_model_ids.reset(token)
        execution_intent.reset(plan_token)


def test_concurrent_requests_do_not_share_exclusions():
    async def request(identity):
        token = excluded_model_ids.set((identity,))
        try:
            await asyncio.sleep(0)
            return discovery_queries(ORIGINAL)[0]
        finally:
            excluded_model_ids.reset(token)
    async def run():
        return await asyncio.gather(request(A), request(B))
    first, second = asyncio.run(run())
    assert A in first and B not in first and B in second and A not in second
    assert excluded_model_ids.get() == ()


def test_capability_path_never_repeats_and_reports_exhaustion(monkeypatch):
    from test_fusion_service import FakeSession, FakeContextService
    monkeypatch.setattr(model_advice, 'SessionLocal', FakeSession)
    monkeypatch.setattr(model_advice, 'conversation_context_service', FakeContextService())
    choices = [(SimpleNamespace(id=identity, canonical_name=name),
                SimpleNamespace(official_model_card_url=None), None)
               for identity, name in [(A, 'Vision A'), (B, 'Vision B')]]
    monkeypatch.setattr(model_advice, 'verified_capability_candidates', lambda *_: choices)
    token = excluded_model_ids.set((A,))
    try:
        result = asyncio.run(model_advice.reply_model_advice('换一个视觉模型', uuid4(), {'vision_input'}))
        assert result['selected_model_ids'] == [B]
        assert 'Vision A' not in result['answer'] and 'Vision B' in result['answer']
        excluded_model_ids.set((A, B))
        result = asyncio.run(model_advice.reply_model_advice('换一个', uuid4(), {'vision_input'}))
        assert result['kind'] == 'chat_fallback' and '因此不会重复推荐' in result['answer']
        assert result['selected_model_ids'] == []
    finally:
        excluded_model_ids.reset(token)


@pytest.mark.parametrize('capability', [False, True])
def test_job_uses_snapshot_for_replacement_and_persists_chain(monkeypatch, capability):
    from app.services import jobs
    from app.inference import context_window
    from app.schemas.query import QueryJobRequest
    from app.schemas.fusion import FusionRunResponse
    question = '推荐视觉模型' if capability else ORIGINAL
    reference = {'question': question, 'seen_ids': [A, B], 'excluded_ids': [A]}
    row = SimpleNamespace(id=uuid4(), request_id=uuid4(), owner_id=uuid4(), status='queued',
        resolved_mode='chat', request_payload={'model_choice_reference': reference, 'previous_build_request': question})
    class Session:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, *_): return row
        def commit(self): pass
        def execute(self, *_args, **_kwargs): return SimpleNamespace(rowcount=1)
    monkeypatch.setattr(jobs, 'SessionLocal', Session)
    monkeypatch.setattr(jobs.QueryJobManager, '_append_event', lambda *_: None)
    async def refresh(*args, **kwargs): pass
    monkeypatch.setattr(context_window, 'refresh_context_windows', refresh)
    monkeypatch.setattr(request_analysis, 'get_settings', lambda: SimpleNamespace(routing_analysis_enabled=False))
    calls = []
    async def fusion(message, conv, constraints, top_k, **kwargs):
        assert not capability
        assert all(word in message for word in ('12GB', 'Qwen', 'Q4_K_M', '聊天'))
        assert excluded_model_ids.get() == (A, B)
        calls.append('fusion')
        return FusionRunResponse(request_id=kwargs['request_id'], conversation_id=conv,
            status='failed', agents=[], error='No remaining candidates')
    async def advice(message, conv, caps, **kwargs):
        assert capability and caps == {'vision_input'} and '视觉' in message
        assert excluded_model_ids.get() == (A, B)
        calls.append('advice')
        return {'kind': 'catalogue_advice', 'conversation_id': str(conv), 'answer': 'new', 'selected_model_ids': [C]}
    monkeypatch.setattr(jobs.fusion_service, 'run', fusion)
    monkeypatch.setattr(model_advice, 'reply_model_advice', advice)
    request = QueryJobRequest(message='换一个', conversation_id=uuid4(), mode='auto')
    asyncio.run(jobs.QueryJobManager()._execute(row.id, request))
    assert row.status == 'completed' and calls == ['advice' if capability else 'fusion']
    assert row.request_payload['model_choice_state']['excluded_ids'] == [A, B]
    assert row.resolved_mode == 'fusion'
    if not capability:
        assert '因此不会重复推荐' in row.result_payload['answer']
    assert excluded_model_ids.get() == () and execution_intent.get() is None


def test_prefixed_replacement_and_old_history_are_backfilled_without_crossing_new_request():
    latest = job_result([B])
    latest.request_payload.pop('model_choice_state')
    latest.request_payload['message'] = '换一个'
    first = job_result([A])
    first.request_payload.pop('model_choice_state')
    first.request_payload['message'] = ORIGINAL
    unrelated = job_result([C], message='推荐别的场景的模型')
    conversation = uuid4()
    class Session:
        def scalar(self, query): return latest
        def scalars(self, query):
            assert conversation in query.compile().params.values()
            return [latest, first, unrelated]
    ref = load_model_choice_reference(Session(), conversation)
    assert ref['seen_ids'] == [A, B]
    assert next_choice_state('换一个', ref, PLAN, True)['excluded_ids'] == [A, B]


def test_explanation_preserves_chain_without_excluding_current_choice():
    reference = {'seen_ids': [A, B], 'excluded_ids': [A]}
    state = next_choice_state('为什么推荐这个模型？', reference, None, False)
    assert state == reference
    row = job_result([B], state=state, message='为什么推荐这个模型？')
    row.result_payload['result']['trace'] = {'input_summary': {'anchor_question': ORIGINAL}}
    following = reference_from_job(row)
    assert following['question'] == ORIGINAL
    assert next_choice_state('换一个', following, PLAN, True)['excluded_ids'] == [A, B]


def test_empty_fusion_presentation_reports_no_alternative():
    from app.services.narrator import deterministic_narration
    token = excluded_model_ids.set((A, B))
    try:
        result = SimpleNamespace(top_k=[], trace=SimpleNamespace(input_summary={}, agent_coverage=1))
        narration = deterministic_narration(result, ORIGINAL)
        assert '因此不会重复推荐' in narration.headline and narration.primary is None
    finally:
        excluded_model_ids.reset(token)


def test_explicit_hardware_switch_is_not_forced_back_to_models():
    from app.services.model_choices import model_replacement
    assert not model_replacement('换一张显卡', ORIGINAL)
    assert not model_replacement('再推荐一个CPU', ORIGINAL)


def test_queued_job_freezes_model_history_at_submission(monkeypatch):
    from app.services import jobs
    from app.schemas.query import QueryJobRequest
    reference = {'question': ORIGINAL, 'seen_ids': [A, B], 'excluded_ids': [A]}
    added = []
    class Session:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def add(self, row): added.append(row)
        def flush(self): added[0].id = uuid4()
        def commit(self): pass
    monkeypatch.setattr(jobs, 'SessionLocal', Session)
    monkeypatch.setattr(jobs, 'load_recommendation_anchor', lambda *_: None)
    monkeypatch.setattr(jobs, 'load_model_choice_reference', lambda *_: reference)
    monkeypatch.setattr(jobs.QueryJobManager, '_append_event', lambda *_: None)
    monkeypatch.setattr(jobs.QueryJobManager, '_response', lambda _self, _session, row: row)
    async def no_execution(*args): pass
    monkeypatch.setattr(jobs.QueryJobManager, '_execute', no_execution)
    async def run():
        manager = jobs.QueryJobManager()
        manager.submit(QueryJobRequest(message='换一个', conversation_id=uuid4()), uuid4())
        await asyncio.gather(*manager._tasks.values())
    asyncio.run(run())
    assert added[0].request_payload['model_choice_reference'] == reference
    assert added[0].request_payload['previous_build_request'] == ORIGINAL
