import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services import request_analysis
from app.services.execution_intent import ExecutionIntent, execution_intent, planned_messages
from app.services.hardware_intent import discovery_queries
from app.services.routing import classify_request, requested_component_roles
from app.services.build_assembler import assemble_core_build


@pytest.mark.parametrize('question,scope,roles,continuation,task', [
    ('推荐一个特别好的 GPU', 'single', ['gpu'], False, 'recommend'),
    ('给我选块性能强的显卡', 'single', ['gpu'], False, 'recommend'),
    ('有哪些值得选的高端显卡', 'single', ['gpu'], False, 'recommend'),
    ('只推荐游戏显卡，不要整机', 'single', ['gpu'], False, 'recommend'),
    ('推荐一套普通打游戏的配置', 'bundle', ['cpu', 'gpu'], False, 'recommend'),
    ('显卡换成RTX 5070，其他不变', 'bundle', ['cpu', 'gpu'], True, 'recommend'),
    ('介绍一下RTX 5090', 'none', ['gpu'], False, 'introduce'),
])
def test_semantic_plan_controls_scope_without_quantity_word_rules(monkeypatch, question, scope, roles, continuation, task):
    monkeypatch.setattr(request_analysis, 'get_settings', lambda: Settings(_env_file=None,
        database_url='postgresql+psycopg://u:p@localhost/db', agent_b_base_url='http://offline.invalid/v1'))
    async def complete(messages, **kwargs):
        assert kwargs['max_tokens'] <= 320
        assert kwargs['temperature'] == 0
        assert set(kwargs['response_schema']['required']) == set(kwargs['response_schema']['properties'])
        return SimpleNamespace(content=json.dumps({'requires_database': True, 'continues_build': continuation,
            'task': task, 'scope': scope, 'roles': roles, 'priority': 'performance', 'workload': 'gaming',
            'subject': 'RTX 5090' if task == 'introduce' else ''}))
    monkeypatch.setattr(request_analysis, 'complete_chat', complete)
    mode, inherits, detail = asyncio.run(request_analysis.analyze_request(question, 'auto', original='推荐一套游戏配置'))
    assert mode == 'fusion' and inherits == continuation
    assert detail['intent']['scope'] == scope
    token = execution_intent.set(ExecutionIntent.model_validate(detail['intent']))
    try:
        assert requested_component_roles('旧回答包含 CPU 和 GPU 整机配置') == set(roles)
        if scope == 'single':
            assert classify_request('旧回答包含整机配置') == 'single'
            assert assemble_core_build(candidates=[SimpleNamespace(candidate_id='fake')], message=question, session=None) is None
            queries = discovery_queries(question, set(roles))
            assert len(queries) == 1 and 'gpu_catalog' in queries[0]
            assert "p.form_factor = 'desktop'" in queries[0]
            assert 'p.index_100 DESC' in queries[0]
    finally:
        execution_intent.reset(token)
    assert execution_intent.get() is None


def test_planned_context_keeps_confirmed_constraints_but_drops_old_recommendations():
    token = execution_intent.set(ExecutionIntent(scope='single', roles=['gpu']))
    try:
        messages = [{'role': 'system', 'content': 'protocol'}, {'role': 'user', 'content': 'old CPU request'},
                    {'role': 'assistant', 'content': 'old RTX 5090 full build'},
                    {'role': 'system', 'content': 'Confirmed constraints: vram >= 16'}]
        result = planned_messages(messages, '推荐GPU')
        assert result[-1] == {'role': 'user', 'content': '推荐GPU'}
        text = json.dumps(result)
        assert 'old' not in text and 'vram >= 16' in text and 'scope=single' in text
    finally:
        execution_intent.reset(token)


def test_intent_is_isolated_between_concurrent_jobs():
    async def task(role):
        token = execution_intent.set(ExecutionIntent(roles=[role]))
        try:
            await asyncio.sleep(0)
            return requested_component_roles('推荐硬件')
        finally:
            execution_intent.reset(token)
    async def run():
        return await asyncio.gather(task('cpu'), task('gpu'))
    assert asyncio.run(run()) == [{'cpu'}, {'gpu'}]


