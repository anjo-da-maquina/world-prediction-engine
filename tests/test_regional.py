import copy
import unittest
from wpe.core import connect
from wpe.risk import import_bundle
from wpe.risk_demo import fixture
from wpe.regional import summarize

class RegionalTests(unittest.TestCase):
    def setUp(self):
        self.db=connect(':memory:'); self.p=fixture(); import_bundle(self.db,self.p)
        o=copy.deepcopy(self.p['observations'][0]); o.update(id='day2',value=6,period_start='2026-01-02T00:00:00+00:00',period_end='2026-01-02T00:00:00+00:00',known_at='2026-01-02T00:00:00+00:00')
        import_bundle(self.db,{'observations':[o]})
    def tearDown(self): self.db.close()
    def test_rate_and_derivative(self):
        r=summarize(self.db,'equipment_reports','DEMO','2026-01-03T00:00:00+00:00',True)
        self.assertAlmostEqual(r['samples'][-1]['display_value'],60)
        self.assertAlmostEqual(r['derivative']['velocity_per_day'],20)
        self.assertAlmostEqual(r['integral']['positive_excess_value_days'],50)
        self.assertIsNone(r['probability'])
    def test_threshold_crossing(self):
        r=summarize(self.db,'equipment_reports','DEMO',baseline=5)
        self.assertAlmostEqual(r['integral']['positive_excess_value_days'],.25)
    def test_future_known_not_used(self):
        r=summarize(self.db,'equipment_reports','DEMO','2026-01-01T00:00:00+00:00')
        self.assertEqual(len(r['samples']),1)
    def test_gap_not_integrated(self):
        r=summarize(self.db,'equipment_reports','DEMO',max_gap_hours=12)
        self.assertEqual(r['integral']['status'],'insufficient_or_gapped_data')
    def test_missing_denominator(self):
        o=copy.deepcopy(self.p['observations'][0]); o.update(id='no-denom',denominator=None,known_at='2026-01-03T00:00:00+00:00')
        import_bundle(self.db,{'observations':[o]})
        with self.assertRaises(ValueError): summarize(self.db,'equipment_reports','DEMO',per_capita=True)
    def test_capacity_is_not_crime_rate(self):
        with self.assertRaises(ValueError): summarize(self.db,'response_delay','DEMO',per_capita=True)
    def test_empty_is_missing(self):
        self.assertEqual(summarize(self.db,'equipment_reports','OTHER')['status'],'pending_data')

if __name__=='__main__': unittest.main()
