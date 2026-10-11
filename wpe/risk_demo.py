"""Offline, fictional scenario fixture and repeatable report generation."""
import argparse
import json
from pathlib import Path
from .core import connect
from .risk import import_bundle, evaluate, report, export, resolve_scenario

T = '2026-01-01T00:00:00+00:00'

def fixture():
    text = '架空の地域DEMOでは設備盗難の申告が記録され、保護窓口の対応遅延が報告された。これはテスト用の架空資料。'
    return {
        'evidence': [dict(id='demo-e1', source_url='https://example.org/fictional-report',
            publisher='架空研究所', title='架空の地域報告', source_text=text, start=0, end=len(text),
            quote=text, captured_at=T, published_at=None, claim_status='report', independence_group='fixture')],
        'metrics': [dict(id='equipment_reports', definition='一日あたりの設備盗難申告件数。実際の犯罪発生件数とは異なる。',
            unit='reports/day', measure='reported_incidents', population='架空地域DEMOの届出対象',
            source_id='fictional-registry', mode='demo', created_at=T),
            dict(id='response_delay', definition='保護窓口の平均初動時間', unit='hours', measure='service_capacity',
            population='架空地域DEMOの相談案件', source_id='fictional-registry', mode='demo', created_at=T)],
        'observations': [dict(id='demo-o1', metric_id='equipment_reports', region='DEMO', period_start=T, period_end=T,
            known_at=T, value=4, denominator=10000, evidence_ids=['demo-e1']),
            dict(id='demo-o2', metric_id='response_delay', region='DEMO', period_start=T, period_end=T,
            known_at=T, value=48, denominator=None, evidence_ids=['demo-e1'])],
        'scenarios': [dict(id='equipment-protection', revision=1, title='架空：設備盗難と対応遅延が重なる場合の備え',
            region='DEMO', harm_type='設備損失・相談後の再被害', legal_class='service_failure', created_at=T,
            deadline='2026-01-08T00:00:00+00:00', resolution_rule='期間内の再被害の有無を架空台帳で確認する',
            conditions=['転売の機会が存在する', '保護体制の不足が継続する'],
            precedent='架空報告を使った動作例。実在地域のリスクの根拠にはならない。',
            warning_indicators=[dict(metric_id='equipment_reports', op='ge', threshold=3, max_age_hours=72),
                                dict(metric_id='response_delay', op='gt', threshold=24, max_age_hours=72)],
            refutation_indicators=[], evidence_ids=['demo-e1'], severity='high', evidence_strength='limited',
            probability=None, probability_reason='予測を校正できる比較可能なデータがない',
            actions=[dict(action='管理記録と代替の相談経路を準備する', burden='low',
                          side_effects='準備に時間を要する', limitations='被害を完全に防ぐ保証はない')],
            release_condition='新しい観測で警戒条件が解消、反証成立、または期限を迎えたら見直す',
            limitations=['架空データ', '申告件数は被害発生率ではない', '腐敗や個人の犯罪性を認定しない'])],
        'judicial_records': [dict(id='demo-j1', case_id='fictional-case', region='DEMO', institution='架空検察',
            stage='prosecutor', status='non_prosecution', occurred_at=T, known_at=T,
            reason='架空資料では理由の詳細が不明', evidence_ids=['demo-e1'], corruption_status='not_assessed',
            corruption_evidence_ids=[], alternative='証拠不足、制度上の判断、対応不全などを区別する資料が必要')]
    }


def main():
    p = argparse.ArgumentParser(description='Offline fictional risk demo')
    p.add_argument('--db', default='data/risk-demo.sqlite')
    p.add_argument('--output', default='outputs/risk-demo')
    args = p.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    db = connect(args.db)
    try:
        imported = import_bundle(db, fixture())
        result = evaluate(db, '2026-01-02T00:00:00+00:00')
        report(db, out/'risk.html', result['as_of'])
        (out/'risk.json').write_text(json.dumps(export(db, result['as_of']), ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'mode': 'fictional demo', 'imported': imported, 'status': result['results'][0]['status'],
                          'new_alerts': len(result['new_alerts']), 'report': str((out/'risk.html').resolve())}, ensure_ascii=False, indent=2))
    finally:
        db.close()

if __name__ == '__main__':
    main()
