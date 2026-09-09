"""Regression tests: isolation, process lifecycle, HTTP contracts and snapshots.
All logs and secrets below are synthetic temporary files, never real Codex data.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lens
import service
from test_lens import meta,ctx,settings,tok,u

class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name)
        self.home=self.base/'codex'; (self.home/'sessions').mkdir(parents=True)
        self.roots=[{'path':str(self.home),'label':'synthetic'}]
        self.log=self.home/'sessions'/'rollout.jsonl'
        self.log.write_text(''.join(json.dumps(r)+'\n' for r in [meta(),ctx(),settings(),tok(u(),u())]))
        self.store=lens.Store(self.base/'state'/'usage.sqlite3',self.roots,'UTC')
    def tearDown(self): self.temp.cleanup()
    def symlink(self, link, target, **kwargs):
        try: link.symlink_to(target, **kwargs)
        except (OSError, NotImplementedError) as exc: self.skipTest(f'Platform cannot create test symlinks: {exc}')
    def test_database_inside_source_rejected(self):
        with self.assertRaises(ValueError): lens.Store(self.home/'db.sqlite3',self.roots)
        self.assertFalse((self.home/'db.sqlite3').exists())
    def test_session_root_protects_parent_codex_home(self):
        with self.assertRaises(ValueError): lens.writable_path(self.home/'config.toml',[{'path':str(self.home/'sessions')}])
    def test_export_source_rejected_before_scan(self):
        before=self.log.read_bytes()
        with self.assertRaises(SystemExit): lens.main(['--home',str(self.home),'--db',str(self.base/'elsewhere.db'),'--once','--export',str(self.log)])
        self.assertEqual(before,self.log.read_bytes());self.assertFalse((self.base/'elsewhere.db').exists())
    def test_ready_file_source_rejected(self):
        with self.assertRaises(SystemExit): lens.main(['--home',str(self.home),'--ready-file',str(self.home/'runtime.json')])
        self.assertFalse((self.home/'runtime.json').exists())
    def test_default_codex_protected_even_with_custom_source(self):
        with self.assertRaises(ValueError):lens.writable_path(Path.home()/'.codex'/'anything',self.roots)
    def test_prefix_sibling_is_not_protected(self):
        target=self.base/'codex-token-lens'/'db';self.assertEqual(lens.writable_path(target,self.roots),target)
    def test_database_symlink_rejected(self):
        link=self.base/'unsafe.db';self.symlink(link,self.log)
        with self.assertRaises(ValueError):lens.Store(link,self.roots)
    def test_parent_symlink_into_codex_rejected(self):
        link=self.base/'linked';self.symlink(link,self.home,target_is_directory=True)
        with self.assertRaises(ValueError):lens.Store(link/'unsafe.db',self.roots)
    def test_database_hardlink_rejected(self):
        link=self.base/'unsafe.db'
        try: os.link(self.log,link)
        except OSError as exc: self.skipTest(f'Platform cannot create hardlinks: {exc}')
        with self.assertRaises(ValueError):lens.Store(link,self.roots)
    def test_sqlite_journal_symlink_rejected(self):
        path=self.base/'unsafe.db';self.symlink(Path(str(path)+'-wal'),self.log)
        with self.assertRaises(ValueError):lens.Store(path,self.roots)
    def test_source_contents_and_mtime_unchanged(self):
        auth=self.home/'auth.json';auth.write_text('SECRET-TOKEN')
        conf=self.home/'config.toml';conf.write_text('model="DO-NOT-CHANGE"')
        paths=[self.log,auth,conf];before=[(p.read_bytes(),p.stat().st_mtime_ns) for p in paths]
        for _ in range(3):self.store.scan();self.store.report()
        self.assertEqual(before,[(p.read_bytes(),p.stat().st_mtime_ns) for p in paths])
        with self.store.connect() as c:
            saved=''.join(r[0] for r in c.execute('SELECT data FROM records'))
            self.assertNotIn('SECRET-TOKEN',saved);self.assertNotIn('DO-NOT-CHANGE',saved)
    def test_no_source_auth_or_config_reads(self):
        (self.home/'auth.json').write_text('SECRET');(self.home/'config.toml').write_text('SECRET')
        real=Path.read_text
        def safe_read(p,*a,**kw):
            self.assertNotIn(p.name,('auth.json','config.toml'))
            return real(p,*a,**kw)
        with patch.object(Path,'read_text',safe_read): self.store.scan();self.store.report()
    def test_unchanged_report_is_cached(self):
        with patch.object(self.store,'report',wraps=self.store.report) as report:
            app=lens.App(self.store);revision=app.revision;app.refresh();app.refresh()
            self.assertEqual(report.call_count,1);self.assertEqual(app.revision,revision)
    def test_append_changes_revision(self):
        app=lens.App(self.store)
        with self.log.open('a') as f:f.write(json.dumps(tok(u(200,120,40,20),u(),3))+'\n')
        app.refresh();self.assertEqual(app.revision,2);self.assertEqual(lens.totals(app.latest['events'])['total_tokens'],240)
    def test_hour_and_offset_are_explicit(self):
        self.store.scan();event=self.store.report()['events'][0]
        self.assertEqual(event['hour'],10);self.assertEqual(event['utc_offset_minutes'],0)
    def test_snapshot_is_self_contained_and_escaped(self):
        self.store.scan();r=self.store.report();r['scope']='</script><script>alert(1)</script>'
        html=lens.render_html(r)
        self.assertNotIn('<script src=',html);self.assertNotIn('<link rel="stylesheet"',html)
        self.assertNotIn(r['scope'],html);self.assertIn('window.LENS_SNAPSHOT',html)
    def test_optional_null_preserved_in_json_export(self):
        data={'events':[{'reasoning_output_tokens':None}]};self.assertIn('null',lens.render_html(data))
    def test_mcp_entrypoint_removed(self):
        self.assertFalse(hasattr(lens,'mcp_loop'))
        with self.assertRaises(SystemExit):lens.main(['--mcp'])
    def test_interval_nan_rejected(self):
        with self.assertRaises(SystemExit):lens.main(['--interval','nan'])
    def test_service_state_in_codex_rejected(self):
        with self.assertRaises(SystemExit):service.main(['status','--home',str(self.home),'--state-dir',str(self.home/'lens')])
        self.assertFalse((self.home/'lens').exists())
    def test_status_has_no_filesystem_write_when_stopped(self):
        state=self.base/'nonexistent-state'
        self.assertEqual(service.main(['status','--home',str(self.home),'--state-dir',str(state)]),1)
        self.assertFalse(state.exists())
    def test_remote_control_state_rejected(self):
        f=self.base/'badstate';f.write_text(json.dumps({'url':'https://example.com/','token':'abc','pid':1}))
        self.assertIsNone(service.state_data(f))

class HTTPTests(IsolationTests):
    # Reuse fixtures but not the inherited tests to keep result counts meaningful.
    def setUp(self):
        super().setUp();self.app=lens.App(self.store,1)
        self.server=lens.ThreadingHTTPServer(('127.0.0.1',0),self.app.handler())
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.app.stop.set();self.server.shutdown();self.server.server_close();super().tearDown()
    def fetch(self,path='/',headers=None,auth=True,method='GET'):
        h={'Cookie':'lens_token='+self.app.token} if auth else {}
        h.update(headers or {})
        try:return urllib.request.urlopen(urllib.request.Request(self.url+path,headers=h,method=method),timeout=3)
        except urllib.error.HTTPError as e:return e
    def test_api_requires_auth(self):self.assertEqual(self.fetch('/api/report',auth=False).code,403)
    def test_static_requires_auth(self):self.assertEqual(self.fetch('/static/app.js',auth=False).code,403)
    def test_host_check(self):self.assertEqual(self.fetch('/api/report',headers={'Host':'evil.invalid'}).code,403)
    def test_external_path_not_served(self):self.assertEqual(self.fetch('/static/../lens.py').code,404)
    def test_static_served(self):
        with self.fetch('/static/format.js') as r:self.assertEqual(r.code,200);self.assertIn(b'LensFormat',r.read())
    def test_authorized_html_sets_cookie(self):
        with self.fetch('/?key='+self.app.token,auth=False) as r:self.assertEqual(r.code,200);self.assertIn('HttpOnly',r.headers['Set-Cookie'])
    def test_security_headers(self):
        with self.fetch('/') as r:self.assertEqual(r.headers['X-Frame-Options'],'DENY');self.assertIn("connect-src 'self'",r.headers['Content-Security-Policy'])
    def test_etag_304(self):
        with self.fetch('/api/report') as r:etag=r.headers['ETag']
        with self.fetch('/api/report',headers={'If-None-Match':etag}) as r:self.assertEqual(r.code,304)
    def test_unsupported_write_rejected(self):
        self.assertEqual(self.fetch('/api/report',method='POST').code,405)
        self.assertEqual(self.fetch('/api/report',method='DELETE').code,405)
    def test_shutdown_requires_separate_bearer_token(self):self.assertEqual(self.fetch('/api/shutdown',method='POST').code,403)
    def test_scan_errors_exposed(self):
        self.store.error='synthetic failure'
        with self.fetch('/api/report') as r:self.assertEqual(json.load(r)['scan_error'],'synthetic failure')
    def test_health_reports_independent_pid(self):
        with self.fetch('/api/health') as r:d=json.load(r)
        self.assertEqual(d['pid'],os.getpid());self.assertEqual(d['codex_access'],'read_only')

# Do not inherit the IsolationTests test methods into the HTTP group.
for name in list(vars(IsolationTests)):
    if name.startswith('test_') and name not in HTTPTests.__dict__:setattr(HTTPTests,name,None)

class ProcessTests(unittest.TestCase):
    def test_detached_start_status_stop_without_codex_mutations(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);home=base/'codex';(home/'sessions').mkdir(parents=True)
            log=home/'sessions'/'sample.jsonl';log.write_text(''.join(json.dumps(r)+'\n' for r in [meta(),ctx(),settings(),tok(u(),u())]))
            before=log.read_bytes();state=base/'state'
            common=[sys.executable,'-B',str(lens.BASE/'service.py')]
            opts=['--home',str(home),'--state-dir',str(state),'--port','0','--no-browser']
            result=subprocess.run(common+['start']+opts,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            ready=service.state_data(state/'runtime.json');self.assertIsNotNone(ready)
            try:
                self.assertIsNotNone(service.contact(ready))
                if os.name!='nt':self.assertEqual(os.getsid(ready['pid']),ready['pid'])
                status=subprocess.run(common+['status']+opts,capture_output=True,text=True,timeout=10)
                self.assertTrue(json.loads(status.stdout)['running'])
                again=subprocess.run(common+['start']+opts,capture_output=True,text=True,timeout=10)
                self.assertEqual(service.state_data(state/'runtime.json')['pid'],ready['pid'])
                self.assertEqual(log.read_bytes(),before)
            finally:
                stopped=subprocess.run(common+['stop']+opts,capture_output=True,text=True,timeout=10)
                self.assertEqual(stopped.returncode,0,stopped.stderr)
                deadline=time.monotonic()+5
                while time.monotonic()<deadline and service.contact(ready):time.sleep(.1)
                self.assertIsNone(service.contact(ready))
            self.assertEqual(log.read_bytes(),before)

if __name__=='__main__':unittest.main()