def test_introduction_does_not_enter_recommendation_or_llm(monkeypatch):
    from app.services import product_info
    from test_fusion_service import FakeContextService, FakeSession
    from uuid import uuid4
    context = FakeContextService()
    monkeypatch.setattr(product_info, 'SessionLocal', FakeSession)
    monkeypatch.setattr(product_info, 'conversation_context_service', context)
    def lookup(session, subject, token):
        assert subject == 'rtx 5090' and token == subject
        return [SimpleNamespace(canonical_name='GeForce RTX 5090', entity_type='gpu')], [
            {'label': '显存容量', 'value': '32', 'unit': 'GiB', 'field': 'gpu.vram_gib'}], []
    monkeypatch.setattr(product_info, 'lookup_product', lookup)
    result = asyncio.run(product_info.introduce_product('介绍一下RTX 5090', uuid4(), ExecutionIntent(task='introduce')))
    assert result['kind'] == 'product_info'
    assert result['facts'][0]['value'] == '32' and result['facts'][0]['unit'] == 'GiB'
    assert '推荐' not in result['answer'] and '配置' not in result['answer']
    assert [role for role, _ in context.appended] == ['user', 'assistant']


def test_fresh_request_does_not_inherit_old_pins_even_when_model_says_continue(monkeypatch):
    monkeypatch.setattr(request_analysis, 'get_settings', lambda: Settings(_env_file=None,
        database_url='postgresql+psycopg://u:p@localhost/db', agent_b_base_url='http://offline.invalid/v1'))
    async def complete(messages, **kwargs):
        if json.loads(messages[-1]['content'])['message'].startswith('推荐'):
            assert json.loads(messages[-1]['content'])['previous_request'] == ''
        return SimpleNamespace(content=json.dumps(dict(requires_database=True, continues_build=True,
            task='recommend', scope='bundle', roles=['cpu', 'gpu'], priority='balanced', workload='gaming', subject='')))
    monkeypatch.setattr(request_analysis, 'complete_chat', complete)
    _, continuation, detail = asyncio.run(request_analysis.analyze_request(
        '推荐一套普通打游戏的配置', 'auto', original='必须使用RTX 5090'))
    assert not continuation
    _, continuation, detail = asyncio.run(request_analysis.analyze_request(
        '只推荐游戏显卡，不要整机', 'auto', original='必须使用RTX 5090'))
    assert detail['intent']['scope'] == 'single' and detail['intent']['roles'] == ['gpu']


def test_only_explicit_reference_binds_previous_selected_component():
    from app.services.requirements import bind_referenced_components
    anchor = SimpleNamespace(selected=[SimpleNamespace(canonical_name='GeForce RTX 5070', claims=[
        SimpleNamespace(claim=SimpleNamespace(field_key='gpu.vram_gib'))])])
    assert '必须使用 GeForce RTX 5070' in bind_referenced_components('给这张显卡配一套主机', '当前要求', anchor)
    assert bind_referenced_components('推荐一套游戏主机', '当前要求', anchor) == '当前要求'
    assert bind_referenced_components('这张显卡换成 RTX 5090', '当前要求', anchor) == '当前要求'


def test_series_followup_cannot_silently_drop_whole_build_scope(monkeypatch):
    monkeypatch.setattr(request_analysis, 'get_settings', lambda: Settings(_env_file=None,
        database_url='postgresql+psycopg://u:p@localhost/db', agent_b_base_url='http://offline.invalid/v1'))
    async def complete(messages, **kwargs):
        return SimpleNamespace(content=json.dumps(dict(requires_database=True, continues_build=True,
            task='recommend', scope='single', roles=['gpu'], priority='balanced', workload='gaming', subject='')))
    monkeypatch.setattr(request_analysis, 'complete_chat', complete)
    _, continuation, detail = asyncio.run(request_analysis.analyze_request(
        '我想要50系NV显卡的配置', 'auto', original='推荐一套打游戏的配置'))
    assert continuation and detail['intent']['scope'] == 'bundle'
    assert detail['intent']['roles'] == ['cpu', 'gpu']


@pytest.mark.parametrize('value,supported', [(12, True), (999, False)])
def test_introduction_only_displays_truth_supported_specs(monkeypatch, value, supported):
    from app.services import product_info
    from test_truth_verification import FakeRepository, CANDIDATE_ID, EVIDENCE_ID
    entity = SimpleNamespace(id=CANDIDATE_ID, canonical_name='Canonical GPU', entity_type='hardware')
    evidence = SimpleNamespace(id=EVIDENCE_ID, normalized_value=value, unit_key='GiB', field_key='gpu.vram_gib')
    class Rows(list):
        def all(self): return self
    class Session:
        calls = 0
        def scalars(self, query):
            self.calls += 1
            return Rows([entity] if self.calls == 1 else [evidence])
        def execute(self, query): return Rows([('Source', 'https://example.com/spec', 'gpu.vram_gib')])
    monkeypatch.setattr(product_info, 'SqlAlchemyTruthRepository', lambda session: FakeRepository())
    entities, facts, sources = product_info.lookup_product(Session(), 'Canonical GPU')
    assert entities == [entity]
    assert bool(facts) == supported and bool(sources) == supported
    if supported:
        assert facts[0]['value'] == '12' and facts[0]['unit'] == 'GiB'


