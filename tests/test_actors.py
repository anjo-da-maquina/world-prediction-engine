import json,tempfile,unittest
from pathlib import Path
from wpe.core import connect
from wpe.actors import schema,store_text,add_evidence,extract_candidates,register_entity,link_interest,register_forecast,resolve_action,review_event,import_bundle,export
T='2026-01-01T00:00:00+00:00'
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=connect(':memory:');schema(self.db)
        self.text='架空の法案はまだ可決されていない。'
        self.tid=store_text(self.db,'fixture',self.text,T,'fixture-fetch')
        extract_candidates(self.db,T)
        self.event=self.db.execute('SELECT id FROM policy_events').fetchone()[0]
        self.evid=self.db.execute('SELECT id FROM evidence_spans').fetchone()[0]
        register_entity(self.db,{'id':'a','name':'架空企業A','kind':'company','region':'FICTION'})
    def tearDown(self):self.db.close()
    def forecast(self):
        return dict(id='f',event_id=self.event,entity_id='a',action_kind='investment_announcement',question='架空企業Aが設備投資を発表するか',created_at=T,deadline='2026-02-01T00:00:00+00:00',probability=.4,method='manual_estimate',evidence_ids=[self.evid],resolution_rule='期限内の公式設備投資発表。否定判定は記録の完全性を確認',counterevidence='制度の成立が未確認')
    def test_negation_not_verified(self):
        self.assertEqual(export(self.db)['policy_events'][0]['current_status'],'candidate')
    def test_exact_quote(self):
        with self.assertRaises(ValueError):add_evidence(self.db,self.tid,0,3,'不一致')
    def test_no_future_evidence(self):
        q=self.forecast();q['created_at']='2025-12-01T00:00:00+00:00'
        with self.assertRaises(ValueError):register_forecast(self.db,q)
    def test_extract_idempotent(self):self.assertEqual(extract_candidates(self.db,T)['added_candidates'],0)
    def test_evidence_version(self):
        newid=store_text(self.db,'fixture','法案が可決されたとの発表。','2026-01-02T00:00:00+00:00','f2')
        self.assertNotEqual(newid,self.tid);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM document_texts').fetchone()[0],2)
    def test_review_history(self):
        review_event(self.db,self.event,'uncertain','資料不足',[self.evid],T)
        review_event(self.db,self.event,'rejected','否定文だった',[self.evid],T)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM event_reviews').fetchone()[0],2)
        self.assertEqual(export(self.db)['policy_events'][0]['current_status'],'rejected')
    def test_forecast_resolution(self):
        register_forecast(self.db,self.forecast())
        with self.assertRaises(ValueError):resolve_action(self.db,'f',1,[self.evid],'test',T)
        out=resolve_action(self.db,'f',1,[self.evid],'fixture outcome','2026-02-02T00:00:00+00:00')
        self.assertAlmostEqual(out['brier'],.36)
        self.assertEqual(resolve_action(self.db,'f',0,[self.evid],'revision','2026-02-03T00:00:00+00:00')['occurred'],1)
    def test_immutable_forecast(self):
        q=self.forecast();register_forecast(self.db,q);q['probability']=.8
        with self.assertRaises(ValueError):register_forecast(self.db,q)
    def test_axis_separation(self):
        link_interest(self.db,dict(id='b',event_id=self.event,entity_id='a',axis='benefit',statement='受注機会の可能性',status='hypothesis',evidence_ids=[self.evid],alternative='費用増で利益は減る可能性',created_at=T))
        self.assertEqual(self.db.execute('SELECT axis FROM interest_links').fetchone()[0],'benefit')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM interest_links WHERE axis='influence'").fetchone()[0],0)
    def test_bundle_atomic(self):
        bad={'entities':[{'id':'b','name':'架空B','kind':'company','region':'FICTION'}],'forecasts':[dict(self.forecast(),entity_id='missing')]}
        with self.assertRaises(ValueError):import_bundle(self.db,bad)
        self.assertIsNone(self.db.execute("SELECT id FROM entities WHERE id='b'").fetchone())
    def test_kokkai_mock(self):
        from wpe.autonomous import init
        from wpe.policy_source import collect_kokkai
        init(self.db)
        body=json.dumps({'numberOfRecords':101,'speechRecord':[{'speechID':'x','speech':'補助金について議論する。','date':'2025-12-31','speaker':'架空発言者'}]}).encode()
        r=collect_kokkai(self.db,{'id':'diet','query':'補助金'},T,{},lambda _:body)
        self.assertTrue(r['coverage_truncated'])
        doc=self.db.execute("SELECT * FROM documents WHERE source='diet'").fetchone()
        self.assertIsNone(doc['published_at'])
        self.assertEqual(doc['first_seen_at'],T)
    def test_empirical_method_requires_history(self):
        q=self.forecast();q['method']='empirical_base_rate'
        with self.assertRaises(ValueError):register_forecast(self.db,q)
    def test_suggestions_require_registered_link(self):
        from wpe.actors import suggest_actions
        self.assertEqual(suggest_actions(self.db,T),[])
        link_interest(self.db,dict(id='b',event_id=self.event,entity_id='a',axis='benefit',statement='受益の可能性',status='hypothesis',evidence_ids=[self.evid],alternative='不成立の可能性',created_at=T))
        suggestions=suggest_actions(self.db,T)
        self.assertEqual(len(suggestions),3)
        self.assertTrue(all(r['probability_estimate']['status']=='pending_data' for r in suggestions))
if __name__=='__main__':unittest.main()
