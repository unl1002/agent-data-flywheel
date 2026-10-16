import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
from analyze import Analyzer

class SourceTests(unittest.TestCase):
    def test_all_raw_occurrences_and_winner_remain_traceable(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'对话详情.json'
            detail={'id':'chat-1','conversationId':'c','question':'设备数量','askTime':1788230400000,'answers':[]}
            rows=[{'requestId':'r','updateTime':1,'body':json.dumps({'detail':json.dumps(detail)})},
                  {'requestId':'r','updateTime':2,'body':json.dumps({'detail':json.dumps(detail)})}]
            p.write_text(json.dumps(rows),encoding='utf-8')
            a=Analyzer().run({'dialogue':p})
            self.assertEqual(len(a['source_rows']),2)
            self.assertFalse(a['source_rows'][0]['log_retained'])
            self.assertTrue(a['source_rows'][1]['log_retained'])
            self.assertEqual(a['source_rows'][0]['duplicate_of'],'raw:dialogue:1')
            self.assertEqual(a['source_rows'][1]['retained_record_id'],a['records'][0]['id'])
            self.assertEqual(a['provenance']['files']['dialogue']['name'],p.name)

    def test_piu_final_answer_and_error_evidence_survive_progress_json(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'对话详情.json'
            payload={'answers':{'status':'failed','ask':{'text':'查询引擎异常，本次查询无法完成'},'errors':[{'key':'query_execution_failed','message':'timeout'}]}}
            detail={'id':'chat','question':'设备多少台','questionTime':1788230400000,'answers':[{'level':'ANSWER','type':'PIU','data':json.dumps(payload),'status':'MESSAGE'}]}
            p.write_text(json.dumps([{'requestId':'r','updateTime':1788230400001,'body':json.dumps({'detail':json.dumps(detail)})}]),encoding='utf-8')
            a=Analyzer(100).run({'dialogue':p});r=a['records'][0]
            self.assertEqual(r['time_field'],'body.detail.questionTime')
            self.assertTrue(any('查询引擎异常' in x['text'] for x in r['response_facts']))
            self.assertIn('piu_error',r['signals'])
            ids={e['id'] for e in r['evidence']}
            self.assertTrue(all(f['evidence_id'] in ids for f in r['response_facts']))
            self.assertTrue(any(s.get('decode')=='json' for s in r['locator_steps']))

if __name__=='__main__':unittest.main()
