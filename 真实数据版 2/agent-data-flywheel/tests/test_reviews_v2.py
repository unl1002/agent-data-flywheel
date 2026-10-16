"""V2 response judgments remain distinct from capability and correctness claims."""
import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from merge_reviews import check_item, combine
from review_common import fingerprint


def millis(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


def record(rid, time=None, **changes):
    value = {'id': rid, 'source': 'dialogue', 'question': '查询设备数量', 'answer': '设备共12台',
             'time': time, 'signals': [], 'evidence': [
                 {'id': rid + ':e0', 'path': 'question', 'excerpt': '查询设备数量'},
                 {'id': rid + ':e1', 'path': 'answer', 'excerpt': '设备共12台'}]}
    value.update(changes)
    return value


def item(rid, response='supported', **changes):
    value = {'record_id': rid, 'validity': 'valid', 'intent': ['data_query'],
             'support': 'uncertain', 'badcase': 'not_observed', 'correctness': 'uncertain',
             'root_cause': 'unknown', 'reason': '只判断实际响应覆盖，未提供正确性评测。',
             'evidence_refs': [rid + ':e0', rid + ':e1'], 'capability_refs': [],
             'evaluation_refs': [], 'confidence': 0.9, 'suggested_action': '补充人工评测',
             'response_support': response, 'response_evidence_refs': [rid + ':e1'],
             'unsupported_reason': '响应未覆盖全部诉求' if response in {'unsupported', 'partial'} else '',
             'value_level': 'high', 'value_reason': '具有明确的运维决策用途',
             'opportunity_type': 'data_query'}
    value.update(changes)
    return value


class ReviewsV2Tests(unittest.TestCase):
    def setUp(self):
        self.caps = {'schema_version': '1.0', 'rules': [], 'evaluations': []}
        self.records = [
            record('dialogue:a', millis('2026-06-30T16:00:00')),
            record('dialogue:b', millis('2026-06-30T15:59:59')),
            record('dialogue:c', millis('2026-07-01T00:00:00')),
            record('dialogue:d', None),
            record('dialogue:e', millis('2026-07-01T00:00:00')),
            record('dialogue:f', 'invalid-time'),
            record('dialogue:unreviewed', millis('2026-07-01T00:00:00')),
            record('feedback:joined', None, source='feedback', linked_dialogue_id='dialogue:a')]
        self.analysis = {'schema_version': '1.0', 'provenance': {'mode': 'raw_json'},
                         'sources': {}, 'metrics': {}, 'distributions': {}, 'quality_issues': [],
                         'records': self.records}
        self.items = [item('dialogue:a'),
                      item('dialogue:b', 'unsupported', value_level='medium'),
                      item('dialogue:c', 'partial', value_level='none'),
                      item('dialogue:d', 'uncertain', value_level='uncertain', response_evidence_refs=[]),
                      item('dialogue:e', 'not_applicable', validity='invalid', value_level='none'),
                      item('dialogue:f', 'uncertain', validity='uncertain', value_level='uncertain')]

    def combine(self, items=None, scope='selected_records', caps=None):
        caps = self.caps if caps is None else caps
        document = {'schema_version': '1.0', 'scope': scope,
                    'analysis_fingerprint': fingerprint(self.analysis),
                    'capabilities_fingerprint': fingerprint(caps),
                    'items': self.items if items is None else items}
        return combine(self.analysis, [document], caps)

    def test_partial_v2_item_is_rejected_instead_of_silently_losing_fields(self):
        for field in ['response_support', 'response_evidence_refs', 'unsupported_reason',
                      'value_level', 'value_reason', 'opportunity_type']:
            with self.subTest(field=field):
                value = item('dialogue:a')
                del value[field]
                with self.assertRaises(ValueError):
                    check_item(value, self.records[0], self.caps)

    def test_response_judgment_requires_matching_current_record_evidence(self):
        for refs in [[], ['dialogue:b:e1'], ['made-up'], 'dialogue:a:e1']:
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                check_item(item('dialogue:a', response_evidence_refs=refs), self.records[0], self.caps)

    def test_invalid_v2_enums_and_non_string_reasons_are_rejected(self):
        for changes in [{'response_support': 'yes'}, {'response_support': []},
                        {'value_level': 'valuable'}, {'value_level': {}},
                        {'unsupported_reason': None}, {'value_reason': 4}, {'opportunity_type': []}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                check_item(item('dialogue:a', **changes), self.records[0], self.caps)
        with self.assertRaises(ValueError):
            check_item(item('dialogue:a', 'unsupported', unsupported_reason=' '), self.records[0], self.caps)

    def test_response_supported_does_not_authorize_capability_or_correctness(self):
        check_item(item('dialogue:a'), self.records[0], self.caps)
        for changes in [{'support': 'supported'}, {'correctness': 'correct'},
                        {'correctness': 'incorrect', 'badcase': 'confirmed'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                check_item(item('dialogue:a', **changes), self.records[0], self.caps)

    def test_invalid_question_cannot_claim_response_support(self):
        with self.assertRaises(ValueError):
            check_item(item('dialogue:a', validity='invalid'), self.records[0], self.caps)

    def test_response_rate_keeps_unknown_valid_items_in_denominator(self):
        metrics = self.combine()['review']['metrics']
        self.assertIn('response_support_rate', metrics)
        self.assertEqual((metrics['response_support_rate']['value'], metrics['response_support_rate']['numerator'],
                          metrics['response_support_rate']['denominator']), (0.25, 1, 4))
        self.assertAlmostEqual(metrics['response_judged_support_rate']['value'], 1 / 3)
        for key, want in [('response_supported', 1), ('response_unsupported', 1),
                          ('response_partial', 1), ('response_unknown', 1), ('value_questions', 2)]:
            self.assertEqual(metrics[key]['value'], want, key)
        self.assertAlmostEqual(metrics['valid_question_rate']['value'], 4 / 6)
        self.assertIsNone(metrics['accuracy']['value'])

    def test_legacy_items_still_merge_but_response_remains_unknown(self):
        legacy = item('dialogue:a')
        for key in ['response_support', 'response_evidence_refs', 'unsupported_reason',
                    'value_level', 'value_reason', 'opportunity_type']:
            del legacy[key]
        metrics = self.combine([legacy])['review']['metrics']
        self.assertIn('response_unknown', metrics)
        self.assertEqual(metrics['response_unknown']['value'], 1)
        self.assertEqual(metrics['response_support_rate']['value'], 0.0)
        self.assertIsNone(metrics['response_judged_support_rate']['value'])
        self.assertEqual(metrics['value_questions']['value'], 0)

    def test_every_review_metric_trace_can_reproduce_its_membership(self):
        review = self.combine()['review']
        self.assertIn('lineage', review)
        for key, metric in review['metrics'].items():
            with self.subTest(key=key):
                self.assertEqual(metric['trace_id'], 'review:' + key)
                trace = review['lineage'][metric['trace_id']]
                self.assertEqual(trace['value'], metric['value'])
                self.assertEqual(trace['unit'], metric['unit'])
                self.assertTrue(trace['formula'])
                if metric['unit'] == 'count':
                    self.assertEqual(len(trace['member_refs']), metric['value'])
                else:
                    self.assertEqual(len(trace['numerator_refs']), metric['numerator'])
                    self.assertEqual(len(trace['denominator_refs']), metric['denominator'])
                    self.assertLessEqual(set(trace['numerator_refs']), set(trace['denominator_refs']))
        rate = review['lineage']['review:response_support_rate']
        self.assertEqual(rate['numerator_refs'], ['dialogue:a'])
        self.assertEqual(rate['denominator_refs'], ['dialogue:a', 'dialogue:b', 'dialogue:c', 'dialogue:d'])
        self.assertIn('dialogue:unreviewed', rate['excluded_refs'])
        self.assertNotIn('feedback:joined', rate['excluded_refs'])

    def test_monthly_metrics_use_beijing_time_and_retain_unknown_dates(self):
        original = copy.deepcopy(self.analysis)
        review = self.combine()['review']
        self.assertIn('monthly', review)
        months = {x['month']: x['metrics'] for x in review['monthly']}
        self.assertEqual(set(months), {'2026-06', '2026-07', 'unknown'})
        self.assertEqual(months['2026-06']['reviewed_records']['value'], 1)
        self.assertEqual(months['2026-07']['reviewed_records']['value'], 3)
        self.assertEqual(months['2026-07']['valid_questions']['value'], 2)
        self.assertEqual(months['2026-07']['response_supported']['value'], 1)
        self.assertEqual(months['2026-07']['response_support_rate']['value'], 0.5)
        self.assertEqual(months['unknown']['reviewed_records']['value'], 2)
        self.assertEqual(months['unknown']['response_unknown']['value'], 1)
        for month, metrics in months.items():
            for key, metric in metrics.items():
                self.assertEqual(metric['trace_id'], 'review:monthly:' + month + ':' + key)
                self.assertIn(metric['trace_id'], review['lineage'])
        self.assertEqual(self.analysis, original)

    def test_empty_review_ratios_are_unknown_not_zero(self):
        metrics = self.combine([])['review']['metrics']
        for key in ['response_support_rate', 'response_judged_support_rate', 'valid_question_rate', 'accuracy']:
            self.assertIn(key, metrics)
            self.assertIsNone(metrics[key]['value'])
            self.assertEqual(metrics[key]['denominator'], 0)

    def test_all_question_scope_includes_orphan_feedback_but_skips_joined_feedback(self):
        self.analysis['records'] = [self.records[0], self.records[-1],
                                    record('feedback:orphan', None, source='feedback')]
        with self.assertRaises(ValueError):
            self.combine([item('dialogue:a')], scope='all_question_records')
        report = self.combine([item('dialogue:a'), item('feedback:orphan', 'uncertain', response_evidence_refs=[])],
                              scope='all_question_records')
        self.assertEqual(report['review']['metrics']['reviewed_records']['value'], 2)

    def test_accuracy_trace_only_contains_record_bound_evaluations(self):
        caps = copy.deepcopy(self.caps)
        caps['evaluations'] = [{'id': 'gold-a', 'record_id': 'dialogue:a', 'kind': 'human_label',
                                'correctness': 'correct', 'evidence_source': 'test-reviewer'}]
        report = self.combine([item('dialogue:a', correctness='correct', evaluation_refs=['gold-a']),
                               item('dialogue:b')], caps=caps)
        metric = report['review']['metrics']['accuracy']
        self.assertEqual((metric['value'], metric['numerator'], metric['denominator']), (1.0, 1, 1))
        self.assertIn('trace_id', metric)
        trace = report['review']['lineage'][metric['trace_id']]
        self.assertEqual(trace['denominator_refs'], ['dialogue:a'])
        self.assertIn('dialogue:b', trace['excluded_refs'])

    def test_primary_dialogue_denominator_excludes_orphan_feedback(self):
        self.analysis['records'].append(record('feedback:orphan', source='feedback'))
        report = self.combine([item('dialogue:a'), item('dialogue:b', 'unsupported'),
                               item('feedback:orphan', 'uncertain', response_evidence_refs=[])])
        review = report['review']
        self.assertEqual(review['metrics']['response_support_rate']['denominator'], 3)
        self.assertEqual(review['primary_metrics']['response_support_rate']['denominator'], 2)
        self.assertEqual(review['primary_metrics']['response_support_rate']['value'], 0.5)
        self.assertEqual(review['by_source_metrics']['feedback']['valid_questions']['value'], 1)
        self.assertEqual(review['primary_monthly'][0]['metrics']['reviewed_records']['value'], 1)
        for metric in review['primary_metrics'].values():
            trace = review['lineage'][metric['trace_id']]
            self.assertNotIn('feedback:orphan', trace['member_refs'])
            self.assertNotIn('feedback:orphan', trace['excluded_refs'])

    def test_unsupported_distributions_keep_exact_dialogue_evidence(self):
        self.analysis['records'].append(record('feedback:orphan', source='feedback'))
        review = self.combine([item('dialogue:a', 'partial', intent=['data_query', 'report']),
                               item('dialogue:b', 'unsupported'),
                               item('feedback:orphan', 'unsupported')])['review']
        rows = review['primary_distributions']['unsupported_intents']
        self.assertEqual({r['name']:r['value'] for r in rows}, {'data_query':2, 'report':1})
        for row in rows:
            trace = review['lineage'][row['trace_id']]
            self.assertEqual(row['value'], len(trace['member_refs']))
            self.assertNotIn('feedback:orphan', trace['member_refs'])

    def test_optional_response_gap_theme_is_ai_label_not_inferred_from_reason_keywords(self):
        review = self.combine([item('dialogue:a', 'unsupported', response_gap_type='field_mapping'),
                               item('dialogue:b', 'partial')])['review']
        rows = review['primary_distributions']['unsupported_reasons']
        self.assertEqual({r['name']:r['value'] for r in rows}, {'field_mapping':1,'unknown':1})
        with self.assertRaises(ValueError):
            check_item(item('dialogue:a', response_gap_type='made_up'), self.records[0], self.caps)

    def test_high_or_medium_value_requires_reason_and_action(self):
        for level in ('high','medium'):
            for changes in ({'value_reason':''},{'value_reason':' '},{'suggested_action':''},
                            {'suggested_action':None}):
                with self.subTest(level=level,changes=changes), self.assertRaises(ValueError):
                    check_item(item('dialogue:a',value_level=level,**changes),self.records[0],self.caps)


if __name__ == '__main__':
    unittest.main()
