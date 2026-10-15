import json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from wpe.core import connect
from wpe.autonomous import init,run_cycle,load_config
from wpe.domain_sources import schema,store,collect_gdelt,collect_sec,collect_mapped,collect_eurostat,decode_jsonstat,report

NOW='2026-10-12T10:00:00+00:00'
def record():return {'id':'r1','domain':'crime','kind':'statistic','known_on':'2026-10-12','occurred_on':'2024-12-31','period_start':'2024-01-01','source_url':'https://example.org/data','source_name':'test','title':'Count','definition':'Police reported offences, annual count','region':'JPN','value':10,'unit':'count','population':'reported incidents','classification':'theft','stage':'reported','denominator':100000}
def jsonstat():return {'label':'test','id':['unit','geo','time'],'size':[1,2,2],'dimension':{'unit':{'category':{'index':{'NR':0},'label':{'NR':'Number'}}},'geo':{'category':{'index':{'FR':0,'DE':1}}},'time':{'category':{'index':{'2023':0,'2024':1}}}},'value':{'0':1,'3':4}}
class DomainTests(unittest.TestCase):
 def setUp(self):self.db=connect(':memory:');init(self.db);schema(self.db)
 def tearDown(self):self.db.close()
 def test_immutable_idempotent(self):
  r=record();self.assertEqual(store(self.db,[r],'2026-10-12'),1);self.assertEqual(store(self.db,[r],'2026-10-12'),0)
  r['value']=11
  with self.assertRaises(ValueError):store(self.db,[r],'2026-10-12')
 def test_atomic_import(self):
  a=record();b=dict(a,id='bad',value=float('nan'))
  with self.assertRaises(ValueError):store(self.db,[a,b],'2026-10-12')
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM domain_records').fetchone()[0],0)
 def test_future_rejected(self):
  with self.assertRaises(ValueError):store(self.db,[record()],'2025-01-01')
 def test_unknown_crime_stage(self):
  with self.assertRaises(ValueError):store(self.db,[dict(record(),stage='unknown')],'2026-10-12')
 def test_no_negative_crime(self):
  with self.assertRaises(ValueError):store(self.db,[dict(record(),value=-1)],'2026-10-12')
 def test_bad_denominator(self):
  with self.assertRaises(ValueError):store(self.db,[dict(record(),denominator=0)],'2026-10-12')
 def test_bool_rejected(self):
  with self.assertRaises(ValueError):store(self.db,[dict(record(),value=True)],'2026-10-12')
 def test_period_rejected(self):
  with self.assertRaises(ValueError):store(self.db,[dict(record(),period_start='2025-01-01')],'2026-10-12')
 def test_graph_needs_quote(self):
  r=record();r.update(kind='relationship',domain='policy',entities=[{'id':'a','name':'A'},{'id':'b','name':'B'}],source_text='A received a grant.',relations=[{'from':'a','to':'b','relation':'receives_subsidy','status':'report','quote':'invented'}])
  with self.assertRaises(ValueError):store(self.db,[r],'2026-10-12')
  r['relations'][0]['quote']='received a grant';self.assertEqual(store(self.db,[r],'2026-10-12'),1)
 def test_gdelt_dedup(self):
  from wpe.actors import schema as actor_schema
  actor_schema(self.db);s={'id':'g','domain':'conflict','query':'armed conflict'}
  data=json.dumps({'articles':[{'url':'https://example.org/news','title':'A report','sourcecountry':'France','seendate':'20261012T000000Z'}]}).encode()
  a=collect_gdelt(self.db,s,NOW,{},lambda _:data);b=collect_gdelt(self.db,s,NOW,{},lambda _:data)
  self.assertEqual(a['new_documents'],1);self.assertEqual(b['new_documents'],0)
  r=json.loads(self.db.execute('SELECT payload FROM domain_records').fetchone()[0]);self.assertEqual(r['region'],'unknown');self.assertNotIn('occurred_on',r)
 def test_gdelt_html_rejected(self):
  with self.assertRaises(ValueError):collect_gdelt(self.db,{'id':'g','domain':'drugs','query':'drug seizure'},NOW,{},lambda _:b'<html>error</html>')
 def test_sec_identity(self):
  with self.assertRaises(ValueError):collect_sec(self.db,{'id':'s','cik':'1'},NOW,{},lambda _:b'{"cik":2}')
 def test_sec_concept(self):
  s={'id':'s','cik':'1','concept':'Assets'};body=json.dumps({'cik':1,'tag':'Assets','entityName':'X','units':{'USD':[{'end':'2024-12-31','filed':'2025-02-01','val':10,'accn':'a'}]}}).encode()
  a=collect_sec(self.db,s,NOW,{},lambda _:body);b=collect_sec(self.db,s,NOW,{},lambda _:body)
  self.assertEqual(a['inserted'],1);self.assertEqual(b['inserted'],0)
 def test_mapped_idempotence(self):
  defaults=record();defaults.pop('id');defaults.pop('value');s={'id':'m','url':'https://example.org/export','format':'csv','columns':{'value':'number'},'defaults':defaults}
  a=collect_mapped(self.db,s,NOW,{},lambda _:b'number\n10\n');b=collect_mapped(self.db,s,'2026-10-12T11:00:00+00:00',{},lambda _:b'number\n10\n')
  self.assertEqual(a['inserted'],1);self.assertEqual(b['inserted'],0)
 def test_jsonstat_decode(self):
  rows=decode_jsonstat(jsonstat());self.assertEqual(rows[1][0]['geo'],'DE');self.assertEqual(rows[1][0]['time'],'2024')
 def test_jsonstat_bad_index(self):
  d=jsonstat();d['value']={'4':1}
  with self.assertRaises(ValueError):decode_jsonstat(d)
 def test_eurostat_geo(self):
  s={'id':'e','dataset':'crim_off_cat','domain':'crime','stage':'reported','definition':'Police records'}
  a=collect_eurostat(self.db,s,NOW,{},lambda _:json.dumps(jsonstat()).encode())
  self.assertEqual(a['inserted'],2);self.assertEqual({r[0] for r in self.db.execute('SELECT region FROM domain_records')},{'FRA','DEU'})
 def test_citizenship_not_conviction(self):
  s={'id':'e','dataset':'crim_pris_ctz','domain':'justice','stage':'prisoner','definition':'Prisoners by citizenship'}
  collect_eurostat(self.db,s,NOW,{},lambda _:json.dumps(jsonstat()).encode())
  r=json.loads(self.db.execute('SELECT payload FROM domain_records').fetchone()[0]);self.assertEqual(r['stage'],'prisoner');self.assertNotIn('period_start',r)
 def test_dashboard_escape(self):
  store(self.db,[dict(record(),title='<script>alert(1)</script>')],'2026-10-12')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'index.html';report(self.db,p,{'sources':[]},'2026-10-12');self.assertNotIn('<script>',p.read_text(encoding='utf-8'))
 def test_config_valid(self):self.assertEqual(len(load_config('config/intelligence.json')['sources']),49)
 def test_integrated_cycle(self):
  c={'sources':[{'id':'g','type':'gdelt','query':'armed conflict','domain':'conflict','interval_seconds':3600}], 'integrated_reports':True}
  data=json.dumps({'articles':[]}).encode()
  with tempfile.TemporaryDirectory() as d:
   db=Path(d)/'db';out=Path(d)/'out'
   a=run_cycle(c,db,out,datetime(2026,10,12,10,tzinfo=timezone.utc),lambda _:data)
   self.assertEqual(a['status'],'ok');self.assertTrue((out/'intelligence.html').exists());self.assertTrue((out/'economic/index.html').exists());self.assertEqual(a['analysis_reports']['status'],'updated')
   b=run_cycle(c,db,out,datetime(2026,10,12,10,1,tzinfo=timezone.utc),lambda _:self.fail('not due'))
   self.assertEqual(b['analysis_reports']['status'],'cached')
 def test_sec_without_contact_not_configured(self):
  c={'sources':[{'id':'s','type':'sec','cik':'1','interval_seconds':3600}]}
  with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{},clear=True):
   r=run_cycle(c,Path(d)/'db',Path(d)/'out',datetime(2026,10,12,tzinfo=timezone.utc),lambda _:self.fail('must not fetch'))
   self.assertEqual(r['sources'][0]['status'],'not_configured')
