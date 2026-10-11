"""Evidence-led regional scenarios, institutional records and local alert outbox.

No protected-trait individual scores, inferred guilt, or invented probabilities.
All imports are explicit analyst inputs. Missing/stale observations stay unknown.
"""
import argparse
import hashlib
import html
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .actors import timestamp as _timestamp, now
from .core import connect

SECTIONS = ('evidence', 'metrics', 'observations', 'scenarios', 'judicial_records')
MEASURES = ('reported_incidents', 'victim_survey', 'suspect_registration',
            'arrests', 'prosecutions', 'convictions', 'reporting_rate',
            'service_capacity', 'context', 'news_mentions')
CLAIMS = ('report', 'allegation', 'court_finding', 'statistic', 'hypothesis', 'correction')
STAGES = {'police': ('report_received', 'report_refused', 'investigation', 'protection'),
          'prosecutor': ('prosecution', 'non_prosecution'),
          'court': ('acquittal', 'conviction'),
          'oversight': ('complaint', 'investigation', 'finding')}
OPERATORS = {'gt': lambda x, y: x > y, 'ge': lambda x, y: x >= y,
             'lt': lambda x, y: x < y, 'le': lambda x, y: x <= y,
             'eq': lambda x, y: x == y}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be ISO8601 text with timezone')
    return _timestamp(value)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def schema(db):
    # Called only before transactions; executescript would otherwise commit them.
    db.executescript('''
    CREATE TABLE IF NOT EXISTS risk_records(
      kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
      available_at TEXT NOT NULL,recorded_at TEXT NOT NULL,payload TEXT NOT NULL,
      PRIMARY KEY(kind,id,revision));
    CREATE TABLE IF NOT EXISTS risk_evaluations(
      id INTEGER PRIMARY KEY,scenario_id TEXT NOT NULL,revision INTEGER NOT NULL,
      as_of TEXT NOT NULL,recorded_at TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS risk_alerts(
      id INTEGER PRIMARY KEY,evaluation_id INTEGER NOT NULL UNIQUE
      REFERENCES risk_evaluations(id),created_at TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS risk_resolutions(
      scenario_id TEXT NOT NULL,revision INTEGER NOT NULL,resolved_at TEXT NOT NULL,
      recorded_at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scenario_id,revision));
    ''')


def fields(p, required):
    if not isinstance(p, dict) or set(p) != set(required.split()):
        raise ValueError('fields must match: ' + required)


