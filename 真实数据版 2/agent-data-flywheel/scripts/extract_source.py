#!/usr/bin/env python3
"""Print an exact source row, verifying its file and analysis fingerprints."""
import argparse
import json
from pathlib import Path
from review_common import load_json
from serve_dashboard import SourceStore

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis',required=True,type=Path)
    p.add_argument('--data-dir',required=True,type=Path)
    p.add_argument('--ref',required=True)
    p.add_argument('--out',type=Path)
    args=p.parse_args();bundle=load_json(args.analysis);a=bundle.get('analysis',bundle)
    store=SourceStore(a,args.data_dir)
    result=store.fetch(args.ref,store.fingerprint)
    payload=json.dumps(result,ensure_ascii=False,indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(payload+'\n',encoding='utf-8')
    else:print(payload)

if __name__=='__main__':main()
