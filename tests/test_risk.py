import copy
import json
import tempfile
import unittest
from pathlib import Path

from wpe.core import connect
from wpe.risk import import_bundle, evaluate, export, report, resolve_scenario
from wpe.risk_demo import fixture, T

A = '2026-01-02T00:00:00+00:00'

class RiskTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
        self.bundle = fixture()
        import_bundle(self.db, self.bundle)
    def tearDown(self):
        self.db.close()
    def outcome(self):
        return dict(scenario_id='equipment-protection', revision=1, resolved_at='2026-01-09T00:00:00+00:00',
                    occurred=None, evidence_ids=['demo-e1'], rationale='Outcome unknown', coverage_complete=False)
    def test_alert_idempotence(self):
        a = evaluate(self.db, A)
        b = evaluate(self.db, A)
        self.assertEqual(a['results'][0]['status'], 'watch')
        self.assertEqual(len(a['new_alerts']), 1)
        self.assertEqual(b['new_alerts'], [])
        self.assertEqual(len(export(self.db, A)['alerts']), 1)
    def test_missing_is_unknown(self):
        self.db.execute("DELETE FROM risk_records WHERE kind='observations'"); self.db.commit()
        result = evaluate(self.db, A)['results'][0]
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['warning_checks'][0]['reason'], 'missing observation')
    def test_stale_is_unknown(self):
        a = evaluate(self.db, '2026-01-05T00:00:00+00:00')
        self.assertEqual(a['results'][0]['status'], 'unknown')
    def test_deadline_is_not_negative_outcome(self):
        a = evaluate(self.db, '2026-01-09T00:00:00+00:00')
        self.assertEqual(a['results'][0]['status'], 'expired')
        self.assertEqual(export(self.db)['resolutions'], [])
    def test_future_observation_excluded(self):
        p = copy.deepcopy(self.bundle['observations'][0]); p.update(id='future', value=0, known_at='2026-01-04T00:00:00+00:00')
        import_bundle(self.db, {'observations': [p]})
        self.assertEqual(evaluate(self.db, A)['results'][0]['warning_checks'][0]['value'], 4)
    def test_no_future_evidence_in_scenario(self):
        e = copy.deepcopy(self.bundle['evidence'][0]); e.update(id='future-e', captured_at='2026-01-03T00:00:00+00:00')
        s = copy.deepcopy(self.bundle['scenarios'][0]); s.update(id='bad', evidence_ids=['future-e'])
        with self.assertRaises(ValueError): import_bundle(self.db, {'evidence': [e], 'scenarios': [s]})
        self.assertEqual(len(export(self.db)['evidence']), 1)  # atomic rollback
    def test_exact_quote(self):
        e = copy.deepcopy(self.bundle['evidence'][0]); e.update(id='bad-e', quote='unmatched')
        with self.assertRaises(ValueError): import_bundle(self.db, {'evidence': [e]})
    def test_no_invented_probability(self):
        s = copy.deepcopy(self.bundle['scenarios'][0]); s.update(id='bad', probability=.9)
        with self.assertRaises(ValueError): import_bundle(self.db, {'scenarios': [s]})
    def test_unknown_field_rejected(self):
        s = copy.deepcopy(self.bundle['scenarios'][0]); s['ethnic_individual_score'] = 9
        with self.assertRaises(ValueError): import_bundle(self.db, {'scenarios': [s]})
    def test_corruption_not_inferred(self):
        j = export(self.db)['judicial_records'][0]
        self.assertEqual(j['status'], 'non_prosecution')
        self.assertEqual(j['corruption_status'], 'not_assessed')
    def test_stage_mismatch_rejected(self):
        j = copy.deepcopy(self.bundle['judicial_records'][0]); j.update(id='bad', status='acquittal')
        with self.assertRaises(ValueError): import_bundle(self.db, {'judicial_records': [j]})
    def test_documented_corruption_needs_source(self):
        j = copy.deepcopy(self.bundle['judicial_records'][0]); j.update(id='bad', corruption_status='documented', corruption_evidence_ids=['demo-e1'])
        with self.assertRaises(ValueError): import_bundle(self.db, {'judicial_records': [j]})
    def test_import_idempotence_and_immutability(self):
        self.assertEqual(sum(import_bundle(self.db, self.bundle).values()), 0)
        s = copy.deepcopy(self.bundle['scenarios'][0]); s['title'] = 'changed'
        with self.assertRaises(ValueError): import_bundle(self.db, {'scenarios': [s]})
    def test_revision_point_in_time(self):
        s = copy.deepcopy(self.bundle['scenarios'][0]); s.update(revision=2, created_at='2026-01-03T00:00:00+00:00', title='revision2')
        import_bundle(self.db, {'scenarios': [s]})
        self.assertEqual(evaluate(self.db, A)['results'][0]['scenario']['revision'], 1)
        self.assertEqual(evaluate(self.db, '2026-01-03T00:00:00+00:00')['results'][0]['scenario']['revision'], 2)
    def test_revision_gap_rejected(self):
        s = copy.deepcopy(self.bundle['scenarios'][0]); s['revision'] = 3
        with self.assertRaises(ValueError): import_bundle(self.db, {'scenarios': [s]})
    def test_dedup_and_reactivation(self):
        evaluate(self.db, A)
        p = copy.deepcopy(self.bundle['observations'][0]); p.update(id='lower', value=0, period_start=A, period_end=A, known_at=A)
        import_bundle(self.db, {'observations': [p]})
        a = evaluate(self.db, A)
        self.assertEqual(a['results'][0]['status'], 'not_triggered')
        self.assertEqual(len(a['new_alerts']), 1)
        p.update(id='higher', value=4, period_start='2026-01-03T00:00:00+00:00', period_end='2026-01-03T00:00:00+00:00', known_at='2026-01-03T00:00:00+00:00')
        import_bundle(self.db, {'observations': [p]})
        self.assertEqual(len(evaluate(self.db, p['known_at'])['new_alerts']), 1)
    def test_refutation_precedence(self):
        s = copy.deepcopy(self.bundle['scenarios'][0]); s.update(revision=2)
        s['refutation_indicators'] = [dict(metric_id='response_delay', op='gt', threshold=1, max_age_hours=72)]
        import_bundle(self.db, {'scenarios': [s]})
        self.assertEqual(evaluate(self.db, A)['results'][0]['status'], 'refuted')
    def test_missing_refutation_prevents_false_confidence(self):
        s = copy.deepcopy(self.bundle['scenarios'][0]); s.update(revision=2)
        s['refutation_indicators'] = [dict(metric_id='response_delay', op='lt', threshold=1, max_age_hours=1)]
        import_bundle(self.db, {'scenarios': [s]})
        self.assertEqual(evaluate(self.db, A)['results'][0]['status'], 'unknown')
    def test_monotonic_clock(self):
        evaluate(self.db, A)
        with self.assertRaises(ValueError): evaluate(self.db, T)
    def test_negative_outcome_needs_coverage(self):
        p = self.outcome(); p['occurred'] = False
        with self.assertRaises(ValueError): resolve_scenario(self.db, p)
        p['coverage_complete'] = True
        self.assertIsNone(resolve_scenario(self.db, p)['probability_score'])
    def test_immutable_resolution(self):
        p = self.outcome(); resolve_scenario(self.db, p)
        p['occurred'] = True
        with self.assertRaises(ValueError): resolve_scenario(self.db, p)
    def test_no_premature_resolution(self):
        p = self.outcome(); p['resolved_at'] = A
        with self.assertRaises(ValueError): resolve_scenario(self.db, p)
    def test_invalid_numbers_and_vintages(self):
        for value in (True, float('nan'), float('inf'), -1):
            p = copy.deepcopy(self.bundle['observations'][0]); p.update(id='bad', value=value)
            with self.assertRaises(ValueError): import_bundle(self.db, {'observations': [p]})
        p = copy.deepcopy(self.bundle['observations'][0]); p.update(id='same-vintage', value=5)
        with self.assertRaises(ValueError): import_bundle(self.db, {'observations': [p]})
    def test_html_escaping(self):
        s = copy.deepcopy(self.bundle['scenarios'][0]); s.update(revision=2, title='<script>alert(1)</script>')
        import_bundle(self.db, {'scenarios': [s]}); evaluate(self.db, A)
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp)/'report.html'; report(self.db, f, A)
            text = f.read_text()
            self.assertNotIn('<script>', text)
            self.assertIn('&lt;script&gt;', text)
    def test_demo_live_mixing(self):
        m = copy.deepcopy(self.bundle['metrics'][0]); m.update(id='live', mode='live')
        s = copy.deepcopy(self.bundle['scenarios'][0]); s.update(revision=2)
        s['warning_indicators'][0]['metric_id'] = 'live'
        with self.assertRaises(ValueError): import_bundle(self.db, {'metrics': [m], 'scenarios': [s]})
    def test_timestamp_type_rejected(self):
        s = copy.deepcopy(self.bundle['scenarios'][0]); s.update(id='bad', created_at=123)
        with self.assertRaises(ValueError): import_bundle(self.db, {'scenarios': [s]})
    def test_collection_cycle_updates_risk_report(self):
        from datetime import datetime
        from wpe.autonomous import run_cycle
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'runtime.sqlite'
            db = connect(path); import_bundle(db, self.bundle); db.close()
            config = {'sources': [{'id': 'fixture', 'type': 'rss', 'url': 'https://example.org/feed', 'interval_seconds': 3600}]}
            result = run_cycle(config, path, Path(tmp)/'out', datetime.fromisoformat(A),
                               lambda _: b'<rss><channel></channel></rss>')
            self.assertEqual(result['risk_scenarios']['results'][0]['status'], 'watch')
            self.assertTrue((Path(tmp)/'out'/'risk.html').is_file())

if __name__ == '__main__': unittest.main()
