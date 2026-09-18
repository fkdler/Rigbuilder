import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest

from app.core.config import Settings
from app.inference import context_window, servers
from app.inference.profiles import ModelProfile, build_profiles
from app.agents.context_budget import compute_prompt_budget
from app.agents.protocol.contracts import Message
from app.context.service import ConversationContextService


def settings():
    return Settings(_env_file=None, database_url='postgresql+psycopg://u:p@localhost/db',
        llm_test_model_ids='agent-a,agent-b',agent_a_model='agent-a',agent_b_model='agent-b',
        agent_a_base_url='http://a.invalid/v1',agent_b_base_url='http://b.invalid/v1',
        agent_default_context_size=16384,llm_context_window_tokens=32768)


@pytest.fixture(autouse=True)
def isolated_windows():
    context_window._windows.clear()
    yield
    context_window._windows.clear()


def test_live_window_updates_each_endpoint_and_history_uses_smallest(monkeypatch):
    current = {'a': 16384, 'b': 8192}
    calls = []
    class Client:
        async def get(self, url, **kwargs):
            key = 'a' if 'a.invalid' in url else 'b'
            calls.append(key)
            return httpx.Response(200,json={'model_alias':'agent-'+key,'total_slots':4,
                'default_generation_settings':{'n_ctx':current[key]}})
    @asynccontextmanager
    async def client(timeout): yield Client()
    monkeypatch.setattr(context_window,'inference_client',client)
    s=settings()
    first=asyncio.run(context_window.refresh_context_windows(s))
    assert [p.context_size for p in first]==[16384,8192]  # already per slot; do not divide by 4
    assert all(p.context_source=='props' for p in first)
    assert [p.context_size for p in build_profiles(s)]==[16384,8192]
    assert ConversationContextService._history_token_budget(s)==int((8192-2048)*.7)
    asyncio.run(context_window.refresh_context_windows(s))
    assert len(calls)==2
    current['b']=4096
    updated=asyncio.run(context_window.refresh_context_windows(s,force=True))
    assert updated[1].context_size==4096 and updated[0].context_size==16384
    assert s.agent_default_context_size==16384
    budget=compute_prompt_budget(updated[1],[],exact_prompt_tokens=3000)
    assert budget.exceeds and budget.completion_limit(8192)==1096


@pytest.mark.parametrize('payload', [None,{}, {'n_ctx':65536},
    {'default_generation_settings':{'n_ctx':True}}, {'default_generation_settings':{'n_ctx':0}},
    {'model_alias':'different','default_generation_settings':{'n_ctx':32768}},
    {'default_generation_settings':{'n_ctx':'16384'}}])
def test_props_cannot_confuse_training_or_other_model_context(payload):
    assert context_window.context_from_props(payload,'agent-a') is None


def test_unavailable_props_falls_back_and_expires_stale_value(monkeypatch):
    class Client:
        async def get(self, *args, **kwargs): raise httpx.ConnectError('offline')
    @asynccontextmanager
    async def client(timeout): yield Client()
    monkeypatch.setattr(context_window,'inference_client',client)
    p=build_profiles(settings())[0]
    p=replace(p,context_size=65536,context_source='props')
    resolved=asyncio.run(context_window.resolve_context_window(p,settings(),force=True))
    assert resolved.context_size==16384 and resolved.context_source=='settings'


def test_exact_count_renders_tools_before_tokenizing_and_preserves_special_tokens(monkeypatch):
    calls=[]
    class Client:
        async def post(self,url,**kwargs):
            calls.append((url,kwargs['json']))
            if url.endswith('/apply-template'): return httpx.Response(200,json={'prompt':'<bos> rendered tools <assistant>'})
            return httpx.Response(200,json={'tokens':[1,2,3,4,5]})
    @asynccontextmanager
    async def client(timeout): yield Client()
    monkeypatch.setattr(servers,'inference_client',client)
    tools=[{'type':'function','function':{'name':'lookup'}}]
    n=asyncio.run(servers.count_prompt_tokens(build_profiles(settings())[0], [{'role':'user','content':'中文'}],settings(),wire_tools=tools))
    assert n==5 and calls[0][1]['tools']==tools
    assert calls[0][1]['chat_template_kwargs']=={'enable_thinking':False}
    assert calls[1][1]=={'content':'<bos> rendered tools <assistant>','add_special':False,'parse_special':True}
    budget=compute_prompt_budget(build_profiles(settings())[0],[Message(role='user',content='x'*50000)],exact_prompt_tokens=n)
    assert not budget.exceeds  # server count overrides the fallback estimate


def test_missing_template_is_not_mislabeled_exact_json_count(monkeypatch):
    class Client:
        async def post(self,url,**kwargs):
            assert url.endswith('/apply-template')
            return httpx.Response(404)
    @asynccontextmanager
    async def client(timeout): yield Client()
    monkeypatch.setattr(servers,'inference_client',client)
    assert asyncio.run(servers.count_prompt_tokens(build_profiles(settings())[0],[],settings())) is None
