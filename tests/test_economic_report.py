import unittest
from wpe.economic_report import analyze, render

class EconomicTests(unittest.TestCase):
    def rows(self):return [{'observed_on':f'{1980+i}-12-31','value':2,'mode':'live'} for i in range(40)]
    def test_constant(self):
        r=analyze(self.rows());self.assertEqual(r['prediction'],2);self.assertEqual(r['lower'],2);self.assertIsNone(r['probability']);self.assertEqual(r['target_year'],2020)
    def test_gap_rejected(self):
        rows=self.rows();del rows[10]
        with self.assertRaises(ValueError):analyze(rows)
    def test_demo_rejected(self):
        rows=self.rows();rows[0]['mode']='demo'
        with self.assertRaises(ValueError):analyze(rows)
    def test_heldout_does_not_select_model(self):
        rows=self.rows()
        for i,row in enumerate(rows[:30]):row['value']=i
        a=analyze(rows)
        for row in rows[30:]:row['value']=1000
        self.assertEqual(a['model'],analyze(rows)['model'])
    def test_html_escape(self):
        output=render([{'title':'<script>','error':'<bad>'}],'2026-10-12');self.assertNotIn('<script>',output)