def string(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 100000:
        raise ValueError(label + ' must be nonempty text (max 100000 characters)')
    return value


def strings(value, label):
    if not isinstance(value, list) or not value:
        raise ValueError(label + ' must be a nonempty array')
    for item in value:
        string(item, label)


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite numeric data')


def record(db, kind, ident, cutoff=None):
    query = 'SELECT payload FROM risk_records WHERE kind=? AND id=?'
    args = [kind, ident]
    if cutoff:
        query += ' AND available_at<=?'
        args.append(cutoff)
    row = db.execute(query + ' ORDER BY revision DESC LIMIT 1', args).fetchone()
    if not row:
        raise ValueError('unknown or not-yet-available ' + kind + ': ' + str(ident))
    return json.loads(row[0])


def citations(db, ids, cutoff):
    strings(ids, 'evidence_ids')
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate evidence_ids')
    for ident in ids:
        record(db, 'evidence', ident, cutoff)


def validate(db, kind, p):
    p = dict(p) if isinstance(p, dict) else p
    if kind == 'evidence':
        fields(p, 'id source_url publisher title source_text start end quote captured_at published_at claim_status independence_group')
        for key in ('id', 'publisher', 'title', 'source_text', 'quote', 'independence_group'):
            string(p[key], key)
        string(p['source_url'], 'source_url')
        u = urlsplit(p['source_url'])
        if u.scheme != 'https' or not u.hostname or u.username or u.password:
            raise ValueError('source_url must be HTTPS without credentials')
        p['captured_at'] = timestamp(p['captured_at'])
        if p['published_at'] is not None:
            p['published_at'] = timestamp(p['published_at'])
            if p['published_at'] > p['captured_at']:
                raise ValueError('publication cannot follow capture')
        for key in ('start', 'end'):
            if type(p[key]) is not int:
                raise ValueError('quote offsets must be integers')
        if not 0 <= p['start'] < p['end'] <= len(p['source_text']):
            raise ValueError('invalid quote offsets')
        if p['source_text'][p['start']:p['end']] != p['quote']:
            raise ValueError('quote does not match source snapshot')
        if p['claim_status'] not in CLAIMS:
            raise ValueError('unknown claim status')
        return p, p['captured_at'], 1
    if kind == 'metrics':
        fields(p, 'id definition unit measure population source_id mode created_at')
        for key in ('id', 'definition', 'unit', 'population', 'source_id'):
            string(p[key], key)
        if p['measure'] not in MEASURES or p['mode'] not in ('demo', 'live'):
            raise ValueError('unknown measure or mode')
        p['created_at'] = timestamp(p['created_at'])
        return p, p['created_at'], 1
    if kind == 'observations':
        fields(p, 'id metric_id region period_start period_end known_at value denominator evidence_ids')
        for key in ('id', 'metric_id', 'region'):
            string(p[key], key)
        for key in ('period_start', 'period_end', 'known_at'):
            p[key] = timestamp(p[key])
        if not p['period_start'] <= p['period_end'] <= p['known_at']:
            raise ValueError('period must end by known_at')
        metric = record(db, 'metrics', p['metric_id'], p['known_at'])
        number(p['value'], 'value')
        if metric['measure'] != 'context' and p['value'] < 0:
            raise ValueError('counts/capacities/rates cannot be negative')
        if metric['measure'] == 'reporting_rate' and not 0 <= p['value'] <= 1:
            raise ValueError('reporting_rate must be in [0,1]')
        if p['denominator'] is not None:
            number(p['denominator'], 'denominator')
            if p['denominator'] <= 0:
                raise ValueError('denominator must be positive')
        citations(db, p['evidence_ids'], p['known_at'])
        return p, p['known_at'], 1
    if kind == 'scenarios':
        fields(p, 'id revision title region harm_type legal_class created_at deadline resolution_rule conditions precedent warning_indicators refutation_indicators evidence_ids severity evidence_strength probability probability_reason actions release_condition limitations')
        for key in ('id', 'title', 'region', 'harm_type', 'resolution_rule', 'precedent',
                    'probability_reason', 'release_condition'):
            string(p[key], key)
        if p['legal_class'] not in ('crime', 'harmful_practice', 'rights_violation', 'service_failure'):
            raise ValueError('unknown legal class')
        if p['severity'] not in ('low', 'moderate', 'high', 'critical'):
            raise ValueError('invalid severity')
        if p['evidence_strength'] not in ('limited', 'moderate', 'strong'):
            raise ValueError('invalid analyst evidence strength')
        if p['probability'] is not None:
            raise ValueError('scenario probability must be null; use forecast module for numeric forecasts')
        strings(p['conditions'], 'conditions')
        strings(p['limitations'], 'limitations')
        if type(p['revision']) is not int or p['revision'] < 1:
            raise ValueError('revision must be a positive integer')
        p['created_at'] = timestamp(p['created_at'])
        p['deadline'] = timestamp(p['deadline'])
        if p['deadline'] <= p['created_at']:
            raise ValueError('deadline must follow cutoff')
        citations(db, p['evidence_ids'], p['created_at'])
        modes = set()
        for key in ('warning_indicators', 'refutation_indicators'):
            if not isinstance(p[key], list) or (key == 'warning_indicators' and not p[key]):
                raise ValueError('warning_indicators required; refutation_indicators must be an array')
            for i in p[key]:
                fields(i, 'metric_id op threshold max_age_hours')
                metric = record(db, 'metrics', i['metric_id'], p['created_at'])
                modes.add(metric['mode'])
                if i['op'] not in OPERATORS:
                    raise ValueError('unknown comparison operator')
                number(i['threshold'], 'threshold')
                number(i['max_age_hours'], 'max_age_hours')
                if i['max_age_hours'] <= 0:
                    raise ValueError('max_age_hours must be positive')
        if len(modes) > 1:
            raise ValueError('demo and live indicators cannot be mixed')
        if not isinstance(p['actions'], list) or not p['actions']:
            raise ValueError('actions required')
        for a in p['actions']:
            fields(a, 'action burden side_effects limitations')
            for key in ('action', 'side_effects', 'limitations'):
                string(a[key], key)
            if a['burden'] not in ('low', 'moderate', 'high'):
                raise ValueError('invalid action burden')
        return p, p['created_at'], p['revision']
    if kind == 'judicial_records':
        fields(p, 'id case_id region institution stage status occurred_at known_at reason evidence_ids corruption_status corruption_evidence_ids alternative')
        for key in ('id', 'case_id', 'region', 'institution', 'reason', 'alternative'):
            string(p[key], key)
        if p['stage'] not in STAGES or p['status'] not in STAGES[p['stage']]:
            raise ValueError('status inconsistent with police/prosecutor/court/oversight stage')
        p['occurred_at'] = timestamp(p['occurred_at'])
        p['known_at'] = timestamp(p['known_at'])
        if p['occurred_at'] > p['known_at']:
            raise ValueError('decision must precede known_at')
        citations(db, p['evidence_ids'], p['known_at'])
        if p['corruption_status'] not in ('not_assessed', 'hypothesis', 'documented'):
            raise ValueError('invalid corruption status')
        if p['corruption_status'] == 'not_assessed':
            if p['corruption_evidence_ids'] != []:
                raise ValueError('not_assessed must have no corruption evidence')
        else:
            citations(db, p['corruption_evidence_ids'], p['known_at'])
            if p['corruption_status'] == 'documented':
                statuses = [record(db, 'evidence', x)['claim_status'] for x in p['corruption_evidence_ids']]
                if 'court_finding' not in statuses:
                    raise ValueError('documented corruption requires a court_finding source; this is analyst classification')
        return p, p['known_at'], 1
    raise ValueError('unknown section')


def import_bundle(db, bundle):
    schema(db)
    if not isinstance(bundle, dict) or set(bundle) - set(SECTIONS):
        raise ValueError('unknown bundle sections')
    for items in bundle.values():
        if not isinstance(items, list):
            raise ValueError('bundle sections must be arrays')
    counts = dict.fromkeys(SECTIONS, 0)
    with db:
        for kind in SECTIONS:
            for item in bundle.get(kind, []):
                p, available, revision = validate(db, kind, item)
                payload = canonical(p)
                old = db.execute('SELECT payload FROM risk_records WHERE kind=? AND id=? AND revision=?',
                                 (kind, p['id'], revision)).fetchone()
                if old:
                    if old[0] != payload:
                        raise ValueError('immutable record conflict; add a new scenario revision/evidence id')
                    continue
                previous = db.execute('SELECT revision,available_at FROM risk_records WHERE kind=? AND id=? ORDER BY revision DESC LIMIT 1',
                                      (kind, p['id'])).fetchone()
                if kind == 'scenarios':
                    expected = previous[0] + 1 if previous else 1
                    if revision != expected or (previous and available < previous[1]):
                        raise ValueError('scenario revisions must be consecutive and chronological')
                if kind == 'observations':
                    for other in records(db, 'observations', available):
                        if (other['metric_id'], other['region'], other['period_end'], other['known_at']) == (
                                p['metric_id'], p['region'], p['period_end'], p['known_at']):
                            raise ValueError('duplicate observation vintage; use a new known_at for corrections')
                db.execute('INSERT INTO risk_records VALUES(?,?,?,?,?,?)',
                           (kind, p['id'], revision, available, now(), payload))
                counts[kind] += 1
    return counts


def resolve_scenario(db, payload):
    """Explicit post-deadline judgement; absence needs declared coverage evidence."""
    schema(db)
    fields(payload, 'scenario_id revision resolved_at occurred evidence_ids rationale coverage_complete')
    p = dict(payload)
    p['resolved_at'] = timestamp(p['resolved_at'])
    if type(p['revision']) is not int or p['revision'] < 1:
        raise ValueError('revision must be a positive integer')
    row = db.execute('SELECT payload FROM risk_records WHERE kind=? AND id=? AND revision=?',
                     ('scenarios', p['scenario_id'], p['revision'])).fetchone()
    if not row:
        raise ValueError('unknown scenario revision')
    s = json.loads(row[0])
    if p['resolved_at'] < s['deadline']:
        raise ValueError('resolve only after deadline')
    if p['occurred'] is not None and type(p['occurred']) is not bool:
        raise ValueError('occurred must be true/false/null')
    if type(p['coverage_complete']) is not bool:
        raise ValueError('coverage_complete must be boolean')
    if p['occurred'] is False and not p['coverage_complete']:
        raise ValueError('negative outcome requires complete coverage declaration and evidence')
    string(p['rationale'], 'rationale')
    citations(db, p['evidence_ids'], p['resolved_at'])
    p['probability_score'] = None
    p['note'] = 'Scenario outcome, not a probability forecast; coverage is analyst-declared.'
    old = db.execute('SELECT payload FROM risk_resolutions WHERE scenario_id=? AND revision=?',
                     (p['scenario_id'], p['revision'])).fetchone()
    if old:
        if old[0] != canonical(p):
            raise ValueError('immutable outcome conflict')
        return p
    with db:
        db.execute('INSERT INTO risk_resolutions VALUES(?,?,?,?,?)',
                   (p['scenario_id'], p['revision'], p['resolved_at'], now(), canonical(p)))
    return p


def records(db, kind, cutoff):
    rows = db.execute('SELECT payload FROM risk_records WHERE kind=? AND available_at<=? ORDER BY id,revision',
                      (kind, cutoff)).fetchall()
    latest = {json.loads(r[0])['id']: json.loads(r[0]) for r in rows}
    return list(latest.values())


def check_indicator(db, indicator, region, cutoff):
    metric = record(db, 'metrics', indicator['metric_id'], cutoff)
    options = [p for p in records(db, 'observations', cutoff)
               if p['metric_id'] == metric['id'] and p['region'] == region]
    result = dict(indicator, definition=metric['definition'], unit=metric['unit'],
                  measure=metric['measure'], population=metric['population'], mode=metric['mode'])
    if not options:
        return dict(result, status='unknown', reason='missing observation', evidence_ids=[])
    # Latest period; latest known vintage at the cutoff, with deterministic ID tie-break.
    obs = max(options, key=lambda p: (p['period_end'], p['known_at'], p['id']))
    age = (datetime.fromisoformat(cutoff) - datetime.fromisoformat(obs['period_end'])).total_seconds() / 3600
    extra = dict(value=obs['value'], denominator=obs['denominator'], observation_id=obs['id'],
                 period_start=obs['period_start'], period_end=obs['period_end'], age_hours=age,
                 evidence_ids=obs['evidence_ids'])
    if age > indicator['max_age_hours']:
        return dict(result, **extra, status='unknown', reason='stale observation')
    return dict(result, **extra, status='met' if OPERATORS[indicator['op']](obs['value'], indicator['threshold']) else 'not_met')


def evaluate(db, as_of=None):
    schema(db)
    cutoff = timestamp(as_of or now())
    results, emitted = [], []
    # BEGIN IMMEDIATE prevents simultaneous runners from emitting duplicates.
    db.execute('BEGIN IMMEDIATE')
    try:
        previous_time = db.execute('SELECT MAX(as_of) FROM risk_evaluations').fetchone()[0]
        if previous_time and cutoff < previous_time:
            raise ValueError('evaluation time must be monotonic; use a separate DB for historical replay')
        for s in records(db, 'scenarios', cutoff):
            warnings = [check_indicator(db, i, s['region'], cutoff) for i in s['warning_indicators']]
            refutations = [check_indicator(db, i, s['region'], cutoff) for i in s['refutation_indicators']]
            if cutoff > s['deadline']:
                status = 'expired'
            elif any(i['status'] == 'met' for i in refutations):
                status = 'refuted'
            elif any(i['status'] == 'unknown' for i in warnings + refutations):
                status = 'unknown'
            elif all(i['status'] == 'met' for i in warnings):
                status = 'watch'
            else:
                status = 'not_triggered'
            ids = sorted(set(s['evidence_ids'] + [e for i in warnings + refutations for e in i['evidence_ids']]))
            result = dict(scenario=s, status=status, evaluated_at=cutoff, warning_checks=warnings,
                          refutation_checks=refutations, evidence_ids=ids,
                          recorded_at=now(),
                          note='Rule-based scenario alert; not an estimated crime probability or a finding of guilt.')
            # Dedupe unchanged state/revision/evidence, not changing evaluation timestamps.
            fp = hashlib.sha256(canonical([s['revision'], status, ids]).encode()).hexdigest()
            previous = db.execute('SELECT fingerprint,payload FROM risk_evaluations WHERE scenario_id=? ORDER BY id DESC LIMIT 1',
                                  (s['id'],)).fetchone()
            ev = db.execute('INSERT INTO risk_evaluations(scenario_id,revision,as_of,recorded_at,fingerprint,payload) VALUES(?,?,?,?,?,?)',
                            (s['id'], s['revision'], cutoff, now(), fp, canonical(result))).lastrowid
            if (not previous or previous[0] != fp) and (status in ('watch', 'refuted', 'expired', 'unknown') or
                    (previous and json.loads(previous[1])['status'] == 'watch')):
                event = dict(scenario_id=s['id'], status=status, region=s['region'], deadline=s['deadline'],
                             reason=result['note'], evidence_ids=ids, actions=s['actions'],
                             warning_checks=warnings, refutation_checks=refutations,
                             release_condition=s['release_condition'], evaluated_at=cutoff)
                db.execute('INSERT INTO risk_alerts(evaluation_id,created_at,payload) VALUES(?,?,?)',
                           (ev, now(), canonical(event)))
                emitted.append(event)
            results.append(result)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {'as_of': cutoff, 'results': results, 'new_alerts': emitted,
            'delivery': 'local SQLite outbox only; no email/SNS delivery'}


def export(db, as_of=None):
    schema(db)
    cutoff = timestamp(as_of or now())
    data = {k: records(db, k, cutoff) for k in SECTIONS}
    evaluations = db.execute('SELECT payload FROM risk_evaluations WHERE as_of<=? ORDER BY id', (cutoff,)).fetchall()
    data['evaluations'] = list({json.loads(r[0])['scenario']['id']: json.loads(r[0]) for r in evaluations}.values())
    data['alerts'] = [json.loads(r[0]) for r in db.execute('''SELECT a.payload FROM risk_alerts a
        JOIN risk_evaluations e ON e.id=a.evaluation_id WHERE e.as_of<=? ORDER BY a.id''', (cutoff,))]
    data['as_of'] = cutoff
    data['resolutions'] = [json.loads(r[0]) for r in db.execute(
        'SELECT payload FROM risk_resolutions WHERE resolved_at<=? ORDER BY scenario_id,revision', (cutoff,))]
    return data


def report(db, path, as_of=None):
    data = export(db, as_of)
    esc = lambda x: html.escape(str(x))
    labels = {'watch': '警戒条件成立', 'unknown': 'データ不足・古い情報', 'refuted': '反証条件成立',
              'expired': '期限切れ（結果未判定）', 'not_triggered': '警戒条件未成立（安全の保証ではない）'}
    parts = []
    for e in data['evaluations']:
        s = e['scenario']
        parts.append('<article><h2>' + esc(s['title']) + '</h2><p><b>' + esc(labels[e['status']]) + '</b></p>')
        for label, value in [('地域', s['region']), ('対象', s['harm_type']), ('法的分類', s['legal_class']),
                             ('期限', s['deadline']), ('判定条件', s['resolution_rule']), ('重大さ', s['severity']),
                             ('根拠の強さ（登録者評価）', s['evidence_strength']), ('確率', '算出不能：' + s['probability_reason']),
                             ('前提条件', s['conditions']), ('先例・根拠', s['precedent']),
                             ('警戒指標', e['warning_checks']), ('反証指標', e['refutation_checks']),
                             ('備えと負担', s['actions']), ('解除・見直し条件', s['release_condition']),
                             ('限界', s['limitations'])]:
            parts.append('<h3>' + esc(label) + '</h3><pre>' + esc(json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (list, dict)) else value) + '</pre>')
        parts.append('</article>')
    for section, label in [('judicial_records', '司法・保護の記録（記載だけで腐敗を認定しない）'),
                           ('evidence', '原文・出典・主張の状態'), ('alerts', '通知履歴'),
                           ('resolutions', '期限後の結果判定（確率の採点ではない）')]:
        parts.append('<h2>' + label + '</h2><pre>' + esc(json.dumps(data[section], ensure_ascii=False, indent=2)) + '</pre>')
    if not data['evaluations']:
        parts.insert(0, '<p>評価記録がありません。evaluateを実行してください。</p>')
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = '''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WPE 危機シナリオ</title><style>body{font-family:system-ui,sans-serif;max-width:1000px;margin:32px auto;padding:0 20px;background:#f4f6fa;color:#182334}article{background:white;padding:24px;margin:24px 0;border:1px solid #d5dce6;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}h3{margin-bottom:8px}</style>
<h1>WPE 危機シナリオ・制度対応</h1><p>確率予測と危機シナリオは別です。架空デモと実データは指標ごとに表示します。</p>'''
    temp = target.with_name(target.name + '.tmp')
    temp.write_text(document + '<p>情報基準時刻：' + esc(data['as_of']) + '</p>' + ''.join(parts) + '</html>', encoding='utf-8')
    temp.replace(target)
    return str(target.resolve())