def test_aliases_and_negated_whole_machine_are_single_category_in_fallback():
    assert classify_request('推荐GPU显卡，不要整机') == 'single'
    assert requested_component_roles('推荐GPU显卡，不要整机') == {'gpu'}


def test_unpinned_discovery_prefers_specs_but_never_substitutes_a_pin():
    token = execution_intent.set(ExecutionIntent(scope='single', roles=['gpu']))
    try:
        assert 'fact_evidence' in discovery_queries('推荐游戏显卡', {'gpu'})[0]
        query = discovery_queries('指定 RTX 5050', {'gpu'})[0]
        assert 'fact_evidence' not in query and '5050' in query
    finally:
        execution_intent.reset(token)


@pytest.mark.parametrize('question,domain,roles,task', [
    ('我本地要部署量化模型的话，你给我推荐一个', 'ai_model', ['model'], 'recommend'),
    ('我有RTX 5070，推荐一个聊天模型', 'ai_model', ['model'], 'recommend'),
    ('我要的是模型，不是配置', 'hardware', ['cpu'], 'introduce'),
    ('我要买显卡来部署模型', 'ai_model', ['model'], 'recommend'),
    ('配一台电脑跑本地模型', 'ai_model', ['model'], 'recommend'),
])
def test_model_target_and_correction_do_not_become_hardware(monkeypatch, question, domain, roles, task):
    monkeypatch.setattr(request_analysis, 'get_settings', lambda: Settings(_env_file=None,
        database_url='postgresql+psycopg://u:p@localhost/db', agent_b_base_url='http://offline.invalid/v1'))
    async def complete(*args, **kwargs):
        return SimpleNamespace(content=json.dumps(dict(task=task, domain=domain, roles=roles, scope='single',
            priority='balanced',workload='local_ai',subject='',requires_database=True,continues_build=True)))
    monkeypatch.setattr(request_analysis, 'complete_chat', complete)
    _, continues, details = asyncio.run(request_analysis.analyze_request(question,'auto',original='推荐一套游戏配置'))
    plan = ExecutionIntent.model_validate(details['intent'])
    if '买显卡' in question:
        assert plan.domain == 'hardware' and plan.roles == ['gpu']
    elif '配一台电脑' in question:
        assert plan.domain == 'hardware' and plan.scope == 'bundle'
    else:
        assert plan.domain == 'ai_model' and plan.roles == ['model'] and plan.task == 'recommend'
        assert not continues
        token = execution_intent.set(plan)
        try:
            assert requested_component_roles(question) == {'model'}
            assert classify_request(question) == 'single'
            assert 'gpu_catalog' not in discovery_queries(question, {'model'})[0]
        finally:
            execution_intent.reset(token)


def test_catalogue_specs_are_not_falsely_marked_verified():
    from app.services.product_info import catalogue_specs
    from app.models.truth_v3 import GpuSpec
    entity = SimpleNamespace(id='gpu',entity_type='hardware')
    session = SimpleNamespace(get=lambda model, key: SimpleNamespace(vram_gib=32,board_power_w=575,memory_type='GDDR7') if model is GpuSpec else None)
    facts = catalogue_specs(session,entity)
    assert {f['field'] for f in facts} == {'gpu.vram_gib','gpu.board_power_w','gpu.memory_type'}
    assert all(f['status']=='catalogue_only' and f['evidence_ids']==[] for f in facts)


