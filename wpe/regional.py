"""Point-in-time regional descriptive rates, derivatives and cumulative excess.

These are observed statistics, not a crime probability or causal attribution.
"""
import argparse
import json
from datetime import datetime
from .core import connect
from .risk import schema, record, records, number
from .actors import timestamp, now

COUNT_MEASURES = {'reported_incidents', 'suspect_registration', 'arrests', 'prosecutions', 'convictions'}


def summarize(db, metric_id, region, as_of=None, per_capita=False, baseline=0.0, max_gap_hours=48):
    schema(db)
    cutoff = timestamp(as_of or now())
    number(baseline, 'baseline')
    number(max_gap_hours, 'max_gap_hours')
    metric = record(db, 'metrics', metric_id, cutoff)
    if max_gap_hours <= 0:
        raise ValueError('max_gap_hours must be positive')
    if per_capita and metric['measure'] not in COUNT_MEASURES:
        raise ValueError('per-capita conversion is only available for counts')
    values = [p for p in records(db, 'observations', cutoff)
              if p['metric_id'] == metric_id and p['region'] == region]
    latest = {}
    for p in sorted(values, key=lambda p: (p['period_end'], p['known_at'], p['id'])):
        latest[p['period_end']] = p
    rows = [latest[x] for x in sorted(latest)]
    if per_capita and any(p['denominator'] is None for p in rows):
        raise ValueError('each count requires a documented positive denominator')
    # Definitions are fixed by metric_id; incompatible periods are not combined.
    durations = {(datetime.fromisoformat(p['period_end']) - datetime.fromisoformat(p['period_start'])).total_seconds() for p in rows}
    if len(durations) > 1:
        raise ValueError('different observation-period lengths; use separate metrics')
    for a, b in zip(rows, rows[1:]):
        if b['period_start'] < a['period_end']:
            raise ValueError('overlapping observation periods')
    samples = [dict(p, display_value=p['value'] / p['denominator'] * 100000 if per_capita else p['value']) for p in rows]
    gaps = [(datetime.fromisoformat(b['period_end']) - datetime.fromisoformat(a['period_end'])).total_seconds()/3600
            for a, b in zip(samples, samples[1:])]
    if not rows:
        return {'status': 'pending_data', 'reason': 'no observations at cutoff', 'metric': metric}
    result = {'status': 'available', 'metric': metric, 'region': region, 'as_of': cutoff,
              'unit': 'count/100000_population/observation_period' if per_capita else metric['unit'],
              'samples': samples, 'baseline': baseline, 'probability': None,
              'note': 'Descriptive data. Counts, reported incidents, arrests and convictions are not interchangeable.'}
    if len(rows) < 2 or any(g > max_gap_hours for g in gaps):
        result['derivative'] = result['integral'] = {'status': 'insufficient_or_gapped_data'}
        return result
    velocities = [(b['display_value']-a['display_value'])/(g/24) for a,b,g in zip(samples,samples[1:],gaps)]
    # Exact positive excess for a piecewise linear path, including threshold crossings.
    total = 0.0
    for a, b, gap in zip(samples, samples[1:], gaps):
        x, y = a['display_value']-baseline, b['display_value']-baseline
        dt = gap/24
        if x >= 0 and y >= 0:
            total += (x+y)*dt/2
        elif x > 0 > y:
            total += x*dt*x/(x-y)/2
        elif y > 0 > x:
            total += y*dt*y/(y-x)/2
    result['derivative'] = {'velocity_per_day': velocities[-1],
        'acceleration_per_day2': (velocities[-1]-velocities[-2])/((gaps[-1]+gaps[-2])/48) if len(velocities)>1 else None}
    result['integral'] = {'positive_excess_value_days': total,
        'note': 'Area of indicator above baseline, not the count of unique victims/events.'}
    return result


def main():
    p=argparse.ArgumentParser(description='Regional observed rates and change indicators')
    p.add_argument('--db',default='data/runtime.sqlite'); p.add_argument('--metric',required=True)
    p.add_argument('--region',required=True); p.add_argument('--as-of'); p.add_argument('--per-capita',action='store_true')
    p.add_argument('--baseline',type=float,default=0); p.add_argument('--max-gap-hours',type=float,default=48)
    a=p.parse_args(); db=connect(a.db)
    try:
        from .risk import number
        number(a.baseline,'baseline'); number(a.max_gap_hours,'max_gap_hours')
        print(json.dumps(summarize(db,a.metric,a.region,a.as_of,a.per_capita,a.baseline,a.max_gap_hours),ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,OSError) as exc:
        p.exit(1,f'Error: {exc}\n')
    finally:
        db.close()

if __name__=='__main__': main()
