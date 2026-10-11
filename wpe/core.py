"""Auditable forecasting prototype. Standard library only, daily time semantics."""
import csv
import hashlib
import json
import math
import sqlite3
import statistics as st
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

VERSION = '0.1.0'

def day(text):
    return date.fromisoformat(text).isoformat()

def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS observations (
      id INTEGER PRIMARY KEY, variable TEXT NOT NULL, region TEXT NOT NULL,
      observed_on TEXT NOT NULL, known_on TEXT NOT NULL, value REAL NOT NULL,
      source TEXT NOT NULL, mode TEXT NOT NULL,
      UNIQUE(variable,region,observed_on,known_on,source));
    CREATE TABLE IF NOT EXISTS predictions (
      id TEXT PRIMARY KEY, created_on TEXT NOT NULL, deadline TEXT NOT NULL,
      variable TEXT NOT NULL, region TEXT NOT NULL, threshold REAL NOT NULL,
      horizon INTEGER NOT NULL, probability REAL NOT NULL CHECK(probability BETWEEN 0 AND 1),
      model TEXT NOT NULL, snapshot TEXT NOT NULL, recorded_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS outcomes (
      prediction_id TEXT PRIMARY KEY REFERENCES predictions(id),
      resolved_on TEXT NOT NULL, occurred INTEGER NOT NULL CHECK(occurred IN (0,1)),
      brier REAL NOT NULL, evidence TEXT NOT NULL);
    ''')
    return db

def insert(db, rows):
    """Immutable vintages. Conflicting values at same vintage are rejected."""
    count = 0
    with db:
        for r in rows:
            obs, known = day(r['observed_on']), day(r['known_on'])
            value = float(r['value'])
            if known < obs or not math.isfinite(value):
                raise ValueError('known_on must be >= observed_on; value must be finite')
            mode = r.get('mode', 'live')
            if mode not in ('live', 'demo'):
                raise ValueError('mode must be live or demo')
            args = (r['variable'], r['region'], obs, known, value, r['source'], mode)
            old = db.execute('''SELECT value,mode FROM observations WHERE
              variable=? AND region=? AND observed_on=? AND known_on=? AND source=?''',
              (args[0],args[1],obs,known,args[5])).fetchone()
            if old:
                if old['value'] != value or old['mode'] != mode:
                    raise ValueError('vintage conflict: add a new known_on, do not overwrite')
            else:
                db.execute('INSERT INTO observations(variable,region,observed_on,known_on,value,source,mode) VALUES (?,?,?,?,?,?,?)',args)
                count += 1
    return count

def import_csv(db, path):
    with open(path, encoding='utf-8', newline='') as f:
        return insert(db, list(csv.DictReader(f)))

def series(db, variable, region, as_of, source=None):
    """Only data actually known at cutoff; latest vintage per observation."""
    day(as_of)
    rows = db.execute('''SELECT * FROM observations WHERE variable=? AND region=?
       AND known_on<=? AND observed_on<=? ORDER BY observed_on,known_on,id''',
       (variable,region,as_of,as_of)).fetchall()
    if source:
        rows = [r for r in rows if r['source'] == source]
    if len({r['source'] for r in rows}) > 1:
        raise ValueError('multiple sources: choose --source explicitly')
    if len({r['mode'] for r in rows}) > 1:
        raise ValueError('demo and live data cannot be mixed')
    latest = {r['observed_on']:dict(r) for r in rows}
    return [latest[k] for k in sorted(latest)]

def features(rows, cutoff, window=12):
    eligible = [r for r in rows if r['observed_on'] <= cutoff and r['known_on'] <= cutoff]
    if len(eligible) < 8:
        raise ValueError('need at least 8 known observations')
    eligible = eligible[-window:]
    # Trailing smoothing only. Irregular intervals respected; no central filters.
    smooth = [st.mean(r['value'] for r in eligible[max(0,i-2):i+1]) for i in range(len(eligible))]
    times = [date.fromisoformat(r['observed_on']).toordinal() for r in eligible]
    velocities = [(smooth[i]-smooth[i-1])/(times[i]-times[i-1]) for i in range(1,len(times))]
    acceleration = (velocities[-1]-velocities[-2])/((times[-1]-times[-3])/2)
    sd = st.pstdev(r['value'] for r in eligible) or 1.0
    return {'value':eligible[-1]['value'], 'velocity_per_day':velocities[-1],
            'acceleration_per_day2':acceleration,
            'zscore':(eligible[-1]['value']-st.mean(r['value'] for r in eligible))/sd,
            'stale_days':(date.fromisoformat(cutoff)-date.fromisoformat(eligible[-1]['observed_on'])).days,
            'count':len(eligible)}

def target(rows, origin, horizon, threshold):
    """Was any observation > threshold in (origin, deadline]? 0 only if covered."""
    end = (date.fromisoformat(origin)+timedelta(days=horizon)).isoformat()
    window = [r for r in rows if origin < r['observed_on'] <= end]
    if any(r['value'] > threshold for r in window):
        return 1
    # Conservative completeness rule for irregular data: observation at deadline.
    if window and window[-1]['observed_on'] == end:
        return 0
    return None

def forecast(db, variable, region, as_of, horizon, threshold, source=None, k=15):
    if horizon <= 0 or k <= 0 or not math.isfinite(threshold):
        raise ValueError('horizon/k must be positive and threshold finite')
    rows = series(db,variable,region,as_of,source)
    current = features(rows,as_of)
    if current['stale_days'] > horizon:
        raise ValueError('data older than forecast horizon; refresh data or choose longer horizon')
    train = []
    evidence_ids = {r["id"] for r in rows}
    for r in rows:
        origin = r['observed_on']
        end = (date.fromisoformat(origin)+timedelta(days=horizon)).isoformat()
        if end > as_of:
            continue
        # Historical features use original as-of vintages, not today's revisions.
        past = series(db,variable,region,origin,source)
        try:
            feat = features(past,origin)
        except ValueError:
            continue
        outcome = target(rows,origin,horizon,threshold)
        if outcome is not None:
            train.append((origin,feat,outcome))
            evidence_ids.update(r["id"] for r in past)
    if len(train) < 20:
        raise ValueError('need >=20 matured training windows with point-in-time features')
    keys = ('value','velocity_per_day','acceleration_per_day2','zscore')
    scales = {key:st.pstdev(x[1][key] for x in train) or 1.0 for key in keys}
    ranked = sorted(train,key=lambda x:sum(((current[key]-x[1][key])/scales[key])**2 for key in keys))[:k]
    base = (1+sum(x[2] for x in train))/(2+len(train))
    analogue = (1+sum(x[2] for x in ranked))/(2+len(ranked))
    # Fixed weights: no unearned claim of learnt/calibrated probabilities.
    probability = .5*base+.5*analogue
    return {'version':VERSION,'mode':rows[-1]['mode'], 'as_of':as_of,
            'variable':variable,'region':region,'source':rows[-1]['source'],
            'horizon_days':horizon,'threshold':threshold,
            'definition':'any observed value strictly > threshold in (as_of,deadline]',
            'probability':probability,'base_rate':base,'analogue_probability':analogue,
            'features':current,'training_windows':len(train),
            'analogues':[{'origin':x[0],'outcome':x[2]} for x in ranked],
            'observation_ids':sorted(evidence_ids),
            'disclaimer':'Uncalibrated research estimate; overlapping windows are dependent.'}

def save_prediction(db, payload):
    snapshot = json.dumps(payload,sort_keys=True,allow_nan=False)
    ident = hashlib.sha256(snapshot.encode()).hexdigest()[:24]
    deadline = (date.fromisoformat(payload['as_of'])+timedelta(days=payload['horizon_days'])).isoformat()
    with db:
        db.execute('INSERT OR IGNORE INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
          (ident,payload['as_of'],deadline,payload['variable'],payload['region'],payload['threshold'],
           payload['horizon_days'],payload['probability'],'base+analogue@'+VERSION,snapshot,datetime.now(timezone.utc).isoformat()))
    return ident

def resolve(db, ident, as_of):
    day(as_of)
    p = db.execute('SELECT * FROM predictions WHERE id=?',(ident,)).fetchone()
    if not p:
        raise ValueError('unknown prediction')
    if as_of < p['deadline']:
        raise ValueError('resolution must be on or after deadline')
    snap = json.loads(p['snapshot'])
    rows = series(db,p['variable'],p['region'],as_of,snap['source'])
    outcome = target(rows,p['created_on'],p['horizon'],p['threshold'])
    if outcome is None:
        raise ValueError('incomplete coverage: cannot resolve a negative outcome')
    score = (p['probability']-outcome)**2
    evidence = json.dumps({'as_of':as_of,'observation_ids':[r['id'] for r in rows if p['created_on']<r['observed_on']<=p['deadline']]})
    old = db.execute('SELECT * FROM outcomes WHERE prediction_id=?',(ident,)).fetchone()
    if old:
        return dict(old)  # Immutable first resolution; subsequent revisions never rewrite.
    with db:
        db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?)',(ident,as_of,outcome,score,evidence))
    return {'prediction_id':ident,'occurred':outcome,'brier':score,'resolved_on':as_of}

def backtest(db, variable, region, as_of, horizon, threshold, source=None):
    rows = series(db,variable,region,as_of,source)
    results = []
    next_origin = ''
    for r in rows:
        origin = r['observed_on']
        if origin < next_origin:
            continue
        deadline = (date.fromisoformat(origin)+timedelta(days=horizon)).isoformat()
        if deadline > as_of:
            continue
        outcome = target(rows,origin,horizon,threshold)
        if outcome is None:
            continue
        try:
            p = forecast(db,variable,region,origin,horizon,threshold,source)
        except ValueError:
            continue
        results.append({'origin':origin,'p':p['probability'],'base':p['base_rate'],'outcome':outcome})
        next_origin = deadline
    if not results:
        raise ValueError('no eligible test windows')
    bs = st.mean((r['p']-r['outcome'])**2 for r in results)
    baseline = st.mean((r['base']-r['outcome'])**2 for r in results)
    bins = []
    for lo in range(0,100,20):
        members = [r for r in results if lo/100 <= r['p'] < (lo+20)/100 or (lo==80 and r['p']==1)]
        if members:
            bins.append({'range':[lo/100,(lo+20)/100],'n':len(members),'mean_p':st.mean(r['p'] for r in members),'observed_rate':st.mean(r['outcome'] for r in members)})
    return {'mode':rows[-1]['mode'],'n':len(results),'brier':bs,'baseline_brier':baseline,
            'skill_vs_base':1-bs/baseline if baseline else None,'calibration':bins,'windows':results,
            'limitation':'Final-known vintages used for evaluation outcomes; fixed origins, no model tuning on test results.'}

def report(db, output):
    records = db.execute('''SELECT p.*,o.occurred,o.brier FROM predictions p LEFT JOIN outcomes o
        ON p.id=o.prediction_id ORDER BY p.created_on DESC''').fetchall()
    out = ['# World Prediction Engine v0.1','', '研究用の未校正推定。DEMOは合成データであり、世界情勢の予測ではありません。','',
           '| ID | 予測日 | 対象 | 閾値 | 期限 | 推定確率 | モード | 結果 | Brier |',
           '|---|---|---|---:|---|---:|---|---|---|']
    for r in records:
        snap = json.loads(r['snapshot'])
        vals = [r['id'],r['created_on'],r['region']+'/'+r['variable'],r['threshold'],r['deadline'],f"{r['probability']:.1%}",snap['mode'],r['occurred'],r['brier']]
        out.append('| '+' | '.join(str(v).replace('|','/').replace('\n',' ') for v in vals)+' |')
    Path(output).write_text('\n'.join(out)+'\n',encoding='utf-8')
