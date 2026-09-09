#!/usr/bin/env python3
"""Codex Token Lens 0.2.0 — read-only local rollout accounting. Python 3.10+."""
from __future__ import annotations
import gzip, shutil, heapq
from collections.abc import Mapping
import argparse, csv, hashlib, io, json, os, re, secrets, sqlite3, sys, threading, time, webbrowser
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

VERSION = '0.2.1-local'
BASE = Path(__file__).resolve().parent
FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')
RELEVANT = (b'session_meta', b'turn_context', b'token_count', b'thread_settings_applied', b'task_started', b'inter_agent_communication', b'turn.completed')
MAX_LINE = 16 * 1024 * 1024

def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def number(x):
    return x if type(x) is int and x >= 0 else None

def text(x):
    return str(x)[:2048] if isinstance(x, (str, int)) else None

def timestamp(s):
    try:
        d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None

def usage(d):
    if not isinstance(d, dict): return None
    result = {k: number(d.get(k)) for k in FIELDS}
    if result['cached_input_tokens'] is None:
        result['cached_input_tokens'] = number((d.get('input_tokens_details') or {}).get('cached_tokens')) if isinstance(d.get('input_tokens_details'), dict) else None
    if result['reasoning_output_tokens'] is None:
        result['reasoning_output_tokens'] = number((d.get('output_tokens_details') or {}).get('reasoning_tokens')) if isinstance(d.get('output_tokens_details'), dict) else None
    extra = {k: v for k, v in d.items() if k not in FIELDS and k.endswith('_tokens') and number(v) is not None}
    if extra: result['extra_counters'] = extra
    return result if any(result[k] is not None for k in FIELDS) else None

def quota(d):
    if not isinstance(d, dict): return None
    out = {k: text(d[k]) for k in ('limit_id', 'limit_name', 'plan_type') if k in d}
    for k in ('primary', 'secondary'):
        item = d.get(k)
        if isinstance(item, dict):
            out[k] = {a: b for a,b in item.items() if a in ('used_percent','window_minutes','resets_at') and type(b) in (float,int)}
    credits = d.get('credits')
    if isinstance(credits, dict):
        out['credits'] = {k: v for k,v in credits.items() if k in ('has_credits','unlimited','balance') and isinstance(v,(str,int,float,bool,type(None)))}
    return out or None

def sanitize(e):
    """Persist only whitelisted telemetry, never prompts, tool results or auth files."""
    if not isinstance(e, dict): return None
    p = e.get('payload')
    if not isinstance(p, dict): p = {}
    kind = e.get('type'); out = {'kind':kind, 'ts':text(e.get('timestamp'))}
    if kind == 'session_meta':
        out.update({k:text(p.get(k)) for k in ('id','timestamp','cwd','originator','cli_version','model_provider','forked_from_id','parent_thread_id')})
        source = p.get('source'); out['source'] = source if isinstance(source, str) else 'unknown'
        if isinstance(source, dict):
            if 'subagent' in source:
                out['source'] = 'subagent'
                a = source['subagent']
                if isinstance(a, dict):
                    for value in a.values():
                        if isinstance(value, dict) and value.get('parent_thread_id'):
                            out['parent_thread_id'] = text(value['parent_thread_id'])
                    out['agent_type'] = ','.join(str(k) for k in a.keys())[:128]
        return out
    if kind == 'turn_context':
        out.update({k:text(p.get(k)) for k in ('model','cwd','effort','reasoning_effort','model_provider','turn_id')})
        if 'service_tier' in p: out['service_tier'] = text(p.get('service_tier'))
        return out
    if kind in ('inter_agent_communication_metadata','inter_agent_communication'):
        out['trigger_turn'] = p.get('trigger_turn') is True
        return out
    if kind == 'event_msg':
        out['kind'] = p.get('type')
        if out['kind'] == 'token_count':
            info = p.get('info') or {}
            if not isinstance(info, dict): info = {}
            out['total'] = usage(info.get('total_token_usage'))
            out['last'] = usage(info.get('last_token_usage'))
            out['quota'] = quota(p.get('rate_limits'))
            out['model'] = text(p.get('model') or info.get('model'))
            out['context_window'] = number(info.get('model_context_window'))
            return out
        if out['kind'] == 'thread_settings_applied':
            s = p.get('thread_settings') or {}
            if not isinstance(s, dict): s = {}
            if 'service_tier' in s: out['service_tier'] = text(s.get('service_tier'))
            out['model'] = text(s.get('model'))
            return out
        if out['kind'] == 'task_started':
            out['turn_id'] = text(p.get('turn_id'))
            return out
    if kind == 'turn.completed':
        # Do not mix headless SDK aggregates with the canonical per-response rollout.
        return {'kind':'unsupported_headless','ts':out['ts']}
    return None

def tuple_usage(d):
    return tuple(d.get(k) for k in FIELDS) if d else None

def diff(a,b):
    return {k: a.get(k) - b.get(k) if a.get(k) is not None and b.get(k) is not None else None for k in FIELDS}

def tier(v):
    if v in ('default','standard'): return 'standard'
    if v in ('priority','fast'): return 'fast'
    return 'unknown'

def protected_paths(roots):
    """Read-only sources plus the standard Codex home, resolved through symlinks."""
    values = [Path.home()/'.codex'] + [Path(r['path']).expanduser() for r in roots]
    values += [p.parent for p in list(values) if p.name in ('sessions','archived_sessions')]
    if os.environ.get('CODEX_HOME'): values.append(Path(os.environ['CODEX_HOME']).expanduser())
    return {p.resolve() for p in values}

