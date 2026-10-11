import json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from wpe.autonomous import parse_feed,topics_for,run_cycle,load_config
from wpe.core import connect,series,insert
from wpe.signals import cumulative_excess,empirical_paths

RSS=b'<rss><channel><item><guid>a</guid><title>Inflation and liquidity</title><link>https://example.org/a</link><pubDate>Mon, 05 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'

def config():
    return {'sources':[{'id':'rss','type':'rss','url':'https://example.org/feed','interval_seconds':3600}],
            'topic_keywords':{'inflation':['inflation'],'ai':['AI']},
            'signals':[{'variable':'received_items','region':'rss','source':'rss','baseline':0,'max_gap_days':1}],
            'questions':[{'id':'q','variable':'received_items','region':'rss','source':'rss','horizon_days':7,'threshold':10}]}
class Tests(unittest.TestCase):
    def test_parse(self):
        r=parse_feed(RSS);self.assertEqual(r[0]['key'],'a');self.assertIn('+00:00',r[0]['published_at'])
    def test_atom(self):
        xml=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>a</id><title>t</title><link href="https://example.org/a"/><updated>2026-10-05T12:00:00Z</updated></entry></feed>'
        self.assertEqual(parse_feed(xml)[0]['url'],'https://example.org/a')
    def test_not_xml_feed(self):
        with self.assertRaises(ValueError):parse_feed(b'<html>unavailable</html>')
    def test_mentions_not_substrings(self):
        self.assertEqual(topics_for('said rail daily',{'ai':['AI']}),[])
        self.assertEqual(topics_for('AI and inflation',{'ai':['AI']}),['ai'])
    def test_integral_crossing(self):
        rows=[{'observed_on':'2026-01-01','value':-1},{'observed_on':'2026-01-03','value':1}]
        self.assertAlmostEqual(cumulative_excess(rows,0,2)['area_above_baseline'],.5)
    def test_integral_gap(self):
        rows=[{'observed_on':'2026-01-01','value':1},{'observed_on':'2026-01-05','value':1}]
        with self.assertRaises(ValueError):cumulative_excess(rows,0,1)
    def test_projection_constant(self):
        start=datetime(2026,1,1)
        rows=[{'observed_on':(start+timedelta(days=i)).date().isoformat(),'value':5} for i in range(30)]
        p=empirical_paths(rows,3,100);self.assertEqual(p['q10'],5);self.assertEqual(p['q90'],5)
    def test_cycles_dedup_and_missing(self):
        now=datetime(2026,10,5,12,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'db.sqlite';out=Path(d)/'outputs'
            a=run_cycle(config(),db,out,now,lambda _:RSS)
            self.assertEqual(a['sources'][0]['new_documents'],1)
            b=run_cycle(config(),db,out,now+timedelta(minutes=1),lambda _:self.fail('should not fetch'))
            self.assertEqual(b['sources'][0]['status'],'not_due')
            c=run_cycle(config(),db,out,now+timedelta(days=1),lambda _:RSS)
            self.assertEqual(c['sources'][0]['new_documents'],0)
            conn=connect(db)
            rows=series(conn,'received_items','rss','2026-10-06','rss')
            self.assertEqual(rows[0]['value'],1);self.assertEqual(rows[0]['known_on'],'2026-10-06')
            self.assertEqual(series(conn,'received_items','rss','2026-10-05','rss'),[])
            conn.close()
            def bad(_):raise OSError('offline')
            failed=run_cycle(config(),db,out,now+timedelta(days=2),bad)
            self.assertEqual(failed['status'],'degraded')
            next_run=run_cycle(config(),db,out,now+timedelta(days=3),lambda _:RSS)
            conn=connect(db);rows=series(conn,'received_items','rss','2026-10-08','rss')
            self.assertNotIn('2026-10-07',[r['observed_on'] for r in rows]);conn.close()
            self.assertEqual(next_run['forecasts'][0]['status'],'pending_data')
            self.assertTrue((out/'latest.json').exists())
    def test_one_source_failure_isolated(self):
        c=config();c['sources'].append({'id':'ok','type':'rss','url':'https://example.org/ok','interval_seconds':3600})
        def fetch(url):
            if url.endswith('feed'):raise OSError('offline')
            return RSS
        with tempfile.TemporaryDirectory() as d:
            result=run_cycle(c,Path(d)/'db',Path(d)/'out',datetime(2026,10,5,tzinfo=timezone.utc),fetch)
            self.assertEqual([r['status'] for r in result['sources']],['failed','ok'])
    def test_config_rejects_http(self):
        c=config();c['sources'][0]['url']='http://example.org'
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text(json.dumps(c))
            with self.assertRaises(ValueError):load_config(p)
    def test_restart_frozen_counts(self):
        now=datetime(2026,10,5,12,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'db';out=Path(d)/'out'
            for i in range(3):run_cycle(config(),db,out,now+timedelta(days=i),lambda _:RSS)
            conn=connect(db)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM observations WHERE variable='received_items' AND observed_on='2026-10-05'").fetchone()[0],1)
            conn.close()
    def test_prospective_forecast_and_resolution(self):
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'db';out=Path(d)/'out'
            conn=connect(db)
            # Test fixture only: synthetic observations routed through live-mode plumbing.
            rows=[{'variable':'received_items','region':'rss','source':'rss',
                   'observed_on':(start+timedelta(days=i)).date().isoformat(),
                   'known_on':(start+timedelta(days=i)).date().isoformat(),
                   'value':5,'mode':'live'} for i in range(75)]
            insert(conn,rows);conn.close()
            a=run_cycle(config(),db,out,start+timedelta(days=74),lambda _:RSS)
            self.assertIn('probability',a['forecasts'][0])
            ident=a['forecasts'][0]['id']
            conn=connect(db)
            insert(conn,[dict(rows[0],observed_on=(start+timedelta(days=i)).date().isoformat(),known_on=(start+timedelta(days=i)).date().isoformat()) for i in range(75,83)])
            conn.close()
            b=run_cycle(config(),db,out,start+timedelta(days=82),lambda _:RSS)
            scored=[r for r in b['resolutions'] if r.get('prediction_id')==ident]
            self.assertEqual(scored[0]['occurred'],0)
            self.assertIn('brier',scored[0])
    def test_worldbank_raw_archive(self):
        from wpe.autonomous import init,collect
        source={'id':'wb','type':'worldbank','country':'JPN','indicator':'FP.CPI.TOTL.ZG','start':2020}
        body=json.dumps([{'pages':1},[{'date':'2020','value':2}]]).encode()
        conn=connect(':memory:');init(conn)
        result=collect(conn,source,'2026-10-06T12:00:00+00:00',{},lambda _:body)
        self.assertEqual(result['inserted'],1)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM fetches').fetchone()[0],1)
        self.assertEqual(series(conn,'FP.CPI.TOTL.ZG','JPN','2025-01-01','wb'),[])
        conn.close()
if __name__=='__main__':unittest.main()
