import io,json,unittest
from datetime import date,timedelta
from unittest.mock import patch
from wpe.core import connect,insert,series,forecast,save_prediction,resolve,target,backtest
class Tests(unittest.TestCase):
    def setUp(self): self.db=connect(':memory:')
    def tearDown(self): self.db.close()
    def load(self,n=100):
        start=date(2020,1,1)
        rows=[dict(variable='x',region='JP',observed_on=(start+timedelta(days=i)).isoformat(),known_on=(start+timedelta(days=i)).isoformat(),value=10+i%20,source='test',mode='demo') for i in range(n)]
        insert(self.db,rows); return rows
    def test_vintage_cutoff(self):
        r=self.load(10); insert(self.db,[dict(r[0],known_on='2020-02-01',value=999)])
        self.assertEqual(series(self.db,'x','JP','2020-01-10')[0]['value'],10)
        self.assertEqual(series(self.db,'x','JP','2020-02-01')[0]['value'],999)
    def test_conflict(self):
        r=self.load(10)
        with self.assertRaises(ValueError): insert(self.db,[dict(r[0],value=999)])
    def test_future_invariance(self):
        r=self.load(); before=forecast(self.db,'x','JP','2020-03-15',5,25)
        insert(self.db,[dict(r[0],known_on='2021-01-01',value=999)])
        self.assertEqual(before,forecast(self.db,'x','JP','2020-03-15',5,25))
    def test_missing_not_false(self): self.assertIsNone(target(self.load(10),'2020-01-09',5,100))
    def test_resolution(self):
        self.load(); p=forecast(self.db,'x','JP','2020-03-15',5,25); ident=save_prediction(self.db,p)
        with self.assertRaises(ValueError): resolve(self.db,ident,'2020-03-16')
        out=resolve(self.db,ident,'2020-04-09')
        self.assertAlmostEqual(out['brier'],(p['probability']-out['occurred'])**2)
        self.assertEqual(save_prediction(self.db,p),ident)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM predictions').fetchone()[0],1)
    def test_nan(self):
        r=self.load(10)
        with self.assertRaises(ValueError): insert(self.db,[dict(r[0],value=float('nan'))])
    def test_source(self):
        r=self.load(10); insert(self.db,[dict(r[0],source='other')])
        with self.assertRaises(ValueError): series(self.db,'x','JP','2020-01-10')
    def test_nonoverlap(self):
        self.load(120); result=backtest(self.db,'x','JP','2020-04-29',5,25)
        origins=[date.fromisoformat(r['origin']) for r in result['windows']]
        self.assertTrue(all((b-a).days>=5 for a,b in zip(origins,origins[1:])))
    def test_strict_threshold(self): self.assertEqual(target(self.load(10),'2020-01-08',2,19),0)
    def test_stale(self):
        self.load()
        with self.assertRaises(ValueError): forecast(self.db,'x','JP','2022-01-01',5,25)
    def test_worldbank(self):
        from wpe.__main__ import worldbank
        pages=[[{'pages':2},[{'date':'2020','value':1.2}]], [{'pages':2},[{'date':'2021','value':2.3}]]]
        with patch('urllib.request.urlopen',side_effect=[io.BytesIO(json.dumps(p).encode()) for p in pages]) as m:
            result=worldbank(self.db,'JPN','FP.CPI.TOTL.ZG',2020,2021)
        self.assertEqual(m.call_count,2); self.assertEqual(result['inserted'],2)
        self.assertEqual(series(self.db,'FP.CPI.TOTL.ZG','JPN','2022-01-01'),[])
if __name__=='__main__': unittest.main()
