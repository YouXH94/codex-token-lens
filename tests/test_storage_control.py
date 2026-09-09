import json, tempfile, unittest, sys, shutil
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lens
from test_lens import meta,ctx,settings,tok,u

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.home=self.base/'codex'; self.home.mkdir(); self.log=self.home/'sample.jsonl'
        self.roots=[{'path':str(self.home),'label':'Test'}]
        self.store=lens.Store(self.base/'state'/'db',self.roots,'UTC',min_free_mib=0)
    def tearDown(self): self.tmp.cleanup()
    def write(self, rows): self.log.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    def test_reserve_pauses_and_resumes_without_source_change(self):
        self.write([meta(),ctx(),tok(u(),u())]); before=self.log.read_bytes()
        self.store.min_free_bytes=1
        with patch.object(lens.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(100,100,0)):
            self.store.scan()
        self.assertIn('暂停',self.store.error); self.assertEqual(self.store.report()['events'],[])
        self.store.min_free_bytes=0; self.store.scan()
        self.assertIsNone(self.store.error); self.assertEqual(lens.totals(self.store.report()['events'])['total_tokens'],120)
        self.assertEqual(before,self.log.read_bytes())
    def test_existing_cache_not_deleted_at_limit(self):
        self.write([meta(),ctx(),tok(u(),u())]); self.store.scan()
        before=self.store.report(); self.store.max_db_bytes=self.store.db.stat().st_size
        self.store.scan(); after=self.store.report()
        self.assertEqual(before['events'],after['events']); self.assertIn('上限',after['scan_error'])
    def test_sqlite_cap_rolls_back_then_resume_is_exact(self):
        self.store.max_db_bytes=1024**2
        rows=[meta()]
        # Enough sanitized context to exceed 1 MiB in multiple committed batches.
        for i in range(1400): rows.extend([ctx('model-'+str(i)+'x'*1800),tok(u(i+1,0,1,0),sec=2)])
        self.write(rows); raw=self.log.read_bytes(); self.store.scan()
        self.assertIsNotNone(self.store.storage_paused)
        self.assertLessEqual(self.store.db.stat().st_size,1024**2)
        self.store.max_db_bytes=16*1024**2; self.store.scan(); r=self.store.report()
        self.assertIsNone(self.store.error)
        self.assertEqual(lens.totals(r['events'])['total_tokens'],1401)
        self.assertEqual(raw,self.log.read_bytes())
    def test_unchanged_session_parse_is_reused(self):
        self.write([meta(),ctx(),tok(u(),u())]); self.store.scan(); first=self.store.report()
        with patch.object(lens.SessionStreams,'__getitem__',side_effect=AssertionError('reparsed unchanged session')):
            second=self.store.report()
        self.assertEqual(first['events'],second['events']); self.assertEqual(first['quality'],second['quality'])
    def test_appended_session_invalidates_cache(self):
        self.write([meta(),ctx(),tok(u(),u())]); self.store.scan(); self.store.report()
        with self.log.open('a') as f:f.write(json.dumps(tok(u(200,120,40,20),sec=3))+'\n')
        self.store.scan(); self.assertEqual(lens.totals(self.store.report()['events'])['total_tokens'],240)
    def test_latest_quota_retained_and_flags_not_accumulated(self):
        rows=[meta(),ctx(),settings()]
        for i in range(3):
            t=tok(u(100*(i+1),60*(i+1),20*(i+1),10*(i+1)),sec=i+2)
            t['payload']['rate_limits']={'limit_id':'x','primary':{'used_percent':i}}
            rows.append(t)
        self.write(rows); self.store.scan(); a=self.store.report(); b=self.store.report()
        self.assertEqual(a['quality'],b['quality']); self.assertEqual(len(b['quota_snapshots']),1)
        self.assertEqual(b['quota_snapshots'][0]['snapshot']['primary']['used_percent'],2)
    def test_prepared_gzip_matches_report_and_reuses_bytes(self):
        self.write([meta(),ctx(),tok(u(),u())]); app=lens.App(self.store)
        self.assertEqual(json.loads(lens.gzip.decompress(app.report_gzip))['events'],app.latest['events'])
        old=app.report_bytes; app.refresh(); self.assertIs(app.report_bytes,old)
    def test_parent_change_invalidates_child_signature(self):
        self.write([meta('P'),ctx(),tok(u(),u())]); self.store.scan()
        child=self.home/'child.jsonl'; child.write_text(json.dumps(meta('C','subagent','P'))+'\n')
        self.store.scan(); streams,_,_=self.store.load_streams(); first=streams.signature('C')
        with self.log.open('a') as f:f.write(json.dumps(tok(u(200,120,40,20),sec=3))+'\n')
        self.store.scan(); streams,_,_=self.store.load_streams(); self.assertNotEqual(first,streams.signature('C'))
    def test_startup_is_nonblocking_and_reports_pending(self):
        with patch.object(self.store,'scan',side_effect=AssertionError('startup scanned synchronously')):
            app=lens.App(self.store,initialize=False)
        pending=json.loads(app.published[2]); self.assertEqual(pending['events'],[])
        self.assertIn('准备中',pending['scan_error'])
        self.write([meta(),ctx(),tok(u(),u())]); app.refresh()
        self.assertEqual(len(json.loads(app.published[2])['events']),1)
    def test_truncation_at_cap_preserves_previous_committed_file(self):
        self.write([meta(),ctx(),tok(u(),u())]); self.store.scan(); before=self.store.report()['events']
        self.store.max_db_bytes=1024**2
        self.write([meta('NEW')]+[ctx('big-'+str(i)+'x'*1800) for i in range(1400)])
        self.store.scan(); self.assertIsNotNone(self.store.storage_paused)
        self.assertEqual(self.store.report()['events'],before)
    def test_display_limit_preserves_history_and_cache_total(self):
        self.store.report_events=2
        self.write([meta(),ctx()]+[tok(u(100*i,0,20*i,0),sec=i+1) for i in range(1,6)])
        self.store.scan(); a=self.store.report(); b=self.store.report()
        self.assertEqual(len(a['events']),2); self.assertEqual(a['events'],b['events'])
        self.assertEqual(a['display_scope']['cached_event_count'],5)
        self.assertEqual(b['display_scope']['cached_event_count'],5)
        self.assertTrue(a['display_scope']['truncated'])
        self.assertEqual([e['timestamp'][17:19] for e in a['events']],['05','06'])
        with self.store.connect() as c:self.assertEqual(c.execute('select count(*) from records').fetchone()[0],7)
    def test_newest_selection_across_sessions_and_append(self):
        self.store.report_events=2
        self.write([meta('A'),ctx(),tok(u(),sec=2)])
        other=self.home/'other.jsonl';other.write_text(''.join(json.dumps(r)+'\n' for r in [meta('B'),ctx(),tok(u(),sec=3),tok(u(200,120,40,20),sec=4)]))
        self.store.scan();self.assertEqual({e['session'] for e in self.store.report()['events']},{'B'})
        with self.log.open('a') as f:f.write(json.dumps(tok(u(200,120,40,20),sec=5))+'\n')
        self.store.scan();r=self.store.report()
        self.assertEqual({e['session'] for e in r['events']},{'A','B'})
        self.assertEqual(r['display_scope']['cached_event_count'],4)
    def test_source_correction_refills_recent_window_from_history(self):
        self.store.report_events=2
        self.write([meta('A'),ctx(),tok(u(),sec=2),tok(u(200,120,40,20),sec=3)])
        other=self.home/'other.jsonl';other.write_text(''.join(json.dumps(r)+'\n' for r in [meta('B'),ctx(),tok(u(),sec=5),tok(u(200,120,40,20),sec=6)]))
        self.store.scan();self.assertEqual({e['session'] for e in self.store.report()['events']},{'B'})
        other.write_text(json.dumps(meta('B'))+'\n');self.store.scan();r=self.store.report()
        self.assertEqual(len(r['events']),2);self.assertEqual({e['session'] for e in r['events']},{'A'})
    def test_database_process_lock_is_exclusive_and_releasable(self):
        lock=lens.DatabaseLock(self.store.db,self.roots)
        try:
            with self.assertRaises(ValueError):lens.DatabaseLock(self.store.db,self.roots)
        finally:lock.close()
        again=lens.DatabaseLock(self.store.db,self.roots);again.close()
    def test_database_lock_rejects_source_directory(self):
        with self.assertRaises(ValueError):lens.DatabaseLock(self.home/'cache',self.roots)
        self.assertFalse((self.home/'cache.lock').exists())
    def test_fast_window_matches_full_parse_without_claiming_history_total(self):
        self.store.report_events=2
        self.write([meta('A'),ctx(),tok(u(),sec=2),tok(u(200,120,40,20),sec=3)])
        other=self.home/'other.jsonl';other.write_text(''.join(json.dumps(r)+'\n' for r in [meta('B'),ctx(),tok(u(),sec=5),tok(u(200,120,40,20),sec=6)]))
        self.store.scan(); full=self.store.report()
        fast=lens.Store(self.store.db,self.roots,'UTC',min_free_mib=0,report_events=2,fast_window=True)
        fast.scan(); r=fast.report()
        self.assertEqual(full['events'],r['events']);self.assertIsNone(r['display_scope']['cached_event_count'])
        self.assertEqual(r['display_scope']['unparsed_historical_sessions'],1)
        self.assertEqual(r['events'],fast.report()['events'])
        with self.log.open('a') as f:f.write(json.dumps(tok(u(300,180,60,30),sec=7))+'\n')
        fast.scan();self.store.scan();self.assertEqual(self.store.report()['events'],fast.report()['events'])
    def test_fast_window_keeps_conservative_bound_for_nonstandard_iso(self):
        self.store.report_events=1
        r=tok(u());r['timestamp']='20260909T100000Z'
        self.write([meta('A'),ctx(),r])
        other=self.home/'other.jsonl';other.write_text(''.join(json.dumps(r)+'\n' for r in [meta('B'),ctx(),tok(u(),sec=6)]))
        self.store.scan();full=self.store.report()
        fast=lens.Store(self.store.db,self.roots,'UTC',min_free_mib=0,report_events=1,fast_window=True)
        fast.scan();self.assertEqual(full['events'],fast.report()['events'])
if __name__=='__main__': unittest.main()
