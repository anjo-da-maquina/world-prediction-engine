"""Trailing integrals and honest numeric projection baselines."""
import math
import random
import statistics as st
from datetime import date

def cumulative_excess(rows, baseline, max_gap_days=2):
    if len(rows)<2 or not math.isfinite(baseline) or max_gap_days<=0:
        raise ValueError('need >=2 samples, finite baseline and positive gap limit')
    points=sorted(rows,key=lambda r:r['observed_on'])
    area=0.0
    for left,right in zip(points,points[1:]):
        dt=(date.fromisoformat(right['observed_on'])-date.fromisoformat(left['observed_on'])).days
        if dt<=0 or dt>max_gap_days:
            raise ValueError('integral refused: duplicate date or excessive gap')
        a=float(left['value'])-baseline; b=float(right['value'])-baseline
        if not math.isfinite(a+b): raise ValueError('nonfinite values')
        # Exact integration of positive part of the piecewise linear interpolant.
        if a>=0 and b>=0: area+=(a+b)*dt/2
        elif a>0: area+=a*a*dt/(2*(a-b))
        elif b>0: area+=b*b*dt/(2*(b-a))
    return {'area_above_baseline':area,'unit':'input_unit * days',
            'baseline':baseline,'start':points[0]['observed_on'],'end':points[-1]['observed_on'],
            'assumption':'linear between observations; observed cumulative exposure, not future probability'}

def empirical_paths(rows,horizon=7,paths=1000,seed=42):
    if len(rows)<30 or not 1<=horizon<=365 or not 100<=paths<=10000:
        raise ValueError('need >=30 samples; horizon 1..365; paths 100..10000')
    points=rows[-180:]
    times=[date.fromisoformat(r['observed_on']) for r in points]
    if any((b-a).days!=1 for a,b in zip(times,times[1:])):
        raise ValueError('projection requires complete daily observations')
    values=[float(r['value']) for r in points]
    if not all(math.isfinite(v) for v in values): raise ValueError('nonfinite values')
    diffs=[b-a for a,b in zip(values,values[1:])]
    rng=random.Random(seed); endings=[]
    for _ in range(paths):
        value=values[-1]
        for _ in range(horizon): value+=rng.choice(diffs)
        endings.append(value)
    endings.sort()
    return {'model':'iid-change-bootstrap-v0.2','horizon_days':horizon,'paths':paths,'seed':seed,
            'q10':endings[int(.1*(paths-1))],'q50':endings[int(.5*(paths-1))],
            'q90':endings[int(.9*(paths-1))],'persistence_baseline':values[-1],
            'warning':'Uncalibrated numeric projection; iid changes ignore regimes and causal interactions. May produce physically impossible values for some series.'}
