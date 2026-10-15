import json,unittest
from wpe.core import connect
from wpe.autonomous import init,load_config
from wpe.actors import schema as actor_schema
from wpe.domain_sources import schema,collect_sec,collect_unhcr
class SourceRepairTests(unittest.TestCase):
 def setUp(self):self.db=connect(':memory:');init(self.db);actor_schema(self.db);schema(self.db)
 def tearDown(self):self.db.close()
 def test_sec_over_5000_not_rejected(self):
  n=5001;data={'cik':1,'name':'X','filings':{'recent':{'accessionNumber':[f'0000000001-26-{i:06d}' for i in range(n)],'form':['8-K']*n,'filingDate':['2027-01-01']*n,'primaryDocument':['x.htm']*n}}}
  r=collect_sec(self.db,{'id':'s','cik':'1'},'2026-10-12T00:00:00+00:00',{},lambda _:json.dumps(data).encode())
  self.assertEqual(r['returned'],n);self.assertEqual(r['skipped_future'],n)
 def test_sec_required_arrays_still_rejected(self):
  data={'cik':1,'filings':{'recent':{'accessionNumber':['a'],'form':[],'filingDate':[]}}}
  with self.assertRaisesRegex(ValueError,'accessionNumber'):collect_sec(self.db,{'id':'s','cik':'1'},'2026-10-12T00:00:00+00:00',{},lambda _:json.dumps(data).encode())
 def test_unhcr_population_and_country_identity(self):
  body=json.dumps({'total':1,'items':[{'year':2025,'coo':'GER','coo_name':'Germany','coo_iso':'DEU','refugees':3}]}).encode()
  r=collect_unhcr(self.db,{'id':'u','direction':'origin','start':2025},'2026-10-12T00:00:00+00:00',{},lambda _:body)
  self.assertEqual(r['inserted'],1);self.assertTrue(r['coverage_verified'])
  record=json.loads(self.db.execute('SELECT payload FROM domain_records').fetchone()[0]);self.assertEqual(record['region'],'DEU');self.assertEqual(record['stage'],'population_stock');self.assertNotIn('period_start',record)
 def test_unhcr_unknown_iso_not_guessed(self):
  body=json.dumps({'total':1,'items':[{'year':2025,'coa':'GER','refugees':'3'}]}).encode()
  collect_unhcr(self.db,{'id':'u','direction':'asylum','start':2025},'2026-10-12T00:00:00+00:00',{},lambda _:body)
  self.assertEqual(self.db.execute('SELECT region FROM domain_records').fetchone()[0],'UNHCR:GER')
 def test_unhcr_missing_total_marks_uncertainty(self):
  body=json.dumps({'items':[{'year':2025,'coa':'X','refugees':3}]}).encode()
  r=collect_unhcr(self.db,{'id':'u','direction':'asylum','start':2025},'2026-10-12T00:00:00+00:00',{},lambda _:body)
  self.assertFalse(r['coverage_verified'])
 def test_unhcr_resumes_cached_year(self):
  s={'id':'u','direction':'asylum','start':2025};body=json.dumps({'total':1,'items':[{'year':2025,'coa':'X','refugees':3}]}).encode()
  collect_unhcr(self.db,s,'2026-10-12T00:00:00+00:00',{},lambda _:body)
  r=collect_unhcr(self.db,s,'2026-10-13T00:00:00+00:00',{},lambda _:self.fail('cached year'))
  self.assertEqual(r['pages'],0)
 def test_replacement_config(self):
  c=load_config('config/intelligence.json');self.assertEqual(sum(s['type']=='unhcr' for s in c['sources']),2);self.assertFalse(any(s.get('indicator')=='SM.POP.REFG' for s in c['sources']))
