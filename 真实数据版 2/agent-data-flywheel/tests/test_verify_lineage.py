"""Independent audit regression cases; fixtures do not call operations.enrich."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'verify_lineage.py'


def load_verifier():
    # A missing implementation is an explicit failed behavior assertion.
    if not SCRIPT.exists():
        raise AssertionError('independent lineage verifier is not implemented')
    spec = importlib.util.spec_from_file_location('independent_verifier', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def trace(value, members=(), unit='count', **kwargs):
    return dict(label='fixture', value=value, unit=unit, formula='fixture formula',
        member_refs=list(members), numerator_refs=[], denominator_refs=[], excluded_refs=[], **kwargs)


def fixture():
    records = [dict(id='d'+str(i), source='dialogue', chat_id='chat'+str(i),
                   conversation_id='s' if i < 3 else 't', project_id='p',
                   time=1784563200000, cost_ms=cost, evidence=[], nodes=[])
               for i, cost in [(1, 5), (2, 7), (3, 100)]]
    a = dict(schema_version='1.0', provenance={'mode': 'raw_json', 'files': {}},
             sources={}, records=records, source_rows=[], metrics={}, distributions={},
             quality_issues=[], lineage={})
    return a


class VerifyLineageTests(unittest.TestCase):
    def verify(self, analysis, **kwargs):
        return load_verifier().verify(analysis, check_sources=False, **kwargs)

    def codes(self, result):
        return {e['code'] for e in result['errors']}

    def test_distinct_count_is_not_member_count(self):
        a = fixture()
        a['lineage']['metric:conversation_count'] = trace(2, ['d1', 'd2', 'd3'],
            entity_type='conversation', group_values={'d1':'s', 'd2':'s', 'd3':'t'})
        a['metrics']['conversation_count'] = dict(value=2, unit='count', trace_id='metric:conversation_count')
        self.assertTrue(self.verify(a)['ok'])
        a['lineage']['metric:conversation_count']['group_values']['d1'] = 'invented'
        self.assertIn('group_binding', self.codes(self.verify(a)))

    def test_count_and_dangling_child_reference_are_rejected(self):
        a = fixture()
        a['lineage']['bad-count'] = trace(2, ['d1'])
        a['lineage']['bad-node'] = trace(1, ['d1#node:99'])
        result = self.verify(a)
        self.assertIn('count_value', self.codes(result))
        self.assertIn('unresolved_ref', self.codes(result))

    def test_zero_denominator_is_unknown_and_numerator_is_subset(self):
        a = fixture()
        a['lineage']['zero'] = trace(None, unit='ratio')
        self.assertTrue(self.verify(a)['ok'])
        a['lineage']['zero']['value'] = 0
        a['lineage']['wrong'] = trace(1, ['d1'], unit='ratio')
        a['lineage']['wrong'].update(numerator_refs=['d2'], denominator_refs=['d1'])
        self.assertIn('ratio_value', self.codes(self.verify(a)))
        self.assertIn('numerator_subset', self.codes(self.verify(a)))

    def test_p95_checks_rank_all_samples_and_binding(self):
        a = fixture()
        a['lineage']['metric:dialogue_cost_p95_ms'] = trace(100, ['d1','d2','d3'], unit='ms',
            entity_type='measurement', sample_values={'d1':5,'d2':7,'d3':100}, rank=3, selected_ref='d3')
        a['lineage']['metric:dialogue_cost_p95_ms']['denominator_refs'] = ['d1','d2','d3']
        self.assertTrue(self.verify(a)['ok'])
        a['lineage']['metric:dialogue_cost_p95_ms']['sample_values']['d1'] = 999
        result = self.verify(a)
        self.assertIn('sample_binding', self.codes(result))
        self.assertIn('p95_value', self.codes(result))

    def test_quality_issue_count_preserves_repeated_record_refs(self):
        a = fixture()
        a['quality_issues'] = [dict(source='dialogue', severity='error', record_id='d1', code='a'),
                               dict(source='dialogue', severity='error', record_id='d1', code='b')]
        a['sources']['dialogue'] = dict(errors=2, trace_ids={'errors':'source:dialogue:errors'})
        a['lineage']['source:dialogue:errors'] = trace(2, ['d1','d1'], entity_type='quality_issue',
            source='dialogue', issue_indexes={'d1':[0,1]})
        self.assertTrue(self.verify(a)['ok'])
        a['lineage']['source:dialogue:errors']['issue_indexes']['d1'] = [0,0]
        self.assertIn('issue_indexes', self.codes(self.verify(a)))

    def test_check_original_index_can_resolve_via_omitted_check_evidence(self):
        a = fixture()
        a['records'].append(dict(id='g', source='guardrail', checks=[],
            evidence=[dict(id='ge', path='responseData.checkResults[4]', excerpt='invalid')]))
        a['lineage']['excluded-check'] = trace(0, entity_type='guardrail_check')
        a['lineage']['excluded-check']['excluded_refs'] = ['g#check:4']
        self.assertTrue(self.verify(a)['ok'])

    def test_metric_alias_and_distribution_alias_must_match_trace(self):
        a = fixture()
        a['lineage']['dist'] = trace(1, ['d1'])
        a['metrics']['arbitrary'] = dict(value=2, unit='count', trace_id='dist')
        a['distributions']['anything'] = [dict(name='x', value=2, trace_id='dist')]
        self.assertIn('trace_value', self.codes(self.verify(a)))

    def test_actual_file_hash_row_hash_index_and_line_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'feedback.json'
            rows = [{'requestId':'x','createTime':1784563200000,'text':'$(touch NEVER_EXECUTED)'},
                    {'requestId':'y','createTime':1784563200001}]
            p.write_text(json.dumps(rows, indent=2), encoding='utf-8')
            content = p.read_bytes()
            a = fixture()
            a['records'] = []
            a['provenance']['files'] = {'feedback':dict(path=str(p), name=p.name,
                sha256=hashlib.sha256(content).hexdigest(), bytes=len(content), record_count=2)}
            # First object is line 2; locate the second object independently.
            second_line = p.read_text().count('\n',0,p.read_text().index('  {',p.read_text().index('  {')+1))+1
            a['source_rows'] = [dict(ref='raw:feedback:'+str(i),source='feedback',index=i,
                line=line, sha256=digest(row), log_retained=True, time=row['createTime'])
                for i,(row,line) in enumerate(zip(rows,[2,second_line]))]
            module = load_verifier()
            self.assertTrue(module.verify(a)['ok'])
            a['source_rows'][0]['sha256'] = a['provenance']['files']['feedback']['sha256']
            a['source_rows'][1]['line'] = 500
            bad = module.verify(a)
            self.assertIn('row_hash', self.codes(bad))
            self.assertIn('raw_line', self.codes(bad))
            a['source_rows'][1]['index'] = 200
            self.assertIn('raw_index', self.codes(module.verify(a)))
            p.write_text(p.read_text()+'\n', encoding='utf-8')
            self.assertIn('source_hash', self.codes(module.verify(a)))
            self.assertFalse((Path(tmp)/'NEVER_EXECUTED').exists())

    def test_ai_review_lineage_fingerprints_evidence_and_denominator(self):
        a = fixture()
        for r in a['records']:
            r['question']='q';r['evidence']=[dict(id=r['id']+':e',excerpt='response')]
        caps={'schema_version':'1.0','rules':[],'evaluations':[]}
        items=[dict(record_id='d1', validity='valid', intent=['data_query'], support='uncertain',
                    response_support='supported', response_evidence_refs=['d1:e'], evidence_refs=['d1:e'],
                    correctness='uncertain', badcase='not_observed', value_level='none'),
               dict(record_id='d2', validity='valid', intent=['data_query'], support='uncertain',
                    response_support='uncertain', response_evidence_refs=[], evidence_refs=['d2:e'],
                    correctness='uncertain', badcase='uncertain', value_level='none')]
        t=trace(.5,['d1','d2'],unit='ratio');t.update(numerator_refs=['d1'],denominator_refs=['d1','d2'],excluded_refs=['d3'])
        review=dict(status='completed',scope='selected_records',items=items,analysis_fingerprint=digest(a),
            capabilities_fingerprint=digest(caps), capabilities=caps,
            metrics={'response_support_rate':dict(value=.5,unit='ratio',numerator=1,denominator=2,trace_id='review:response_support_rate')},
            lineage={'review:response_support_rate':t})
        self.assertTrue(self.verify(a,review=review)['ok'])
        review['lineage']['review:response_support_rate']['denominator_refs']=['d1']
        self.assertIn('review_membership',self.codes(self.verify(a,review=review)))
        review['analysis_fingerprint']='bad'
        review['items'][0]['response_evidence_refs']=['d3:e']
        result=self.verify(a,review=review)
        self.assertIn('analysis_fingerprint',self.codes(result))
        self.assertIn('review_evidence',self.codes(result))

    def test_optional_response_gap_distribution_is_not_value_opportunity(self):
        a=fixture();caps={'schema_version':'1.0','rules':[],'evaluations':[]}
        a['records'][0]['evidence']=[dict(id='d1:e',excerpt='error')]
        item=dict(record_id='d1',validity='valid',intent=['data_query'],support='uncertain',
                  response_support='unsupported',response_gap_type='execution_failure',
                  response_evidence_refs=['d1:e'],evidence_refs=['d1:e'],
                  correctness='uncertain',badcase='candidate',value_level='none')
        t=trace(1,['d1']);t['excluded_refs']=[]
        review=dict(status='completed',scope='selected_records',items=[item],
                    analysis_fingerprint=digest(a),capabilities_fingerprint=digest(caps),capabilities=caps,
                    metrics={},lineage={'gap':t},primary_distributions={'unsupported_reasons':[
                        dict(name='execution_failure',value=1,trace_id='gap')]})
        self.assertTrue(self.verify(a,review=review)['ok'])
        review['primary_distributions']['unsupported_reasons'][0]['name']='field_mapping'
        self.assertIn('review_membership',self.codes(self.verify(a,review=review)))

    def test_cli_is_read_only_and_returns_failure_for_bad_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'analysis.json';out=Path(tmp)/'audit.json'
            a=fixture();a['lineage']['bad']=trace(2,['d1'])
            p.write_text(json.dumps(a),encoding='utf-8');before=p.read_bytes()
            result=subprocess.run([sys.executable,str(SCRIPT),'--analysis',str(p), '--skip-source-files','--out',str(out)],capture_output=True,text=True)
            self.assertEqual(result.returncode,1)
            self.assertEqual(p.read_bytes(),before)
            self.assertIn('count_value',{e['code'] for e in json.loads(out.read_text())['errors']})


if __name__ == '__main__':
    unittest.main()
