import unittest
from wpe.relationships import changes,find,correlation
class RelationsTests(unittest.TestCase):
    def test_gap_not_bridged(self):self.assertEqual(changes({2000:1,2002:3,2003:5}),{2003:2})
    def test_constant(self):self.assertIsNone(correlation([(1,2)]*20))
    def test_lag_direction(self):
        a={2000+i:float(i*i) for i in range(30)};b={y+1:2*v for y,v in a.items()}
        results,_=find({'X':{'a':a,'b':b}})
        hit=next(r for r in results if r['lag_years']==1)
        self.assertAlmostEqual(hit['r'],1);self.assertEqual(hit['n'],29)
    def test_small_sample(self):self.assertEqual(find({'X':{'a':{1:2},'b':{1:3}}})[0],[])