def writable_path(path, roots):
    """Fail before mkdir/open if a destination is inside a protected Codex tree.

    Existing symlink and multiply-linked destinations are rejected as well. This
    guards normal mistakes; it is not a sandbox against a hostile same-user race.
    """
    original = Path(path).expanduser()
    resolved = original.resolve()
    for root in protected_paths(roots):
        if resolved == root or root in resolved.parents:
            raise ValueError(f'只读保护：禁止写入 Codex 日志/配置目录：{resolved}')
    if original.is_symlink() or (resolved.exists() and resolved.stat().st_nlink > 1 and resolved.is_file()):
        raise ValueError(f'只读保护：写入目标不能是符号链接或多重硬链接：{original}')
    return resolved

def render_html(report=None):
    """Create an entirely offline, self-contained HTML snapshot."""
    html = (BASE/'static'/'index.html').read_text(encoding='utf-8')
    css = (BASE/'static'/'style.css').read_text(encoding='utf-8')
    html = html.replace('<link rel="stylesheet" href="/static/style.css">', '<style>'+css+'</style>')
    for name in ('format.js', 'app.js'):
        code = (BASE/'static'/name).read_text(encoding='utf-8')
        html = html.replace(f'<script src="/static/{name}"></script>', '<script>'+code+'</script>')
    if report is not None:
        data=json.dumps(report,ensure_ascii=False).replace('<', '\\u003c').replace('&', '\\u0026')
        html=html.replace('/*__EMBEDDED__*/', 'window.LENS_SNAPSHOT = '+data+';')
    return html

class DatabaseLock:
    """One Lens process per cache. Lock lives only for the owning process."""
    def __init__(self,path,roots):
        target=writable_path(str(path)+'.lock',roots)
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.file=os.fdopen(os.open(str(target),os.O_CREAT|os.O_RDWR,0o600),'a+b')
        try:
            if os.name=='nt':
                import msvcrt
                self.file.seek(0); self.file.write(b'0'); self.file.flush(); self.file.seek(0)
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError('该 Lens 数据库已有进程使用；请先通过 service.py stop 停止实例。')
    def close(self): self.file.close()

class SessionStreams(Mapping):
    """Keep only file metadata globally; load telemetry for one session at a time."""
    def __init__(self, store, groups, warnings):
        self.store, self.groups, self.warnings = store, groups, warnings
        self._parent_totals = {}
    def __iter__(self): return iter(sorted(self.groups,key=lambda sid:self.upper_bound(sid) if self.store.fast_window else max(f['mtime'] for f,m in self.groups[sid]),reverse=True))
    def upper_bound(self,sid):
        return max((self.store._time_ranges.get(f['path'],(None,None,None))[2] or 0) for f,m in self.groups[sid])
    def __len__(self): return len(self.groups)
    def __contains__(self, sid): return sid in self.groups
    def signature(self, sid):
        own=self.groups[sid]
        meta=own[0][1]; parent=meta.get('parent_thread_id') or meta.get('forked_from_id')
        rows=own+self.groups.get(parent,[])
        return tuple((f['path'],f['offset'],f['mtime'],f['present']) for f,m in rows)
    def parent_counters(self, sid):
        if sid not in self._parent_totals:
            values=set()
            with self.store.connect() as c:
                for f,meta in self.groups[sid]:
                    for row in c.execute('SELECT data FROM records WHERE path=? ORDER BY pos',(f['path'],)):
                        r=json.loads(row['data'])
                        if r['kind']=='token_count' and r.get('total'): values.add(tuple_usage(r['total']))
            self._parent_totals[sid]=values
        return self._parent_totals[sid]
    def __getitem__(self,sid):
        if sid not in self.groups: raise KeyError(sid)
        copies=[]
        with self.store.connect() as c:
            for f,meta in self.groups[sid]:
                rs=[]
                for row in c.execute('SELECT pos,data FROM records WHERE path=? ORDER BY pos',(f['path'],)):
                    r=json.loads(row['data']); r['pos']=row['pos']; rs.append(r)
                copies.append((f,rs,meta))
        copies.sort(key=lambda x:(len(x[1]),x[0]['present'],x[0]['size']),reverse=True)
        f,rs,meta=copies[0]; merged=rs
        if len(copies)>1:
            merged=list(rs); seen={digest({k:v for k,v in r.items() if k!='pos'}) for r in rs}
            for _,other,_ in copies[1:]:
                for r in other:
                    h=digest({k:v for k,v in r.items() if k!='pos'})
                    if h not in seen: merged.append(r); seen.add(h)
            if len(merged)>len(rs):
                merged.sort(key=lambda r:(timestamp(r.get('ts')) or datetime.min.replace(tzinfo=timezone.utc)))
                self.warnings.append({'code':'merged_session_fragments','session':sid,'count':1})
        return {'meta':meta,'records':merged,'root':f['root'],'file':f['path'],'copies':len(copies)}

