import sys
from pathlib import Path
import unittest
import base64
import gzip
import json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from render_dashboard import dashboard_bundle, embedded_payload

class ProjectionTest(unittest.TestCase):
    def test_preview_keeps_review_evidence_and_does_not_change_canonical_analysis(self):
        a={'records':[{'id':'d','source':'dialogue','answer':'x'*1000,'evidence':[{'id':'d:e0','path':'answer','excerpt':'a'*1000},{'id':'d:e1','path':'progress','excerpt':'b'*1000}],'nodes':[{'text':'z'*1000,'original_index':7}]}],'provenance':{}}
        b={'analysis':a,'review':{'items':[{'record_id':'d','evidence_refs':['d:e0']}],'analysis_fingerprint':'fixed'}}
        out=dashboard_bundle(b,100)
        self.assertEqual(len(out['analysis']['records'][0]['evidence'][0]['excerpt']),1000)
        self.assertEqual(len(out['analysis']['records'][0]['evidence'][1]['excerpt']),100)
        self.assertEqual(len(a['records'][0]['evidence'][1]['excerpt']),1000)
        self.assertEqual(out['review']['analysis_fingerprint'],'fixed')
        self.assertTrue(out['analysis']['provenance']['view_projection'])

    def test_large_embedded_payload_roundtrips_without_losing_members(self):
        bundle={'records':['中文</script>\u2028'*150000], 'members':['a','b']}
        wrapper=json.loads(embedded_payload(bundle))
        self.assertEqual(wrapper['encoding'],'gzip-base64')
        restored=json.loads(gzip.decompress(base64.b64decode(wrapper['data'])))
        self.assertEqual(restored,bundle)
        self.assertNotIn('</script>',embedded_payload(bundle,compress=False))

if __name__=='__main__':unittest.main()
