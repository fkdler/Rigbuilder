import asyncio
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services import request_analysis as routing
from app.services.hardware_intent import allows_candidate, discovery_queries
from app.services.requirements import continue_request
from app.services.routing import resolve_query_mode, requested_component_roles


def test_series_followup_preserves_gaming_and_build_scope():
    question = '我想要50系NV显卡的配置'
    assert resolve_query_mode(question, 'auto', has_recommendation=True) == 'fusion'
    effective = continue_request(question, '给我推荐一套打游戏的配置', True)
    assert requested_component_roles(effective) == {'cpu', 'gpu'}
    queries = discovery_queries(effective, {'cpu', 'gpu'})
    assert len(queries) == 2
    assert all("ILIKE '%rtx 50__%'" in q for q in queries)
    assert allows_candidate(effective, 'gpu', 'NVIDIA GeForce RTX 5070')
    assert not allows_candidate(effective, 'gpu', 'NVIDIA GeForce RTX 4070')
    assert not allows_candidate(effective, 'gpu', 'AMD Radeon RX 9070')


def test_new_pin_overrides_old_and_guides_cpu_discovery():
    effective = continue_request('显卡必须换成 RTX 5090', '推荐一套游戏配置，指定 RTX 4070', True)
    assert allows_candidate(effective, 'gpu', 'GeForce RTX 5090')
    assert not allows_candidate(effective, 'gpu', 'GeForce RTX 4070')
    cpu_query = discovery_queries(effective, {'cpu', 'gpu'})[0]
    assert "gp.name ILIKE '%rtx%5090%'" in cpu_query
    assert 'gp.index_100 <= p.index_100 * b.ratio_max' in cpu_query


@pytest.mark.parametrize('reply,question,expected', [
    ('{"requires_database":false,"continues_build":false}', '我想要50系NV显卡的配置', 'fusion'),
    ('{"requires_database":true,"continues_build":false}', '这个新品上市了吗', 'fusion'),
    ('{"requires_database":false,"continues_build":false}', '你好', 'chat'),
    ('not json', '这个新品上市了吗', 'fusion'),
])
def test_analysis_is_bounded_and_cannot_downgrade(monkeypatch, reply, question, expected):
    settings = Settings(_env_file=None, database_url='postgresql+psycopg://u:p@localhost/db',
                        llm_test_model_ids='weak,strong', agent_b_model='strong',
                        agent_b_base_url='http://offline.invalid/v1')
    monkeypatch.setattr(routing, 'get_settings', lambda: settings)
    async def complete(messages, **kwargs):
        assert kwargs['model_id'] == 'strong'
        assert kwargs['base_url'] == 'http://offline.invalid/v1'
        assert kwargs['response_schema']
        return SimpleNamespace(content=reply)
    monkeypatch.setattr(routing, 'complete_chat', complete)
    assert asyncio.run(routing.analyze_request(question, 'auto'))[0] == expected


def test_explicit_fast_skips_analysis(monkeypatch):
    monkeypatch.setattr(routing, 'get_settings', lambda: SimpleNamespace(routing_analysis_enabled=True))
    assert asyncio.run(routing.analyze_request('推荐配置', 'chat'))[0] == 'chat'


def test_single_pin_still_plans_database_lookup():
    assert discovery_queries('指定 RTX 5090', {'gpu'})
    assert continue_request('改为办公配置', '推荐游戏配置', True) == '改为办公配置'


@pytest.mark.parametrize('phrase', ['不要查数据库', '不查数据库', '别查库', '无需查询数据库'])
def test_opt_out_variants_skip_model(monkeypatch, phrase):
    monkeypatch.setattr(routing, 'get_settings', lambda: SimpleNamespace(routing_analysis_enabled=True))
    assert asyncio.run(routing.analyze_request(phrase + '，解释一下这套配置', 'auto', original='推荐游戏配置'))[0] == 'chat'


def test_concept_after_build_does_not_inherit_hardware_request():
    assert resolve_query_mode('什么是显卡？', 'auto', has_recommendation=True) == 'chat'
    assert not routing.is_hardware_followup('什么是显卡？')
    assert resolve_query_mode('什么是显卡？推荐一款', 'auto', has_recommendation=True) == 'fusion'


def test_same_workload_keeps_other_pins():
    effective = continue_request('还是打游戏，但显卡换成RTX 5070', '推荐一套游戏配置，指定RTX 5090和Core i7-14700K', True)
    assert 'i7-14700K' in effective
    assert '5090' not in effective
    assert requested_component_roles(effective) == {'cpu', 'gpu'}


def test_routing_detail_survives_sse_sanitization():
    from app.services.jobs import QueryJobManager
    detail = {'source': 'model_and_rules', 'model': 'agent-b', 'requires_database': True,
              'continues_build': True, 'model_requires_database': False, 'model_continues_build': True}
    safe = QueryJobManager._sanitize_event({'event_type': 'routing_completed', 'detail': {**detail, 'prompt': 'private'}})
    assert safe['detail'] == detail


def test_intel_canonical_name_matches_pin_without_matching_kf():
    assert allows_candidate('指定Core i7-14700K', 'cpu', 'Intel Core i7-14700K')
    assert not allows_candidate('指定Core i7-14700K', 'cpu', 'Core i7-14700KF')


def test_catalogue_miss_preserves_request_but_not_verified_anchor():
    from app.context.anchor import load_previous_build_request, anchor_from_payload
    row = SimpleNamespace(resolved_mode='fusion', request_payload={'effective_message': '游戏配置，指定RTX 9999'},
                          result_payload={'kind': 'chat_fallback'})
    session = SimpleNamespace(scalar=lambda _: row)
    assert load_previous_build_request(session, '00000000-0000-0000-0000-000000000001') == '游戏配置，指定RTX 9999'
    assert anchor_from_payload(row.result_payload, '') is None
    row.resolved_mode = 'chat'
    assert load_previous_build_request(session, '00000000-0000-0000-0000-000000000001') is None


def test_series_discovery_does_not_match_3050_memory_suffix():
    import sqlite3
    from app.services.hardware_intent import _gpu_sql_filter
    # SQLite LIKE has the same wildcard semantics as PostgreSQL ILIKE here.
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE candidates (name TEXT)')
        db.executemany('INSERT INTO candidates VALUES (?)', [('GeForce RTX 3050 6GB',), ('GeForce RTX 5070 Ti',)])
        names = db.execute('SELECT c.name FROM candidates c WHERE ' + _gpu_sql_filter('要50系NV显卡', 'c').replace('ILIKE', 'LIKE')).fetchall()
    assert names == [('GeForce RTX 5070 Ti',)]
