"""Read-only model classification and raw streaming-counter diagnostics."""
import asyncio
import json
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings
from app.services.request_analysis import analyze_request


async def main():
    settings = get_settings()
    output = {'streams': [], 'routing': []}
    async with httpx.AsyncClient(trust_env=False, timeout=60) as client:
        for model, url in [('agent-a', settings.agent_a_base_url), ('agent-b', settings.agent_b_base_url)]:
            start = time.monotonic()
            frames = []
            async with client.stream('POST', url + '/chat/completions', json={
                'model': model, 'messages': [{'role': 'user', 'content': '用中文详细解释显存、内存、缓存的区别。'}],
                'max_tokens': 200, 'stream': True, 'stream_options': {'include_usage': True},
                'timings_per_token': True, 'chat_template_kwargs': {'enable_thinking': False},
            }) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith('data:') and line[5:].strip() != '[DONE]':
                        chunk = json.loads(line[5:])
                        frames.append({'seconds': round(time.monotonic()-start, 3), 'usage': chunk.get('usage'), 'timings': chunk.get('timings'), 'finish_reason': [c.get('finish_reason') for c in chunk.get('choices', [])]})
            output['streams'].append({'model': model, 'frames': frames})
        for message in ['什么是显卡？', '谢谢', '不要查数据库，解释一下这套配置', '还是打游戏，但显卡换成RTX 5070', '改成普通办公，不玩游戏', '只换CPU，显卡保持不变']:
            result = await analyze_request(message, 'auto', original='推荐一套游戏配置，指定RTX 5090和Core i7-14700K')
            output['routing'].append({'message': message, 'result': result})
            print(json.dumps(output['routing'][-1], ensure_ascii=False), flush=True)
    target = Path('../.local-logs/routing-stream-probe.json')
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(target)


if __name__ == '__main__':
    asyncio.run(main())
