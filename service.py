#!/usr/bin/env python3
"""Start/stop the independent Lens process. No Codex CLI, model, or config changes.
Only explicit commands write Lens state. Importing this module has no side effects.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import webbrowser
# Running from a source checkout must not create a __pycache__ as a side effect.
sys.dont_write_bytecode = True
import lens


def state_data(path: Path) -> dict | None:
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        url=urlsplit(value['url'])
        if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port: return None
        if not isinstance(value.get('token'),str): return None
        return value
    except (OSError,ValueError,KeyError,TypeError): return None


def contact(state: dict, stop: bool = False) -> dict | None:
    url=urlsplit(state['url']); base=f'http://127.0.0.1:{url.port}'
    headers={'Cookie':'lens_token='+state['token']}
    if stop: headers['Authorization']='Bearer '+state['token']
    req=urllib.request.Request(base+('/api/shutdown' if stop else '/api/health'),headers=headers,method='POST' if stop else 'GET')
    # Never use machine-wide proxy settings for this loopback-only control call.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req,timeout=30) as r: return json.load(r)
    except (OSError,ValueError,urllib.error.URLError): return None


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='Token Lens 独立后台进程管理（不修改 Codex）')
    parser.add_argument('action',choices=('start','stop','status','open'),nargs='?',default='start')
    parser.add_argument('--state-dir',default=str(Path.home()/'.codex-token-lens'))
    parser.add_argument('--home',action='append')
    parser.add_argument('--config')
    parser.add_argument('--db')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--full-history-parse',action='store_true')
    parser.add_argument('--report-events',type=int,default=20000)
    parser.add_argument('--max-db-mib',type=int,default=2048)
    parser.add_argument('--min-free-mib',type=int,default=1024)
    parser.add_argument('--interval',type=float,default=15)
    parser.add_argument('--timezone',default='local')
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args(argv)
    try:
        roots=lens.make_roots(args)
        lens.writable_path(lens.BASE,roots)
        directory=lens.writable_path(args.state_dir,roots)
        ready=lens.writable_path(directory/'runtime.json',roots)
        logfile=lens.writable_path(directory/'service.log',roots)
        database=lens.writable_path(args.db or directory/'usage.sqlite3',roots)
    except (OSError,ValueError,KeyError) as ex: parser.error(str(ex))
    state=state_data(ready)
    health=contact(state) if state else None
    running=bool(health and health.get('pid')==state.get('pid') and health.get('codex_access')=='read_only')
    if args.action=='status':
        print(json.dumps({'running':running,'health':health if running else None,'state_dir':str(directory)},ensure_ascii=False,indent=2))
        return 0 if running else 1
    if args.action=='stop':
        if not running:
            print('未发现可认证的 Lens 实例；没有终止任何进程。'); return 0
        result=contact(state,stop=True)
        if result and result.get('stopping'):
            print('已请求停止 Token Lens；Codex 不受影响。'); return 0
        print('停止请求未成功；没有使用 PID 强制结束其他进程。',file=sys.stderr); return 1
    if running:
        print(f'独立实例已运行：PID {state["pid"]}\n{state["url"]}',flush=True)
        if not args.no_browser: webbrowser.open(state['url'])
        return 0
    if args.action=='open':
        print('服务未运行。执行 python3 service.py start 启动。',file=sys.stderr); return 1
    if not 0<=args.port<=65535 or not 1<=args.interval<=3600: parser.error('端口或间隔无效')
    directory.mkdir(parents=True,exist_ok=True)
    try: directory.chmod(0o700)
    except OSError: pass
    # Explicit start replaces only stale Lens readiness metadata, never user logs.
    if ready.exists(): ready.unlink()
    cmd=[sys.executable,'-B',str(lens.BASE/'lens.py'),'--no-browser','--ready-file',str(ready),'--db',str(database),'--port',str(args.port),'--interval',str(args.interval),'--timezone',args.timezone]
    cmd+=['--report-events',str(args.report_events),'--max-db-mib',str(args.max_db_mib),'--min-free-mib',str(args.min_free_mib)]
    if args.full_history_parse: cmd+=['--full-history-parse']
    if args.config: cmd+=['--config',str(Path(args.config).expanduser().resolve())]
    for home in args.home or []: cmd+=['--home',str(Path(home).expanduser().resolve())]
    flags={}
    if os.name=='nt': flags['creationflags']=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP
    else: flags['start_new_session']=True
    fd=os.open(str(logfile),os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'ab',buffering=0) as output:
        child=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=output,stderr=output,cwd=str(lens.BASE),close_fds=True,**flags)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        state=state_data(ready)
        if state and state.get('pid')==child.pid and contact(state):
            print(f'已启动独立 Token Lens：PID {child.pid}\n{state["url"]}\n状态目录：{directory}\n数据库：{database}',flush=True)
            if not args.no_browser: webbrowser.open(state['url'])
            return 0
        if child.poll() is not None:
            print(f'启动失败（退出码 {child.returncode}），请检查本工具日志：{logfile}',file=sys.stderr); return 1
        time.sleep(.2)
    print(f'独立进程 PID {child.pid} 已创建，首次扫描仍在进行。稍后执行 service.py status；日志：{logfile}',flush=True)
    return 0

if __name__=='__main__': raise SystemExit(main())