def test_model_sql_preserves_quantization_memory_family_and_safety():
    from app.services.local_models import discovery_queries as queries
    from app.tools.validator import validate_sql, ValidationSuccess
    settings = Settings(_env_file=None,database_url='postgresql+psycopg://u:p@localhost/db')
    sql = queries('我有12GB显存，推荐Qwen3-8B Q8_0量化模型')[0]
    assert "qualifier_key='Q8_0'" in sql and 'a.value_number < 12.0' in sql
    assert "ILIKE '%Qwen3%8B%'" in sql and "quantization_method='Q8_0'" in sql
    assert isinstance(validate_sql(sql,settings), ValidationSuccess)
    assert isinstance(validate_sql(queries('推荐量化模型')[0],settings), ValidationSuccess)
    from app.services.requirements import continue_request
    effective = continue_request('换成Q8_0，其他不变', '推荐Qwen3-8B Q4_K_M聊天模型，12GB显存', True)
    sql = queries(effective)[0]
    assert "qualifier_key='Q8_0'" in sql and "ILIKE '%Qwen3%8B%'" in sql
    assert 'a.value_number < 12.0' in sql
    effective = continue_request('推荐Qwen3-8B量化模型', '推荐更小一点的模型', True)
    assert '4500000000' not in queries(effective)[0]


def test_hardware_candidate_cannot_pass_model_target_gate():
    from test_truth_verification import FakeRepository, recommendation, fact
    from app.verification import TruthVerifier
    from app.services.request_scope import check_requested_categories
    token = execution_intent.set(ExecutionIntent(domain='ai_model', roles=['model']))
    try:
        checked = TruthVerifier(FakeRepository()).verify(recommendation([fact()]))
        result = check_requested_categories(checked, '我有RTX 5070，推荐模型', None)
        assert not result.candidates[0].candidate_valid
        assert 'requested_category_mismatch' in result.candidates[0].candidate_reason_codes
    finally:
        execution_intent.reset(token)


def test_classifier_failure_keeps_explicit_model_target(monkeypatch):
    monkeypatch.setattr(request_analysis, 'get_settings', lambda: Settings(_env_file=None,
        database_url='postgresql+psycopg://u:p@localhost/db',agent_b_base_url='http://offline.invalid/v1'))
    async def invalid(*args, **kwargs): return SimpleNamespace(content='invalid')
    monkeypatch.setattr(request_analysis,'complete_chat',invalid)
    mode,continues,detail = asyncio.run(request_analysis.analyze_request(
        '我本地要部署量化模型的话，你给我推荐一个','auto',original='推荐游戏配置'))
    assert mode == 'fusion' and not continues
    assert detail['intent']['domain']=='ai_model' and detail['intent']['roles']==['model']


def test_product_intro_without_a_reference_does_not_reuse_previous_cpu(monkeypatch):
    from app.services import product_info
    from test_fusion_service import FakeContextService,FakeSession
    from uuid import uuid4
    monkeypatch.setattr(product_info,'SessionLocal',FakeSession)
    monkeypatch.setattr(product_info,'conversation_context_service',FakeContextService())
    monkeypatch.setattr(product_info,'lookup_product',lambda *args: pytest.fail('Must not look up the stale CPU'))
    anchor=SimpleNamespace(selected=[SimpleNamespace(canonical_name='Ryzen 5 5600T')])
    result=asyncio.run(product_info.introduce_product('我要的是模型，不是配置',uuid4(),ExecutionIntent(task='introduce'),anchor=anchor))
    assert 'Ryzen' not in result['answer']


@pytest.mark.parametrize('sku,matching,rejected', [
    ('rtx 5070', ['GeForce RTX 5070', 'GeForce RTX5070'], ['GeForce RTX 5070 Ti', 'GeForce RTX5070Ti']),
    ('rtx 5090', ['GeForce RTX 5090'], ['GeForce RTX 5090D']),
    ('i7-14700k', ['Core i7-14700K'], ['Core i7-14700KF']),
])
def test_pair_discovery_uses_exact_sku_before_candidate_limit(sku, matching, rejected):
    import re
    from app.services.hardware_intent import _sku_sql_filter
    from app.tools.validator import validate_sql, ValidationSuccess
    clause = _sku_sql_filter(sku, 'c')
    # PostgreSQL ARE lookahead also works in Python after POSIX class conversion.
    pattern = clause.split("~* '")[1][:-1].replace('[[:space:]]', r'\s').replace('[[:alnum:]]', '[a-z0-9]')
    assert all(re.search(pattern, name, re.I) for name in matching)
    assert all(not re.search(pattern, name, re.I) for name in rejected)
    settings = Settings(_env_file=None, database_url='postgresql+psycopg://u:p@localhost/db')
    assert isinstance(validate_sql('SELECT c.name FROM agent_catalog.gpu_catalog c WHERE ' + clause + ' LIMIT 3', settings), ValidationSuccess)
    queries = discovery_queries('推荐游戏配置，指定 ' + sku, {'cpu', 'gpu'})
    assert all('~*' in q for q in queries)
