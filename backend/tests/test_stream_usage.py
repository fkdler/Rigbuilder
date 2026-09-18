"""Provider counters, tool assembly, and error cleanup at the transport boundary."""
import asyncio
import json

import httpx
import pytest

from app.services import stream_usage
from app.services.llm import LLMServiceError


def run_stream(chunks, monkeypatch, done=True):
    events = []
    clock = iter(range(100))
    monkeypatch.setattr(stream_usage.time, 'monotonic', lambda: next(clock, 100))
    wire = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks)
    if done:
        wire += 'data: [DONE]\n\n'
    def handle(request):
        body = json.loads(request.content)
        assert body['stream_options']['include_usage'] and body['timings_per_token']
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=wire)
    async def execute():
        token = stream_usage.usage_sink.set(events.append)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                return await stream_usage.read_completion_stream(client, 'http://test/completions',
                    {'model': 'agent-b'}, {}, per_token_timings=True)
        finally:
            stream_usage.usage_sink.reset(token)
    return execute, events


def chunk(delta=None, count=None, finish=None):
    value = {'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}]}
    if count is not None:
        value['timings'] = {'predicted_n': count}
    return value


def test_real_llama_frames_and_final_usage(monkeypatch):
    execute, events = run_stream([chunk({'content': '你'}, 1), chunk({'content': '好'}, 20),
        chunk(finish='stop'), {'choices': [], 'usage': {'completion_tokens': 21, 'prompt_tokens': 10}}], monkeypatch)
    result = asyncio.run(execute())
    assert result['choices'][0]['message']['content'] == '你好'
    assert result['usage']['completion_tokens'] == 21
    assert [e['detail']['completion_tokens'] for e in events] == [1, 20, None]
    assert events[-1]['event_type'] == 'llm_usage_finished'
    assert len({e['detail']['call_id'] for e in events}) == 1


def test_final_only_usage_is_not_live(monkeypatch):
    execute, events = run_stream([chunk({'content': 'ok'}), chunk(finish='stop'),
        {'choices': [], 'usage': {'completion_tokens': 99}}], monkeypatch)
    assert asyncio.run(execute())['usage']['completion_tokens'] == 99
    assert events == []


def test_tool_arguments_assemble_without_losing_usage(monkeypatch):
    execute, events = run_stream([
        chunk({'tool_calls': [{'index': 0, 'id': 'call_1', 'function': {'name': 'query_database', 'arguments': '{"sql":'}}]}, 1),
        chunk({'tool_calls': [{'index': 0, 'function': {'arguments': '"SELECT 1"}'}}]}, 12),
        chunk(finish='tool_calls')], monkeypatch)
    tool = asyncio.run(execute())['choices'][0]['message']['tool_calls'][0]
    assert tool == {'id': 'call_1', 'type': 'function', 'function': {'name': 'query_database', 'arguments': '{"sql":"SELECT 1"}'}}
    assert events[-1]['event_type'] == 'llm_usage_finished'


@pytest.mark.parametrize('tail', [[], [{'error': {'message': 'failed'}}]])
def test_failed_stream_clears_live_counter(monkeypatch, tail):
    execute, events = run_stream([chunk({'content': 'partial'}, 1), *tail], monkeypatch, done=False)
    with pytest.raises(LLMServiceError):
        asyncio.run(execute())
    assert events[-1]['event_type'] == 'llm_usage_finished'
