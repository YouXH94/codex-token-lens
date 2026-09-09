import io,json,shutil,tempfile,unittest,sys
from contextlib import redirect_stdout
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lens

def meta(sid='S',source='cli',parent=None):
    p={'id':sid,'timestamp':'2026-09-08T10:00:00Z','cwd':'/work/avalon','source':source,'model_provider':'openai','cli_version':'0.144.0'}
    if parent: p['parent_thread_id']=parent
    return {'type':'session_meta','timestamp':p['timestamp'],'payload':p}
def ctx(model='gpt-5.6-sol'):
    return {'type':'turn_context','timestamp':'2026-09-08T10:00:01Z','payload':{'model':model,'effort':'high'}}
def settings(mode='default',**kw):
    d={} if mode is None else {'service_tier':mode};d.update(kw)
    return {'type':'event_msg','timestamp':'2026-09-08T10:00:01Z','payload':{'type':'thread_settings_applied','thread_settings':d}}
def u(i=100,c=60,o=20,r=10,t=None):
    d={'input_tokens':i,'output_tokens':o,'total_tokens':i+o if t is None else t}
    if c is not None:d['cached_input_tokens']=c
    if r is not None:d['reasoning_output_tokens']=r
    return d
MISSING=object()
def tok(total=None,last=MISSING,sec=2):
    info={}
    if total is not None:info['total_token_usage']=total
    if last is not MISSING:info['last_token_usage']=last
    return {'type':'event_msg','timestamp':f'2026-09-08T10:00:{sec:02d}Z','payload':{'type':'token_count','info':info}}
def task():return {'type':'event_msg','timestamp':'2026-09-08T10:00:04Z','payload':{'type':'task_started','turn_id':'T1'}}

class LensTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.home=self.base/'codex';(self.home/'sessions').mkdir(parents=True)
        self.file=self.home/'sessions'/'rollout.jsonl';self.store=lens.Store(self.base/'state'/'db.sqlite3',[{'path':str(self.home),'label':'Test'}],'UTC')
    def tearDown(self):self.tmp.cleanup()
    def write(self,records,path=None):
        path=path or self.file;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(''.join(json.dumps(r)+'\n' for r in records));return path
    def report(self):self.store.scan();return self.store.report()
    def base_records(self,*rs):return [meta(),ctx(),settings(),*rs]
    def test_token_hierarchy(self):
        self.write(self.base_records(tok(u(),u())));r=self.report();e=r['events'][0]
        self.assertEqual(e['total_tokens'],120);self.assertEqual(e['uncached_input_tokens'],40);self.assertEqual(e['nonreasoning_output_tokens'],10)
        self.assertAlmostEqual(e['estimated_credits'],.0146)
    def test_cumulative_not_summed(self):
        self.write(self.base_records(tok(u()),tok(u(250,150,50,25),sec=3)));es=self.report()['events']
        self.assertEqual(sum(e['total_tokens'] for e in es),300)
    def test_duplicate_snapshot_different_timestamp(self):
        self.write(self.base_records(tok(u(),u()),tok(u(),u(),3)));r=self.report();self.assertEqual(len(r['events']),1);self.assertEqual(r['dedup']['duplicate_snapshot'],1)
    def test_quota_only(self):
        t=tok();t['payload']['info']=None;t['payload']['rate_limits']={'primary':{'used_percent':21,'window_minutes':300,'resets_at':1800000000}}
        self.write(self.base_records(t));r=self.report();self.assertEqual(len(r['events']),0);self.assertEqual(len(r['quota_snapshots']),1)
    def test_gap_delta_preserved(self):
        self.write(self.base_records(tok(u(),u()),tok(u(300,180,60,30),u(),3)));r=self.report();self.assertEqual(lens.totals(r['events'])['total_tokens'],360)
        self.assertIn('delta_spans_multiple_or_corrected_requests',r['quality'])
    def test_missing_cache_is_unknown(self):
        self.write(self.base_records(tok(u(c=None),u(c=None))));e=self.report()['events'][0];self.assertIsNone(e['cached_input_tokens']);self.assertIsNone(e['uncached_input_tokens']);self.assertIsNone(e['estimated_credits'])
    def test_missing_reasoning_is_unknown(self):
        self.write(self.base_records(tok(u(r=None),u(r=None))));e=self.report()['events'][0];self.assertIsNone(e['reasoning_output_tokens']);self.assertIsNone(e['nonreasoning_output_tokens'])
    def test_explicit_zero_is_known(self):
        self.write(self.base_records(tok(u(c=0,r=0),u(c=0,r=0))));e=self.report()['events'][0];self.assertEqual(e['cached_input_tokens'],0);self.assertEqual(e['reasoning_output_tokens'],0)
    def test_invalid_breakdown_flagged(self):
        self.write(self.base_records(tok(u(c=999,r=999),u(c=999,r=999))));e=self.report()['events'][0];self.assertIsNone(e['cached_input_tokens']);self.assertIsNone(e['reasoning_output_tokens'])
    def test_reported_total_preserved_not_added(self):
        self.write(self.base_records(tok(u(t=999),u(t=999))));e=self.report()['events'][0];self.assertEqual(e['total_tokens'],120);self.assertEqual(e['reported_total_tokens'],999);self.assertIn('reported_total_mismatch',e['flags'])
    def test_reset_with_last_uncertain(self):
        self.write(self.base_records(tok(u(),u()),tok(u(30,10,5,2),u(30,10,5,2),3)));r=self.report();self.assertEqual(lens.totals(r['events'])['total_tokens'],155);self.assertIn('counter_reset_using_last',r['quality'])
    def test_reset_without_last_excluded(self):
        self.write(self.base_records(tok(u()),tok(u(30,10,5,2),sec=3)));r=self.report();self.assertEqual(lens.totals(r['events'])['total_tokens'],120);self.assertIn('counter_regression_excluded',r['quality'])
    def test_partial_tail_deferred_then_completed(self):
        self.write(self.base_records(tok(u(),u())));self.report();s=json.dumps(tok(u(200,120,40,20),u(),3))
        with self.file.open('a') as f:f.write(s[:len(s)//2])
        self.assertEqual(len(self.report()['events']),1)
        with self.file.open('a') as f:f.write(s[len(s)//2:]+'\n')
        r=self.report();self.assertEqual(lens.totals(r['events'])['total_tokens'],240)
    def test_file_truncation_rebuild(self):
        self.write(self.base_records(tok(u()),tok(u(200,120,40,20),sec=3)));self.report();self.write(self.base_records(tok(u(50,20,10,5))));r=self.report();self.assertEqual(lens.totals(r['events'])['total_tokens'],60)
    def test_archive_copy_not_double_counted(self):
        self.write(self.base_records(tok(u(),u())));archive=self.home/'archived_sessions'/'rollout.jsonl';archive.parent.mkdir();shutil.copyfile(self.file,archive)
        r=self.report();self.assertEqual(len(r['events']),1);self.assertEqual(r['sessions'][0]['copies'],2)
    def test_archive_move_not_double_counted(self):
        self.write(self.base_records(tok(u(),u())));self.report();archive=self.home/'archived_sessions'/'rollout.jsonl';archive.parent.mkdir();self.file.rename(archive)
        r=self.report();self.assertEqual(len(r['events']),1)
    def test_model_switch(self):
        self.write(self.base_records(tok(u(),u()),ctx('gpt-6-astra'),tok(u(200,120,40,20),u(),3)));es=self.report()['events'];self.assertEqual([e['model'] for e in es],['gpt-5.6-sol','gpt-6-astra'])
    def test_tier_switch_and_unknown(self):
        self.write(self.base_records(tok(u(),u()),settings('priority'),tok(u(200,120,40,20),u(),3),settings(None),tok(u(300,180,60,30),u(),4),settings('alien'),tok(u(400,240,80,40),u(),5)))
        es=self.report()['events'];self.assertEqual([e['tier'] for e in es],['standard','fast','fast','unknown']);self.assertAlmostEqual(es[1]['estimated_credits'],es[0]['estimated_credits']*2.5);self.assertIsNone(es[-1]['estimated_credits'])
    def test_unknown_model_not_guessed(self):
        self.write([meta(),ctx('brand-new-model'),settings(),tok(u(),u())]);e=self.report()['events'][0];self.assertEqual(e['total_tokens'],120);self.assertIsNone(e['estimated_credits'])
    def test_subagent_trigger_prefix(self):
        self.write(self.base_records(tok(u(),u())))
        child=[meta('C',{'subagent':{'thread_spawn':{'parent_thread_id':'S'}}}),ctx(),tok(u(),u()),{'type':'inter_agent_communication_metadata','timestamp':'2026-09-08T10:00:04Z','payload':{'trigger_turn':True,'content':'SECRET'}},ctx('gpt-6-astra'),settings(),tok(u(130,70,25,12),u(30,10,5,2),5)]
        self.write(child,self.home/'sessions'/'child.jsonl');r=self.report();self.assertEqual(lens.totals(r['events'])['total_tokens'],155);self.assertEqual(r['dedup']['inherited_replay'],1)
    def test_subagent_task_boundary(self):
        self.write([meta('C','subagent','P'),ctx(),tok(u(),u()),task(),ctx(),tok(u(130,70,25,12),u(30,10,5,2),5)])
        r=self.report();self.assertEqual(lens.totals(r['events'])['total_tokens'],35)
    def test_unanchored_child_quarantine(self):
        self.write([meta('C','subagent','missing'),ctx(),tok(u(),u())]);r=self.report();self.assertEqual(len(r['events']),0);self.assertIn('unanchored_child_usage',r['quality'])
    def test_trigger_false_not_boundary(self):
        self.write([meta('C','subagent','missing'),ctx(),{'type':'inter_agent_communication','payload':{'trigger_turn':False}},tok(u(),u())]);self.assertEqual(len(self.report()['events']),0)
    def test_identical_independent_sessions_counted(self):
        self.write(self.base_records(tok(u(),u())));self.write([meta('SECOND'),ctx(),settings(),tok(u(),u())],self.home/'sessions'/'other.jsonl');self.assertEqual(lens.totals(self.report()['events'])['total_tokens'],240)
    def test_no_prompt_or_secrets_persisted(self):
        m=meta();m['payload']['instructions']='SECRET-PROMPT';rs=self.base_records(tok(u(),u()));rs[0]=m;rs.append({'type':'response_item','payload':{'text':'SECRET-CODE session_meta token_count'}});self.write(rs);self.report()
        with self.store.connect() as c:s=''.join(r[0] for r in c.execute('SELECT data FROM records'))
        self.assertNotIn('SECRET',s)
    def test_missing_root_and_empty(self):
        other=lens.Store(self.base/'other.sqlite3',[{'path':str(self.base/'missing')}]);other.scan();r=other.report();self.assertEqual(len(r['events']),0);self.assertFalse(r['scan']['roots'][0]['exists'])
    def test_reopen_database_no_dup(self):
        self.write(self.base_records(tok(u(),u())));self.report();other=lens.Store(self.store.db,self.store.roots,'UTC');other.scan();self.assertEqual(len(other.report()['events']),1)
    def test_deleted_log_history_retained(self):
        self.write(self.base_records(tok(u(),u())));self.report();self.file.unlink();r=self.report();self.assertEqual(len(r['events']),1);self.assertEqual(r['coverage']['present_files'],0)
    def test_noncanonical_exec_not_mixed(self):
        self.write([{'type':'turn.completed','usage':u()}]);r=self.report();self.assertFalse(r['events']);self.assertIn('no_session_meta',r['quality'])
    def test_csv_formula_escaped(self):
        out=lens.csv_export([{'model':'=EVIL()','flags':[]}]);self.assertIn("'=EVIL()",out)
    def test_only_incremental_bytes_read(self):
        self.write(self.base_records(tok(u(),u())));self.report();self.assertEqual(self.report()['scan']['read_bytes'],0)
        new=json.dumps(tok(u(200,120,40,20),u(),3))+'\n'
        with self.file.open('a') as f:f.write(new)
        r=self.report();self.assertEqual(r['scan']['read_bytes'],len(new.encode()))
    def test_malformed_line_reported(self):
        self.write(self.base_records(tok(u(),u())))
        with self.file.open('a') as f:f.write('{"type":"event_msg","token_count":oops}\n')
        r=self.report();self.assertEqual(r['quality']['malformed_records'],1);self.assertEqual(len(r['events']),1)
    def test_first_snapshot_history_warning(self):
        self.write(self.base_records(tok(u(1000,600,200,100),u())));r=self.report();self.assertEqual(lens.totals(r['events'])['total_tokens'],1200);self.assertIn('first_snapshot_history_time_uncertain',r['quality'])
    def test_nested_usage_details(self):
        d=lens.usage({'input_tokens':100,'output_tokens':30,'input_tokens_details':{'cached_tokens':60},'output_tokens_details':{'reasoning_tokens':20}});self.assertEqual(d['cached_input_tokens'],60);self.assertEqual(d['reasoning_output_tokens'],20)
    def test_reset_matching_earlier_snapshot_not_silently_dropped(self):
        self.write(self.base_records(tok(u(),u()),tok(u(200,120,40,20),u(),3),tok(u(),u(),4)))
        r=self.report();self.assertEqual(lens.totals(r['events'])['total_tokens'],360);self.assertIn('counter_reset_using_last',r['quality'])
    def test_optional_totals_unknown_not_zero(self):
        self.write(self.base_records(tok(u(c=None,r=None),u(c=None,r=None))));r=lens.totals(self.report()['events'])
        self.assertIsNone(r['cached_input_tokens']);self.assertIsNone(r['reasoning_output_tokens']);self.assertEqual(r['field_coverage']['cached_input_tokens']['reported'],0)
    def test_earliest_child_boundary_used(self):
        child=[meta('C','subagent','P'),ctx(),tok(u(),u()),task(),ctx(),tok(u(130,70,25,12),u(30,10,5,2),5),{'type':'inter_agent_communication_metadata','timestamp':'2026-09-08T10:00:06Z','payload':{'trigger_turn':True}},tok(u(160,80,30,14),u(30,10,5,2),7)]
        self.write(child);self.assertEqual(lens.totals(self.report()['events'])['total_tokens'],70)
    def test_breakdown_only_correction_diagnostic(self):
        self.write(self.base_records(tok(u(),u()),tok(u(100,70,20,10),sec=3)));r=self.report()
        self.assertEqual(lens.totals(r['events'])['total_tokens'],120);self.assertIn('breakdown_correction_without_usage',r['quality'])

if __name__=='__main__':unittest.main()
