"""Live HTTP/SSE routing replay; creates an isolated ordinary test account.

Run from backend with --output PATH. Does not modify catalogue data.
"""
import argparse
import json
import secrets
import time
from pathlib import Path
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--questions', type=Path, help='Optional JSON array of consecutive user messages')
    args = parser.parse_args()
    records = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=300) as client:
        response = client.post('/api/auth/register', json={'username': 'routing-' + uuid4().hex[:12], 'password': secrets.token_urlsafe(24)})
        response.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + response.json()['token']
        conversation = None
        questions = ['你好，你能做什么？', '推荐一套适合打3A游戏的电脑配置', '我想要50系NV显卡的配置', '显卡必须换成 RTX 5090', '换成 RTX 5070，其他要求不变', '改为普通办公配置，不玩游戏，不要独显', '不要查询数据库，解释一下什么是显存']
        if args.questions:
            questions = json.loads(args.questions.read_text(encoding='utf-8-sig'))
        try:
            for message in questions:
                start = time.monotonic()
                response = client.post('/api/query/jobs', json={'message': message, 'conversation_id': conversation, 'mode': 'auto'})
                response.raise_for_status()
                job_id = response.json()['id']
                print(json.dumps({'started': message, 'job_id': job_id}, ensure_ascii=False), flush=True)
                events = []
                with client.stream('GET', f'/api/query/jobs/{job_id}/events') as stream:
                    stream.raise_for_status()
                    for line in stream.iter_lines():
                        if line.startswith('data:'):
                            event = json.loads(line[5:])
                            events.append({'received_seconds': round(time.monotonic()-start, 3), **event})
                response = client.get(f'/api/query/jobs/{job_id}')
                response.raise_for_status()
                job = response.json()
                conversation = job['conversation_id']
                trace = client.get(f'/api/query/jobs/{job_id}/trace').json()
                record = {'message': message, 'seconds': round(time.monotonic()-start, 3), 'job': job, 'events': events, 'trace': trace}
                records.append(record)
                args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
                live = [e for e in events if e['event_type'] == 'llm_usage']
                print(json.dumps({'finished': message, 'seconds': record['seconds'], 'status': job['status'], 'mode': job['resolved_mode'], 'kind': (job.get('result') or {}).get('kind'), 'live_events': len(live), 'max_live_tokens': max((e['detail']['completion_tokens'] for e in live), default=0)}, ensure_ascii=False), flush=True)
        finally:
            client.post('/api/auth/logout')


if __name__ == '__main__':
    main()