def main():
    parser = argparse.ArgumentParser(description='Regional risk scenarios and institutional records')
    parser.add_argument('--db', default='data/runtime.sqlite')
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import'); imp.add_argument('path')
    res = sub.add_parser('resolve'); res.add_argument('path')
    ev = sub.add_parser('evaluate'); ev.add_argument('--as-of')
    ex = sub.add_parser('export'); ex.add_argument('--as-of'); ex.add_argument('--output', default='outputs/risk.json')
    rep = sub.add_parser('report'); rep.add_argument('--as-of'); rep.add_argument('--output', default='outputs/risk.html')
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    db = connect(args.db)
    try:
        if args.command == 'import':
            result = import_bundle(db, json.loads(Path(args.path).read_text(encoding='utf-8')))
        elif args.command == 'resolve':
            result = resolve_scenario(db, json.loads(Path(args.path).read_text(encoding='utf-8')))
        elif args.command == 'evaluate':
            result = evaluate(db, args.as_of)
        elif args.command == 'report':
            result = {'report': report(db, args.output, args.as_of)}
        else:
            target = Path(args.output); target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(export(db, args.as_of), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            result = {'export': str(target.resolve())}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError, TypeError, KeyError, sqlite3.Error) as exc:
        parser.exit(1, f'Error: {exc}\n')
    finally:
        db.close()


if __name__ == '__main__':
    main()
