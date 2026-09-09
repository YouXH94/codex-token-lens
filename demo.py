#!/usr/bin/env python3
"""Generate clearly marked synthetic data; never reads the real ~/.codex directory."""
import json,random,shutil,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from lens import Store,BASE,FIELDS,render_html

def build():
    rng=random.Random(94)
    with tempfile.TemporaryDirectory() as td:
        home=Path(td)/'sample-codex';(home/'sessions').mkdir(parents=True)
        root_labels=['sample-app','sample-dashboard','sample-tool']
        start=datetime(2026,8,12,0,tzinfo=timezone.utc)
        for n in range(112):
            dt=start+timedelta(days=n%28,hours=(n//28)*5+2)
            sid=f'demo-session-{n:04d}';model='gpt-6-astra' if dt.day>=4 and dt.month==9 and n%3==0 else 'gpt-5.6-sol'
            child=n>0 and n%5==0;source={'subagent':{'thread_spawn':{'parent_thread_id':f'demo-session-{max(0,n-1):04d}'}}} if child else ('vscode' if n%4==0 else 'cli')
            rows=[{'timestamp':dt.isoformat(),'type':'session_meta','payload':{'id':sid,'timestamp':dt.isoformat(),'cwd':'/demo/projects/'+root_labels[n%3],'source':source,'model_provider':'openai','cli_version':'0.144.0'}}]
            if child:rows.append({'timestamp':dt.isoformat(),'type':'event_msg','payload':{'type':'task_started','turn_id':sid+'-turn'}})
            rows += [{'timestamp':dt.isoformat(),'type':'turn_context','payload':{'model':model,'effort':'high','turn_id':sid+'-turn'}},{'timestamp':dt.isoformat(),'type':'event_msg','payload':{'type':'thread_settings_applied','thread_settings':{'service_tier':'priority' if n%7==0 else 'default'}}}]
            cum={k:0 for k in FIELDS}
            for j in range(rng.randint(5,12)):
                I=rng.randint(23000,145000);C=int(I*rng.uniform(.55,.94));O=rng.randint(900,9000);R=int(O*rng.uniform(.45,.85))
                last=dict(zip(FIELDS,[I,C,O,R,I+O]));cum={k:cum[k]+last[k] for k in FIELDS}
                event={'timestamp':(dt+timedelta(minutes=5*(j+1))).isoformat(),'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':cum.copy(),'last_token_usage':last,'model_context_window':1048576}}}
                if n==37 and j==0:event['payload']['rate_limits']={'limit_id':'codex','limit_name':'Codex · 演示快照','primary':{'used_percent':42.5,'window_minutes':300,'resets_at':1788872400},'secondary':{'used_percent':63.2,'window_minutes':10080,'resets_at':1789257600}}
                if n==34:
                    event['payload']['info']['total_token_usage'].pop('reasoning_output_tokens')
                    event['payload']['info']['last_token_usage'].pop('reasoning_output_tokens')
                rows.append(event)
                if j==2: rows.append(event.copy())
            (home/'sessions'/f'{sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
        store=Store(Path(td)/'db.sqlite3',[{'path':str(home),'label':'演示本机'}],'UTC');store.scan();r=store.report()
        r['runtime']={'mode':'independent','codex_access':'read_only','database':'~/.codex-token-lens/usage.sqlite3','model_calls':False};r['poll_seconds']=5
        titles=['市场系统交互重构','卡牌技能与战斗构筑','用量面板设计与联调','综合联调场景整理','专家系统检索优化','素材拆分与前端还原','消息桥接稳定性修复','工程资料分类与导出']
        for i,session in enumerate(r['sessions']):
            session['demo_title']=titles[i%len(titles)]
            session['root']='/demo/.codex'
        r['demo']=True;r['generated_at']='2026-09-08T18:30:00+00:00'
        for e in r['events']:e['root']='/demo/.codex'
        for q in r['quota_snapshots']:q['root']='/demo/.codex'
        r['scan']['roots']=[{'path':'/demo/.codex','label':'演示本机','exists':True,'files':112}]
        r['scan']['at']=r['generated_at']
        (BASE/'preview.html').write_text(render_html(r),encoding='utf-8')
        print(f'Created preview.html with {len(r["events"])} synthetic events.')
if __name__=='__main__':build()
