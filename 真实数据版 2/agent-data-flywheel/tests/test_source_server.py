import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from serve_dashboard import SourceStore

class StoreTest(unittest.TestCase):
    def test_exact_original_row_and_dataset_binding(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'对话详情.json';p.write_text('[{"requestId":"r","body":"{\\"detail\\":1}"}]')
            a={'records':[{'id':'dialogue:request:r','source':'dialogue','raw_locator':{'index':0,'line':1,'path':str(p)}}],
               'source_rows':[{'ref':'raw:dialogue:0','source':'dialogue','index':0,'line':1}],
               'provenance':{'files':{'dialogue':{'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}}}}
            store=SourceStore(a,Path(d))
            self.assertEqual(store.fetch('dialogue:request:r',store.fingerprint)['raw']['requestId'],'r')
            self.assertEqual(store.fetch('raw:dialogue:0',store.fingerprint)['raw']['body'],'{"detail":1}')
            with self.assertRaises(ValueError):store.fetch('dialogue:request:r','old-data')
            with self.assertRaises(KeyError):store.fetch('../../secrets',store.fingerprint)
            p.write_text('[{"requestId":"tampered"}]')
            with self.assertRaises(ValueError):store.fetch('raw:dialogue:0',store.fingerprint)

if __name__=='__main__':unittest.main()
