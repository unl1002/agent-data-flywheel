#!/usr/bin/env python3
"""Render a self-contained HTML dashboard from analysis and optional validated AI reviews."""
import argparse
import base64
import gzip
import json
from pathlib import Path
import sys
from review_common import capabilities, fingerprint, load_json, no_review, validate_analysis
from merge_reviews import combine

def embedded_payload(bundle, compress=True):
    text = json.dumps(bundle, ensure_ascii=False, separators=(',',':'), allow_nan=False)
    if compress and len(text.encode('utf-8')) > 1000000:
        encoded = base64.b64encode(gzip.compress(text.encode('utf-8'), mtime=0)).decode('ascii')
        text = json.dumps({'encoding':'gzip-base64','data':encoded}, separators=(',',':'))
    return text.replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')

def dashboard_bundle(bundle, limit=240):
    """A bounded view keeps all memberships and referenced AI evidence intact."""
    analysis=bundle['analysis']
    referenced={ref for item in bundle['review'].get('items',[]) for group in ('evidence_refs','response_evidence_refs') for ref in item.get(group,[])}
    projected=dict(analysis);records=[]
    for record in analysis['records']:
        row=dict(record)
        facts=record.get('response_facts',[])
        decisive=[f['text'] for f in facts if '.ask.' in f['path'] or f['kind']=='error' or 'diagnoseConclusion' in f['path'] or 'diagnoseSuggestion' in f['path']]
        if decisive:
            row['answer']='\n'.join(dict.fromkeys(decisive))[-8000:]
        elif isinstance(row.get('answer'),str):
            row['answer']=row['answer'][:max(limit,800)]
        row['evidence']=[]
        for e in record.get('evidence',[]):
            ev=dict(e)
            if e['id'] not in referenced and isinstance(e.get('excerpt'),str) and len(e['excerpt'])>limit:
                ev['excerpt']=e['excerpt'][:limit];ev['truncated']=True
            row['evidence'].append(ev)
        if 'nodes' in record:
            row['nodes']=[dict(n,text=n.get('text','')[:limit],truncated=n.get('truncated',False) or len(n.get('text',''))>limit) for n in record['nodes']]
        if 'generations' in record:
            row['generations']=[dict(n,text=n.get('text','')[:limit]) for n in record['generations']]
        row.pop('response_facts',None)
        row['view_excerpt']=True
        records.append(row)
    projected['records']=records
    projected['provenance']=dict(analysis['provenance'],view_projection=True,
        view_note='看板保留全部指标血缘与被AI引用的证据，其他正文为展示摘录。CLI校验使用原analysis.json；完整原文按来源文件指纹读取。')
    return dict(bundle,analysis=projected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--analysis", type=Path, help="只渲染脚本统计；AI 评审状态为未运行")
    group.add_argument("--report", type=Path, help="merge_reviews.py 输出的 reviewed_analysis.json")
    parser.add_argument("--template", type=Path, default=Path(__file__).resolve().parents[1] / "assets" / "dashboard.html")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--uncompressed", action="store_true", help="为不支持 DecompressionStream 的旧浏览器生成未压缩页面")
    args = parser.parse_args()
    try:
        if args.analysis:
            bundle = {"schema_version": "1.0", "analysis": validate_analysis(load_json(args.analysis)), "review": no_review()}
        else:
            bundle = load_json(args.report)
            if not isinstance(bundle, dict) or not isinstance(bundle.get("review"), dict):
                raise ValueError("report 需要已合并的 analysis 与 review 对象")
            analysis = validate_analysis(bundle.get("analysis"))
            if bundle["review"].get("status") not in {"completed", "not_run"}:
                raise ValueError("report 的评审状态不合法")
            review = bundle["review"]
            if review["status"] == "completed":
                if not isinstance(review.get("capabilities"), dict):
                    raise ValueError("已评审报告缺少绑定的能力/评测依据，请重新合并")
                document = {"schema_version": "1.0", "analysis_fingerprint": review.get("analysis_fingerprint"),
                            "capabilities_fingerprint": review.get("capabilities_fingerprint"),
                            "scope": review.get("scope"), "items": review.get("items")}
                bundle = combine(analysis, [document], capabilities(data=review["capabilities"]))
            else:
                if review.get("items"):
                    raise ValueError("not_run 报告不能含已评审条目")
                bundle = {"schema_version": "1.0", "analysis": analysis, "review": no_review()}
        template = args.template.read_text(encoding="utf-8")
        if template.count("__FLYWHEEL_DATA__") != 1:
            raise ValueError("模板必须恰有一个数据占位符")
        bundle['review'].setdefault('analysis_fingerprint',fingerprint(bundle['analysis']))
        bundle=dashboard_bundle(bundle)
        payload = embedded_payload(bundle, compress=not args.uncompressed and 'decodeEmbedded' in template)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(template.replace("__FLYWHEEL_DATA__", payload), encoding="utf-8")
        print("已生成静态看板：%s" % args.out)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print("看板生成失败: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