class Store:
    def __init__(self, db, roots, tzname='local', max_db_mib=2048, min_free_mib=1024, report_events=20000, fast_window=False):
        self.db = writable_path(db, roots)
        self.max_db_bytes = int(max_db_mib * 1024**2)
        self.min_free_bytes = int(min_free_mib * 1024**2)
        self._parsed_sessions = {}
        self.report_events = int(report_events)
        self.report_coverage = {}
        self._last_display_floor = None
        self.fast_window = fast_window
        self._time_ranges = None
        self.storage_paused = None
        for suffix in ('-wal','-shm','-journal'): writable_path(str(self.db)+suffix,roots)
        is_new = not self.db.parent.exists(); self.db.parent.mkdir(parents=True,exist_ok=True)
        if is_new:
            try: self.db.parent.chmod(0o700)
            except OSError: pass
        self.roots = [{'path':str(Path(r['path']).expanduser().resolve()), 'label':str(r.get('label') or Path(r['path']).name)} for r in roots]
        self.tzname = tzname; self.zone = None
        if tzname in ('UTC', 'Etc/UTC'):
            self.zone = timezone.utc
        elif tzname != 'local':
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try: self.zone = ZoneInfo(tzname)
            except ZoneInfoNotFoundError as exc: raise ValueError('缺少该时区数据库。使用 --timezone local 或安装 tzdata。') from exc
        self.lock = threading.RLock(); self.scan_info = {}; self.error = None; self.source_signature = None
        with self.connect() as c:
            c.executescript('''PRAGMA journal_mode=DELETE;
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, root TEXT, offset INTEGER DEFAULT 0, size INTEGER DEFAULT 0,
              mtime INTEGER DEFAULT 0, inode TEXT, head TEXT, tail TEXT, bad INTEGER DEFAULT 0, huge INTEGER DEFAULT 0,
              incomplete INTEGER DEFAULT 0, present INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS records(path TEXT, pos INTEGER, data TEXT, PRIMARY KEY(path,pos));
            CREATE INDEX IF NOT EXISTS record_path ON records(path);
            ''')
        try: self.db.chmod(0o600)
        except OSError: pass
    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30); c.row_factory = sqlite3.Row
        pages = c.execute('PRAGMA page_size').fetchone()[0]
        c.execute('PRAGMA max_page_count=' + str(max(1, self.max_db_bytes // pages)))
        try:
            with c: yield c
        finally: c.close()
    def discover(self):
        found = {}; coverage = []
        for root in self.roots:
            p = Path(root['path']); count = 0
            if not p.exists(): coverage.append({**root,'exists':False,'files':0}); continue
            dirs = [p/n for n in ('sessions','archived_sessions') if (p/n).is_dir()]
            if not dirs: dirs=[p]
            try:
                for d in dirs:
                    for f in d.rglob('*.jsonl'):
                        if f.is_symlink() or not f.is_file(): continue
                        key = str(f.resolve()); found[key] = root['path']; count += 1
                coverage.append({**root,'exists':True,'files':count})
            except OSError as ex: coverage.append({**root,'exists':True,'files':count,'error':str(ex)})
        return found,coverage
    def storage_status(self):
        size = self.db.stat().st_size if self.db.exists() else 0
        return {'database_bytes': size, 'max_database_bytes': self.max_db_bytes,
                'min_free_bytes': self.min_free_bytes, 'paused': self.storage_paused,
                'policy': '保留历史；到达容量或磁盘余量边界后暂停采集，不删除原始日志。'}
    def scan(self):
        self.storage_paused = None
        if self.db.stat().st_size >= self.max_db_bytes:
            self.storage_paused = '数据库已达到容量上限，采集暂停；显示最近成功采集的数据。'
        elif shutil.disk_usage(self.db.parent).free < self.min_free_bytes:
            self.storage_paused = '磁盘可用空间不足，采集暂停；显示最近成功采集的数据。'
        if self.storage_paused:
            self.error = self.storage_paused
            return self.scan_info
        try:
            return self._scan()
        except sqlite3.OperationalError as ex:
            if 'full' not in str(ex).lower(): raise
            self.storage_paused = '数据库或磁盘容量边界触发，采集暂停；未完成批次已回滚。'
            self.error = self.storage_paused
            return self.scan_info
    def _scan(self):
        start = time.monotonic()
        with self.lock, self.connect() as c:
            found, coverage = self.discover(); c.execute('UPDATE files SET present=0')
            changed=0; bytes_read=0
            for path, root in found.items():
                try:
                    st = os.stat(path); old = c.execute('SELECT * FROM files WHERE path=?',(path,)).fetchone()
                    if old and old['size']==st.st_size and old['mtime']==st.st_mtime_ns:
                        c.execute('UPDATE files SET present=1,root=? WHERE path=?',(root,path)); continue
                    offset = old['offset'] if old else 0; reset = not old or st.st_size < offset
                    inode = f'{st.st_dev}:{st.st_ino}'
                    if old and old['inode'] != inode: reset=True
                    with open(path,'rb') as f:
                        if old and offset:
                            f.seek(0); head = hashlib.sha256(f.read(min(512,offset))).hexdigest()
                            f.seek(max(0,offset-512)); tail=hashlib.sha256(f.read(min(512,offset))).hexdigest()
                            if head!=old['head'] or tail!=old['tail']: reset=True
                        if old and st.st_size==old['size'] and st.st_mtime_ns!=old['mtime']: reset=True
                        if reset:
                            c.execute('DELETE FROM records WHERE path=?',(path,)); offset=0
                        batch_count=0
                        bad=0 if reset else old['bad']; huge=0 if reset else old['huge']; incomplete=0
                        f.seek(offset)
                        while True:
                            pos=f.tell(); line=f.readline(MAX_LINE+1)
                            if not line: break
                            bytes_read += len(line)
                            if len(line)>MAX_LINE:
                                while line and not line.endswith(b'\n'): line=f.readline(MAX_LINE+1); bytes_read+=len(line)
                                if not line.endswith(b'\n'): incomplete=1; f.seek(pos); break
                                huge+=1; offset=f.tell(); continue
                            if not line.endswith(b'\n'):
                                # A complete JSON value without newline is also deferred: writer may still append.
                                incomplete=1; f.seek(pos); break
                            offset=f.tell()
                            if not any(k in line for k in RELEVANT): continue
                            try: record=sanitize(json.loads(line))
                            except (ValueError,TypeError,AttributeError): bad+=1; continue
                            if record: c.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(path,pos,json.dumps(record,ensure_ascii=False,separators=(',', ':'))))
                            # Keep each source file atomic, including truncation/reset and its offset.
                            # Check free space periodically without committing partial source state.
                            if record:
                                batch_count += 1
                                if batch_count >= 1000:
                                    batch_count = 0
                                    if shutil.disk_usage(self.db.parent).free < self.min_free_bytes:
                                        raise sqlite3.OperationalError('disk full: reserve reached')
                        f.seek(0); head=hashlib.sha256(f.read(min(512,offset))).hexdigest()
                        f.seek(max(0,offset-512)); tail=hashlib.sha256(f.read(min(512,offset))).hexdigest()
                    c.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                              (path,root,offset,st.st_size,st.st_mtime_ns,inode,head,tail,bad,huge,incomplete,1))
                    c.commit()
                    changed+=1
                except OSError as ex:
                    coverage.append({'path':path,'error':str(ex)})
            self.scan_info={'at':datetime.now(timezone.utc).isoformat(),'seconds':round(time.monotonic()-start,3),
                            'changed_files':changed,'read_bytes':bytes_read,'roots':coverage}
            self.source_signature = digest([list(found), coverage, [(r['path'],r['size'],r['mtime'],r['present']) for r in c.execute('SELECT path,size,mtime,present FROM files ORDER BY path')]])
            self.error=None
        return self.scan_info
    def load_streams(self):
        with self.lock, self.connect() as c:
            files=[dict(r) for r in c.execute('SELECT * FROM files')]
            if self.fast_window:
                if self._time_ranges is None:
                    bounds=dict(c.execute("SELECT path, CASE WHEN sum(CASE WHEN json_extract(data,'$.ts') IS NOT NULL AND julianday(json_extract(data,'$.ts')) IS NULL THEN 1 ELSE 0 END)>0 THEN 1e99 ELSE max(julianday(json_extract(data,'$.ts'))) END FROM records GROUP BY path"))
                    self._time_ranges={f['path']:(f['offset'],f['mtime'],bounds.get(f['path'])) for f in files}
                else:
                    for f in files:
                        prior=self._time_ranges.get(f['path'])
                        if prior is None or prior[:2]!=(f['offset'],f['mtime']):
                            bound=c.execute("SELECT CASE WHEN sum(CASE WHEN json_extract(data,'$.ts') IS NOT NULL AND julianday(json_extract(data,'$.ts')) IS NULL THEN 1 ELSE 0 END)>0 THEN 1e99 ELSE max(julianday(json_extract(data,'$.ts'))) END FROM records WHERE path=?",(f['path'],)).fetchone()[0]
                            self._time_ranges[f['path']]=(f['offset'],f['mtime'],bound)
            groups=defaultdict(list); warnings=[]
            allowed_roots={r['path'] for r in self.roots}
            for f in files:
                if f['root'] not in allowed_roots: continue
                meta=None; any_record=False
                for row in c.execute('SELECT data FROM records WHERE path=? ORDER BY pos',(f['path'],)):
                    any_record=True; r=json.loads(row['data'])
                    if r['kind']=='session_meta': meta=r; break
                if meta is None:
                    if any_record: warnings.append({'code':'no_session_meta','file':Path(f['path']).name,'count':1})
                    continue
                sid=meta.get('id') or digest(f['path'])[:24]
                groups[sid].append((f,meta))
                for col,label in [('bad','malformed_records'),('huge','oversize_lines'),('incomplete','pending_tail')]:
                    if f[col]: warnings.append({'code':label,'file':Path(f['path']).name,'count':f[col]})
        return SessionStreams(self,groups,warnings),warnings,[f for f in files if f['root'] in allowed_roots]
    def parse(self):
        streams,warnings,files=self.load_streams(); events=[]; quotas=[]; ignored=Counter(); sessions=[]
        recent=[]; cached_count=0; sequence=0; last_pruned_floor=None; skipped_history=0
        def retain(candidates):
            nonlocal sequence
            for event in candidates:
                sequence+=1; item=(event.get('timestamp') or '',event['id'],sequence,event)
                if len(recent)<self.report_events: heapq.heappush(recent,item)
                elif item[:2]>recent[0][:2]: heapq.heapreplace(recent,item)
        for sid in streams:
            if self.fast_window and len(recent)>=self.report_events and recent[0][0]:
                floor_jd=timestamp(recent[0][0]).timestamp()/86400+2440587.5
                # One-second conservative margin covers SQLite Julian-day rounding.
                if streams.upper_bound(sid)<floor_jd-1/86400:
                    skipped_history+=1; f,meta=streams.groups[sid][0]
                    parent=meta.get('parent_thread_id') or meta.get('forked_from_id')
                    sessions.append({'id':sid,'project':meta.get('cwd') or 'unknown','model':'unknown','source':meta.get('source'),
                                     'child':meta.get('source')=='subagent' or bool(parent),'parent':parent,'events':None,
                                     'copies':len(streams.groups[sid]),'started_at':meta.get('timestamp') or meta.get('ts'),
                                     'root':f['root'],'file':Path(f['path']).name})
                    continue
            signature=streams.signature(sid)
            cached=self._parsed_sessions.get(sid)
            if cached and cached[0]==signature:
                ce,cw,cq,ci,cs=cached[1]
                retain(ce); warnings.extend(cw); quotas.extend(cq); ignored.update(ci); sessions.append(cs); cached_count+=cs['events']
                continue
            events=[]
            e0,w0,q0=0,len(warnings),len(quotas); old_ignored=ignored.copy()
            s=streams[sid]
            meta=s['meta']; rs=s['records']; parent=meta.get('parent_thread_id') or meta.get('forked_from_id')
            child=meta.get('source')=='subagent' or bool(parent); start=timestamp(meta.get('timestamp') or meta.get('ts'))
            boundary=None
            if child:
                triggers=[i for i,r in enumerate(rs) if r['kind'] in ('inter_agent_communication','inter_agent_communication_metadata') and r.get('trigger_turn')]
                tasks=[i for i,r in enumerate(rs) if r['kind']=='task_started' and (not start or not timestamp(r.get('ts')) or timestamp(r['ts'])>=start)]
                boundary=min(triggers+tasks) if triggers or tasks else None
            parent_counters=set()
            if parent in streams:
                parent_counters=streams.parent_counters(parent)
            seen_records=set(); seen_last=set(); previous=None; model='unknown'; provider=meta.get('model_provider') or 'unknown'
            project=meta.get('cwd') or 'unknown'; mode='unknown'; effort='unknown'; turn_id=None; count=0; own_started=not child; session_quotas={}
            for idx,r in enumerate(rs):
                kind=r['kind']
                if kind in ('turn_context','thread_settings_applied'):
                    if r.get('model'): model=r['model']
                    if r.get('cwd'): project=r['cwd']
                    if r.get('model_provider'): provider=r['model_provider']
                    if r.get('effort') or r.get('reasoning_effort'): effort=r.get('effort') or r['reasoning_effort']
                    if r.get('turn_id'): turn_id=r['turn_id']
                    if 'service_tier' in r: mode=tier(r['service_tier'])
                    continue
                if kind=='task_started': turn_id=r.get('turn_id') or turn_id
                if kind!='token_count': continue
                if r.get('quota'):
                    q={'root':s['root'],'session':sid,'timestamp':r.get('ts'),'snapshot':r['quota']}
                    qkey=r['quota'].get('limit_id','default')
                    if qkey not in session_quotas or (q['timestamp'] or '')>(session_quotas[qkey]['timestamp'] or ''): session_quotas[qkey]=q
                total=r.get('total'); last=r.get('last'); key=tuple_usage(total)
                if not total and not last: ignored['quota_only']+=1; continue
                inherited=child and boundary is not None and idx<boundary
                if child and boundary is None and not own_started:
                    if (start and timestamp(r.get('ts')) and timestamp(r['ts'])<start) or (key and key in parent_counters): inherited=True
                    elif parent in streams: own_started=True
                    else:
                        warnings.append({'code':'unanchored_child_usage','session':sid,'count':1,'candidate_tokens':(last or total or {}).get('total_tokens'),'detail':'缺少父日志或可靠回放边界，暂不计入。'})
                        previous=total or previous; continue
                if inherited:
                    previous=total or previous; ignored['inherited_replay']+=1; continue
                record_key=digest([r.get('ts'),key,tuple_usage(last),turn_id])
                if total and (key==tuple_usage(previous) or record_key in seen_records):
                    ignored['duplicate_snapshot']+=1; continue
                if total: seen_records.add(record_key)
                flags=[]; attribution='reported_delta'
                if total and previous:
                    changed=diff(total,previous)
                    if any(changed[k] is not None and changed[k]<0 for k in FIELDS):
                        if last and last.get('input_tokens') is not None and last.get('output_tokens') is not None:
                            values=dict(last); flags.append('counter_reset_using_last'); attribution='reset_last_uncertain'
                        else:
                            warnings.append({'code':'counter_regression_excluded','session':sid,'count':1,'detail':'累计计数回退且无单次用量，建立新基线，未猜测回退点用量。'})
                            previous=total; continue
                    else:
                        values=changed; attribution='cumulative_delta'
                        if last and last.get('input_tokens') is not None and last.get('output_tokens') is not None:
                            diff_total=(changed.get('input_tokens') or 0)+(changed.get('output_tokens') or 0)
                            last_total=last['input_tokens']+last['output_tokens']
                            if diff_total!=last_total: flags.append('delta_spans_multiple_or_corrected_requests')
                            elif (changed.get('input_tokens'), changed.get('output_tokens')) == (last.get('input_tokens'), last.get('output_tokens')):
                                for optional in ('cached_input_tokens','reasoning_output_tokens'):
                                    if values.get(optional) is None: values[optional]=last.get(optional)
                elif total:
                    values=dict(total); attribution='initial_cumulative'
                    if not last or (total.get('input_tokens'),total.get('output_tokens')) != (last.get('input_tokens'),last.get('output_tokens')):
                        flags.append('first_snapshot_history_time_uncertain')
                else:
                    values=dict(last); h=digest([r.get('ts'),turn_id,last])
                    if h in seen_last: ignored['duplicate_last']+=1; continue
                    seen_last.add(h); flags.append('last_only')
                previous=total or previous
                if values.get('input_tokens') is None or values.get('output_tokens') is None:
                    warnings.append({'code':'missing_input_or_output','session':sid,'count':1}); continue
                I,O=values['input_tokens'],values['output_tokens']; C,R=values.get('cached_input_tokens'),values.get('reasoning_output_tokens')
                if I<0 or O<0: warnings.append({'code':'negative_delta','session':sid,'count':1}); continue
                if C is not None and not 0<=C<=I: flags.append('invalid_cache_breakdown'); C=None
                if R is not None and not 0<=R<=O: flags.append('invalid_reasoning_breakdown'); R=None
                if values.get('total_tokens') is not None and values['total_tokens']!=I+O: flags.append('reported_total_mismatch')
                if I+O==0:
                    if any(values.get(k) not in (None,0) for k in ('cached_input_tokens','reasoning_output_tokens')):
                        warnings.append({'code':'breakdown_correction_without_usage','session':sid,'count':1,'detail':'只有分项修正，没有输入/输出增量；未回写历史归属，请核对。'})
                    ignored['zero_usage']+=1; continue
                d=timestamp(r.get('ts'))
                if not d: flags.append('missing_timestamp')
                local=d.astimezone(self.zone) if d else None
                ev={'id':digest([sid,r.get('ts'),key,tuple_usage(last),turn_id]),'session':sid,'parent':parent,'is_child':child,
                    'timestamp':d.isoformat() if d else None,'date':local.date().isoformat() if local else 'unknown',
                    'hour':local.hour if local else None,'utc_offset_minutes':int(local.utcoffset().total_seconds()/60) if local else None,
                    'model':r.get('model') or model,'provider':provider,'project':project,'source':meta.get('source') or 'unknown',
                    'root':s['root'],'file':Path(s['file']).name,'tier':mode,'effort':effort,'turn_id':turn_id,
                    'input_tokens':I,'cached_input_tokens':C,'uncached_input_tokens':I-C if C is not None else None,
                    'output_tokens':O,'reasoning_output_tokens':R,'nonreasoning_output_tokens':O-R if R is not None else None,
                    'total_tokens':I+O,'reported_total_tokens':values.get('total_tokens'),'context_window':r.get('context_window'),
                    'attribution':attribution,'flags':flags,'extra_counters':(last or total or {}).get('extra_counters',{})}
                events.append(ev); count+=1
            quotas.extend(session_quotas.values())
            sessions.append({'id':sid,'project':project,'model':model,'source':meta.get('source'),'child':child,'parent':parent,'events':count,'copies':s['copies'],'started_at':meta.get('timestamp') or meta.get('ts'),'root':s['root'],'file':Path(s['file']).name})
            unique_session={e['id']:e for e in events}
            ignored['duplicate_event']+=len(events)-len(unique_session)
            sessions[-1]['events']=len(unique_session); cached_count+=len(unique_session)
            retain(unique_session.values())
            # No session can contribute more than the global display limit.
            keep=heapq.nlargest(self.report_events,unique_session.values(),key=lambda e:(e.get('timestamp') or '',e['id']))
            if recent and len(recent)>=self.report_events:
                floor=recent[0][:2]; keep=[e for e in keep if (e.get('timestamp') or '',e['id'])>=floor]
            self._parsed_sessions[sid]=(signature,(keep,warnings[w0:],quotas[q0:],ignored-old_ignored,sessions[-1]))
            # Bound cached event objects throughout first parsing, not just at its end.
            if recent and len(recent)>=self.report_events and recent[0][:2]!=last_pruned_floor:
                floor=recent[0][:2]; last_pruned_floor=floor
                for cache_key,(cache_sig,entry) in self._parsed_sessions.items():
                    ce,cw,cq,ci,cs=entry
                    self._parsed_sessions[cache_key]=(cache_sig,([e for e in ce if (e.get('timestamp') or '',e['id'])>=floor],cw,cq,ci,cs))
        self._parsed_sessions={sid:v for sid,v in self._parsed_sessions.items() if sid in streams}
        events=[item[3] for item in recent]
        floor=recent[0][:2] if recent else None
        if self._last_display_floor is not None and (len(events)<min(self.report_events,cached_count) or (floor and floor<self._last_display_floor)):
            # A source correction can remove recent events. Refill from retained history,
            # rather than silently serving an undersized or incorrectly ordered window.
            self._parsed_sessions.clear(); self._last_display_floor=None
            return self.parse()
        self._last_display_floor=floor
        self.report_coverage={'limit':self.report_events,'cached_event_count':None if skipped_history else cached_count,'parsed_event_count':cached_count,'unparsed_historical_sessions':skipped_history,'displayed_event_count':len(events),
                             'truncated':cached_count>len(events) or skipped_history>0,'first_displayed':min((e['timestamp'] for e in events if e.get('timestamp')),default=None),
                             'last_displayed':max((e['timestamp'] for e in events if e.get('timestamp')),default=None),
                             'history_deleted':False}
        return events,warnings,quotas,ignored,sessions,files
    def report(self):
        events,warnings,quotas,ignored,sessions,files=self.parse()
        # Pricing and flags are report-specific; keep cached parse results immutable.
        events=[dict(e,flags=list(e['flags'])) for e in events]
        try: prices=json.loads((BASE/'rates.json').read_text(encoding='utf-8'))
        except (OSError,ValueError): prices={'models':{},'as_of':None}
        labels={r['path']:r['label'] for r in self.roots}
        for e in events:
            e['root_label']=labels.get(e['root'],Path(e['root']).name)
            e['estimated_credits']=None; e['standard_equivalent_credits']=None
            rate=prices.get('models',{}).get(e['model'])
            if rate and e['provider'] in ('openai','unknown') and e['cached_input_tokens'] is not None:
                if e['provider']=='unknown': e['flags'].append('provider_assumed_openai_for_estimate')
                v=(e['uncached_input_tokens']*rate['input']+e['cached_input_tokens']*rate['cached']+e['output_tokens']*rate['output'])/1e6
                e['standard_equivalent_credits']=v
                factor=1 if e['tier']=='standard' else rate.get('fast_multiplier') if e['tier']=='fast' else None
                if factor is not None: e['estimated_credits']=v*factor
        qmap={}
        for q in quotas:
            k=(q['root'],q['snapshot'].get('limit_id','default'))
            if k not in qmap or (q['timestamp'] or '')>(qmap[k]['timestamp'] or ''): qmap[k]=q
        counts=Counter()
        for w in warnings: counts[w['code']]+=w.get('count',1)
        for e in events:
            for flag in e['flags']: counts[flag]+=1
        return {'version':VERSION,'generated_at':datetime.now(timezone.utc).isoformat(),'demo':False,
                'timezone':self.tzname if self.zone else 'system local time','scan':self.scan_info,'scan_error':self.error,'storage':self.storage_status(),'display_scope':self.report_coverage,
                'events':sorted(events,key=lambda e:e['timestamp'] or ''),'sessions':sessions,'quota_snapshots':list(qmap.values()),
                'quality':dict(counts),'warnings':warnings[:300],'dedup':dict(ignored),'prices':prices,
                'coverage':{'canonical_sessions':len(sessions),'cached_files':len(files),'present_files':sum(f['present'] for f in files),
                            'cache_known_events':sum(e['cached_input_tokens'] is not None for e in events),
                            'reasoning_known_events':sum(e['reasoning_output_tokens'] is not None for e in events),
                            'priced_events':sum(e['estimated_credits'] is not None for e in events),'event_count':len(events)},
                'runtime':{'mode':'independent','codex_access':'read_only','database':str(self.db),'pid':os.getpid(),'model_calls':False},
                'scope':'Only local persisted Codex rollout usage. Not a billing statement or an account-wide completeness guarantee.'}

def totals(events):
    result={k:sum(e.get(k) or 0 for e in events) for k in ('input_tokens','cached_input_tokens','uncached_input_tokens','output_tokens','reasoning_output_tokens','nonreasoning_output_tokens','total_tokens','estimated_credits')}
    optional=('cached_input_tokens','uncached_input_tokens','reasoning_output_tokens','nonreasoning_output_tokens','estimated_credits')
    for k in optional:
        if not any(e.get(k) is not None for e in events): result[k]=None
    result['aggregate_policy']='Optional counters sum only reported rows; null means no reported values. Inspect field coverage before interpreting totals.'
    result['field_coverage']={k:{'reported':sum(e.get(k) is not None for e in events),'events':len(events)} for k in optional}
    result.update({'events':len(events),'sessions':len({e['session'] for e in events}),
                   'cache_known_events':sum(e.get('cached_input_tokens') is not None for e in events),
                   'reasoning_known_events':sum(e.get('reasoning_output_tokens') is not None for e in events),
                   'priced_events':sum(e.get('estimated_credits') is not None for e in events)})
    return result

def csv_export(events):
    buf=io.StringIO(); fields=['timestamp','date','session','parent','model','provider','root_label','project','source','tier','effort',*FIELDS,'uncached_input_tokens','nonreasoning_output_tokens','estimated_credits','standard_equivalent_credits','attribution','flags']
    w=csv.DictWriter(buf,fieldnames=fields,extrasaction='ignore'); w.writeheader()
    for e in events:
        row=dict(e); row['flags']=';'.join(e.get('flags',[]))
        for k,v in row.items():
            if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')): row[k]="'"+v
        w.writerow(row)
    return '\ufeff'+buf.getvalue()

class App:
    def __init__(self,store,interval=5,initialize=True):
        self.store=store; self.interval=interval; self.token=secrets.token_urlsafe(32); self.stop=threading.Event(); self.latest=None
        self._source_signature=None; self.revision=0; self._rates_mtime=None
        self.report_bytes=b'{}'; self.report_gzip=b''; self._published_error=None
        self.published=None
        if initialize: self.refresh()
        else:
            self.latest={'version':VERSION,'events':[],'sessions':[],'coverage':{},'quality':{},'scan':{},
                         'scan_error':'首次报表准备中，尚无可展示数据。','storage':self.store.storage_status(),
                         'runtime':{'mode':'independent','codex_access':'read_only','model_calls':False}}
            body=json.dumps(self.latest,ensure_ascii=False).encode()
            self.published=(0,None,body,gzip.compress(body,compresslevel=1))
    def refresh(self):
        self.store.scan()
        try: rates_mtime=(BASE/'rates.json').stat().st_mtime_ns
        except OSError: rates_mtime=None
        if self._published_error is None or self._source_signature != self.store.source_signature or rates_mtime != self._rates_mtime:
            self.latest=self.store.report(); self.revision+=1
            self._source_signature=self.store.source_signature; self._rates_mtime=rates_mtime
        self.latest['poll_seconds']=self.interval
        self.latest['scan_error']=self.store.error; self.latest['storage']=self.store.storage_status()
        publish_key=(self.revision,self.store.error)
        if self._published_error != publish_key:
            self.report_bytes=json.dumps(self.latest,ensure_ascii=False,separators=(',', ':')).encode()
            self.report_gzip=gzip.compress(self.report_bytes,compresslevel=1)
            self.published=(self.revision,self.store.error,self.report_bytes,self.report_gzip)
            self._published_error=publish_key
    def watch(self):
        try: self.refresh()
        except Exception as ex: self.store.error=str(ex)
        while not self.stop.wait(self.interval):
            try: self.refresh()
            except Exception as ex: self.store.error=str(ex)
    def handler(self):
        app=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def reply(self,status,body,ctype='application/json; charset=utf-8',cookie=False,etag=None,encoding=None):
                if not isinstance(body,bytes): body=body.encode('utf-8')
                self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Referrer-Policy','no-referrer'); self.send_header('X-Frame-Options','DENY')
                self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                if encoding: self.send_header('Content-Encoding',encoding); self.send_header('Vary','Accept-Encoding')
                if etag: self.send_header('ETag',etag)
                if cookie: self.send_header('Set-Cookie',f'lens_token={app.token}; HttpOnly; SameSite=Strict; Path=/')
                self.end_headers()
                if status != 304: self.wfile.write(body)
            def do_GET(self):
                u=urlsplit(self.path); q=parse_qs(u.query); host=self.headers.get('Host','')
                if host!=f'127.0.0.1:{self.server.server_port}': return self.reply(403,'{"error":"invalid Host"}')
                if u.path=='/' and secrets.compare_digest(q.get('key',[''])[0],app.token):
                    return self.reply(200,(BASE/'static'/'index.html').read_bytes(),'text/html; charset=utf-8',True)
                from http.cookies import SimpleCookie
                c=SimpleCookie()
                try: c.load(self.headers.get('Cookie',''))
                except Exception: pass
                candidate=c['lens_token'].value if 'lens_token' in c else ''
                if not secrets.compare_digest(candidate,app.token): return self.reply(403,'{"error":"请使用启动终端显示的授权地址。"}')
                if u.path=='/': return self.reply(200,(BASE/'static'/'index.html').read_bytes(),'text/html; charset=utf-8')
                static_types={'/static/style.css':'text/css; charset=utf-8','/static/app.js':'application/javascript; charset=utf-8','/static/format.js':'application/javascript; charset=utf-8'}
                if u.path in static_types: return self.reply(200,(BASE/u.path.lstrip('/')).read_bytes(),static_types[u.path])
                if u.path=='/api/health':
                    return self.reply(200,json.dumps({'version':VERSION,'revision':app.revision,'scan':app.store.scan_info,'error':app.store.error,'mode':'independent','codex_access':'read_only','pid':os.getpid()}))
                if u.path=='/api/report':
                    revision,published_error,plain,zipped=app.published
                    etag='"'+str(revision)+'-'+digest(app.store.error)[:8]+'"'
                    if self.headers.get('If-None-Match')==etag: return self.reply(304,'',etag=etag)
                    # A changed diagnostic is small and must be visible even between refreshes.
                    if published_error != app.store.error:
                        data=dict(app.latest,scan_error=app.store.error)
                        return self.reply(200,json.dumps(data,ensure_ascii=False),etag=etag)
                    compressed='gzip' in self.headers.get('Accept-Encoding','')
                    return self.reply(200,zipped if compressed else plain,etag=etag,encoding='gzip' if compressed else None)
                if u.path=='/api/export.json': return self.reply(200,json.dumps(app.latest,ensure_ascii=False,indent=2))
                if u.path=='/api/export.csv': return self.reply(200,csv_export(app.latest['events']),'text/csv; charset=utf-8')
                return self.reply(404,'{"error":"not found"}')
            def do_POST(self):
                # Only our own process lifecycle is mutable; no file-write API exists.
                if self.command != 'POST' or self.path != '/api/shutdown': return self.reply(405,'{"error":"read-only data API"}')
                if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}': return self.reply(403,'{"error":"invalid Host"}')
                candidate=self.headers.get('Authorization','').removeprefix('Bearer ')
                if not secrets.compare_digest(candidate,app.token): return self.reply(403,'{"error":"authorization required"}')
                self.reply(200,'{"stopping":true}'); app.stop.set()
                threading.Thread(target=self.server.shutdown,daemon=True).start()
            do_PUT=do_POST
            do_DELETE=do_POST
            do_PATCH=do_POST
        return Handler

