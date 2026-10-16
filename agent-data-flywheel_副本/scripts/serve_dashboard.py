#!/usr/bin/env python3
"""Serve one dashboard and source rows on loopback, with dataset and file hashes."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
from review_common import fingerprint, load_json

class SourceStore:
    def __init__(self, analysis, data_dir):
        self.analysis=analysis
        self.fingerprint=fingerprint(analysis)
        self.data_dir=Path(data_dir)
        self.refs={r['id']:(r['source'],r['raw_locator']['index'],r.get('locator_steps',[])) for r in analysis['records']}
        self.refs.update({r['ref']:(r['source'],r['index'],[]) for r in analysis.get('source_rows',[])})
        self.files=analysis['provenance'].get('files',{})
        self.cache={}
        self.lock=threading.Lock()

    def fetch(self, ref, dataset_fingerprint):
        if dataset_fingerprint!=self.fingerprint:
            raise ValueError('看板与服务的数据指纹不一致，请重新打开对应看板。')
        ref=ref.split('#node:')[0].split('#check:')[0]
        if ref not in self.refs:
            raise KeyError('未知来源引用')
        source,index,steps=self.refs[ref]
        spec=self.files[source]
        name=spec['name']
        if Path(name).name!=name or '/' in name or '\\' in name:
            raise ValueError('来源文件名无效')
        path=self.data_dir/name
        if path.resolve().parent!=self.data_dir.resolve():
            raise ValueError('来源文件必须位于data-dir目录内')
        stat=path.stat();signature=(stat.st_mtime_ns,stat.st_size)
        with self.lock:
            cached=self.cache.get(source)
            if cached is None or cached[0]!=signature:
                data=path.read_bytes()
                if hashlib.sha256(data).hexdigest()!=spec['sha256']:
                    raise ValueError('原始文件已改变，与统计来源指纹不符：'+name)
                rows=json.loads(data.decode('utf-8-sig'))
                if not isinstance(rows,list):raise ValueError('源文件必须为对象数组')
                cached=(signature,rows);self.cache[source]=cached
            rows=cached[1]
            if not isinstance(index,int) or not 0<=index<len(rows):raise ValueError('原始行索引超出范围')
            row=rows[index]
        return {'ref':ref,'source':source,'filename':name,'index':index,'json_pointer':'/'+str(index),
                'file_sha256':spec['sha256'],'locator_steps':steps,'raw':row}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',required=True,type=Path)
    p.add_argument('--html',required=True,type=Path)
    p.add_argument('--data-dir',required=True,type=Path)
    p.add_argument('--port',default=8765,type=int)
    args=p.parse_args()
    bundle=load_json(args.report);analysis=bundle.get('analysis',bundle)
    store=SourceStore(analysis,args.data_dir)
    html=args.html.read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            host=self.headers.get('Host','')
            allowed={'127.0.0.1:'+str(args.port),'localhost:'+str(args.port)}
            if host not in allowed:self.send_error(403);return
            origin=self.headers.get('Origin')
            if origin and origin not in {'http://'+h for h in allowed}:self.send_error(403);return
            request=urlsplit(self.path)
            if request.path in ('/','/dashboard.html'):
                body=html;ct='text/html; charset=utf-8';status=200
            elif request.path=='/api/source':
                query=parse_qs(request.query)
                try:
                    value=store.fetch(query.get('ref',[''])[0],query.get('fingerprint',[''])[0])
                    body=json.dumps(value,ensure_ascii=False).encode();status=200
                except (ValueError,KeyError,OSError,TypeError) as exc:
                    body=json.dumps({'error':str(exc)},ensure_ascii=False).encode();status=409
                ct='application/json; charset=utf-8'
            else:self.send_error(404);return
            self.send_response(status);self.send_header('Content-Type',ct)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print('看板与原文追溯：http://127.0.0.1:'+str(args.port)+' （仅本机）',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
