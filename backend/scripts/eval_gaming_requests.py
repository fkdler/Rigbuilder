"""Live HTTP evaluation; sign in via prompted/piped password, save jobs and traces.
Creates test conversations which can be deleted with --cleanup after capture.
"""
import argparse, getpass, json, sys, time
from pathlib import Path
import httpx

CASES = [
    '给我一套比较好的打游戏的配置，我希望CPU是9800X3D',
    '配一台1440p游戏主机，CPU必须是9800X3D，不要笔记本显卡，也不要RTX 4090，没有价格就明确说不知道。',
    '给我一套4K游戏配置，CPU指定9800X3D，显卡必须RTX 5090，不能换型号，不要编造帧率。',
    '配一台打游戏的电脑，CPU必须是不存在的99999X3D，查不到就说明，不要擅自换成别的CPU。',
    '预算8000元含显示器，CPU必须9800X3D，2K玩游戏且安静省电；没有价格和噪声证据请列出缺口，不要保证总价。',
    '配一套普通办公电脑，不玩游戏，安静省电。',
]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--username', default='111@111.com')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=len(CASES))
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--cleanup', action='store_true')
    args = parser.parse_args()
    password = getpass.getpass('Password: ') if sys.stdin.isatty() else sys.stdin.readline().strip()
    records=[]
    with httpx.Client(base_url=args.base_url, timeout=20) as c:
        response=c.post('/api/auth/login',json={'username':args.username,'password':password})
        response.raise_for_status()
        c.headers['Authorization']='Bearer '+response.json()['token']
        try:
            for message in CASES[args.start:args.start+args.limit]:
                start=time.perf_counter()
                response=c.post('/api/query/jobs',json={'message':message,'mode':'auto'})
                response.raise_for_status(); job=response.json()
                while time.perf_counter()-start < 240:
                    response=c.get('/api/query/jobs/'+job['id']); response.raise_for_status(); job=response.json()
                    if job['status'] in ('completed','failed','cancelled'): break
                    time.sleep(1)
                trace=c.get('/api/query/jobs/'+job['id']+'/trace').json()
                payload = job.get('result') or {}
                build = (payload.get('presentation') or {}).get('core_build') or {}
                core = build.get('core', [])
                from app.services.hardware_intent import allows_candidate
                from app.services.routing import requested_component_roles
                unknown = '99999X3D' in message
                checks = {
                    'terminal': job['status'] == 'completed',
                    'scope': (payload.get('verification') == 'not_verified' and not core) if unknown else requested_component_roles(message).issubset({c['role'] for c in core}),
                    'sku_constraints': all(allows_candidate(message, c['role'], c['name']) for c in core),
                    'no_mobile_gpu': not any('laptop' in c['name'].casefold() or 'a370m' in c['name'].casefold() for c in core),
                    'budget_gap': '预算' not in message or any('预算' in c for c in (payload.get('presentation') or {}).get('caveats', [])),
                }
                record={'message':message,'seconds':round(time.perf_counter()-start,2),'checks':checks,'job':job,'trace':trace}
                records.append(record)
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
                payload=job.get('result') or {}
                print(json.dumps({'message':message,'status':job['status'],'seconds':record['seconds'],
                    'checks':checks,'kind':payload.get('kind'),'core':((payload.get('presentation') or {}).get('core_build') or {}).get('core',[]),
                    'caveats':(payload.get('presentation') or {}).get('caveats',[]),
                    'answer':payload.get('answer'),'error':job.get('error_message')},ensure_ascii=False),flush=True)
                if args.cleanup and job.get('conversation_id'):
                    c.delete('/api/conversations/'+job['conversation_id']).raise_for_status()
        finally:
            c.post('/api/auth/logout')
    if any(not all(record['checks'].values()) for record in records):
        raise SystemExit(1)

if __name__=='__main__': main()