def make_roots(args):
    if args.home: return [{'path':p,'label':Path(p).name} for p in args.home]
    if args.config:
        conf=json.loads(Path(args.config).expanduser().read_text(encoding='utf-8-sig'))
        return [{'path':x,'label':Path(x).name} if isinstance(x,str) else x for x in conf['roots']]
    return [{'path':os.environ.get('CODEX_HOME',str(Path.home()/'.codex')),'label':'本机 Codex'}]

def main(argv=None):
    p=argparse.ArgumentParser(description='Codex Token Lens — 本地日志 token 监控（不读取密钥）')
    p.add_argument('--full-history-parse',action='store_true',help='展开全部历史后选取展示窗口；耗时较长')
    p.add_argument('--report-events',type=int,default=20000)
    p.add_argument('--max-db-mib',type=int,default=2048)
    p.add_argument('--min-free-mib',type=int,default=1024)
    p.add_argument('--ready-file',help=argparse.SUPPRESS)
    p.add_argument('--home',action='append',help='Codex home / rollout directory, repeatable')
    p.add_argument('--config',help='JSON roots configuration')
    p.add_argument('--db',default=str(Path.home()/'.codex-token-lens'/'usage.sqlite3'))
    p.add_argument('--timezone',default='local',help='local or an IANA zone supported by Python')
    p.add_argument('--port',type=int,default=8765); p.add_argument('--interval',type=float,default=15)
    p.add_argument('--no-browser',action='store_true'); p.add_argument('--once',action='store_true')
    p.add_argument('--export',help='Write JSON/CSV/interactive HTML snapshot; used with --once')
    args=p.parse_args(argv)
    if args.max_db_mib < 1 or args.min_free_mib < 0 or not 1<=args.report_events<=100000: p.error('存储边界无效')
    if not 1 <= args.interval <= 3600: p.error('--interval 必须在 1–3600 秒之间')
    if not 0 <= args.port <= 65535: p.error('--port 超出有效范围')
    try:
        roots=make_roots(args)
        writable_path(BASE,roots)  # Do not install this independent app inside Codex home.
        if args.export: writable_path(args.export, roots)
        if args.ready_file: writable_path(args.ready_file, roots)
        database=writable_path(args.db,roots)
        instance_lock=DatabaseLock(database,roots)
        store=Store(database,roots,args.timezone,args.max_db_mib,args.min_free_mib,args.report_events,not args.full_history_parse)
    except (OSError,ValueError,KeyError) as ex: p.error(str(ex))
    if args.once:
        store.scan(); report=store.report()
        if args.export:
            dest=writable_path(args.export,roots); dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.suffix.lower()=='.csv': content=csv_export(report['events'])
            elif dest.suffix.lower()=='.html': content=render_html(report)
            else: content=json.dumps(report,ensure_ascii=False,indent=2)
            dest.write_text(content,encoding='utf-8'); print(str(dest))
        else: print(json.dumps({'totals':totals(report['events']),'coverage':report['coverage'],'quality':report['quality'],'scope':report['scope']},ensure_ascii=False,indent=2))
        return 0
    app=App(store,args.interval,initialize=False)
    try: server=ThreadingHTTPServer(('127.0.0.1',args.port),app.handler())
    except OSError as ex: print(f'无法绑定本机端口 {args.port}: {ex}. 使用 --port 8766。',file=sys.stderr); return 1
    url=f'http://127.0.0.1:{server.server_port}/?key={app.token}'
    if args.ready_file:
        dest=writable_path(args.ready_file,roots); dest.parent.mkdir(parents=True,exist_ok=True)
        tmp=writable_path(str(dest)+'.'+secrets.token_hex(5)+'.tmp',roots)
        fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump({'pid':os.getpid(),'url':url,'token':app.token,'version':VERSION},f)
        os.replace(tmp,dest)
    print(f'Codex Token Lens {VERSION}\n{url}\n独立进程；仅读取 Codex 日志；Ctrl+C 退出。后台正在准备报表，首次加载请等待页面更新。',flush=True)
    threading.Thread(target=app.watch,daemon=True).start()
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        app.stop.set(); server.server_close()
        if args.ready_file:
            try:
                dest=writable_path(args.ready_file,roots)
                if json.loads(dest.read_text(encoding='utf-8')).get('pid')==os.getpid(): dest.unlink()
            except (OSError,ValueError): pass
    return 0

if __name__=='__main__':
    if sys.version_info < (3,10): raise SystemExit('需要 Python 3.10 或更新版本')
    raise SystemExit(main())
