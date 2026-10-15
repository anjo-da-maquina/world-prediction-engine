import tempfile,unittest,json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch,MagicMock
from urllib.error import HTTPError
from wpe.autonomous import run_cycle,error_detail,retry_after_seconds,fetch_bytes
from wpe.core import connect
from wpe.domain_sources import schema,collect_sec
class RecoveryTests(unittest.TestCase):
 def test_value_error_detail(self):self.assertEqual(error_detail(ValueError('too many JSON-stat values'))['reason'],'too many JSON-stat values')
 def test_429_not_retried_immediately(self):
  opener=MagicMock();opener.open.side_effect=HTTPError('https://example.org',429,'limited',{'Retry-After':'900'},None)
  with patch('wpe.autonomous.urllib.request.build_opener',return_value=opener):
   with self.assertRaises(HTTPError):fetch_bytes('https://example.org')
  self.assertEqual(opener.open.call_count,1)
 def test_retry_after(self):
  now=datetime(2026,10,12,tzinfo=timezone.utc);exc=HTTPError('https://example.org',429,'limited',{'Retry-After':'900'},None)
  self.assertEqual(retry_after_seconds(exc,now),900)
 def test_provider_cooldown_persists(self):
  now=datetime(2026,10,12,tzinfo=timezone.utc)
  config={'sources':[{'id':'g1','type':'gdelt','domain':'conflict','query':'armed conflict','interval_seconds':3600},{'id':'g2','type':'gdelt','domain':'drugs','query':'drug seizure','interval_seconds':3600}]}
  def denied(_):raise HTTPError('https://example.org',429,'limited',{'Retry-After':'900'},None)
  with tempfile.TemporaryDirectory() as d:
   db=Path(d)/'db';out=Path(d)/'out';a=run_cycle(config,db,out,now,denied)
   self.assertEqual(a['sources'][1]['status'],'provider_cooldown')
   b=run_cycle(config,db,out,now+timedelta(minutes=6),lambda _:self.fail('cooldown must prevent fetch'))
   self.assertEqual([s['status'] for s in b['sources']],['provider_cooldown','provider_cooldown'])
   c=run_cycle(config,db,out,now+timedelta(minutes=15),lambda _:b'{"articles":[]}')
   self.assertEqual(c['status'],'ok')
 def test_sec_missing_primary_uses_filing_index(self):
  from wpe.autonomous import init
  from wpe.actors import schema as actor_schema
  db=connect(':memory:');init(db);actor_schema(db);schema(db)
  body=json.dumps({'cik':1,'name':'X','filings':{'recent':{'accessionNumber':['0000000001-26-000001'],'form':['CORRESP'],'filingDate':['2026-01-01'],'primaryDocument':['']}}}).encode()
  r=collect_sec(db,{'id':'s','cik':'1'},'2026-10-12T00:00:00+00:00',{},lambda _:body)
  self.assertEqual(r['new_documents'],1);self.assertIn('-index.html',db.execute('SELECT url FROM documents').fetchone()[0]);db.close()
