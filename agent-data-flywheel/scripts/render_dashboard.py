#!/usr/bin/env python3
"""Render a self-contained HTML dashboard from analysis and optional validated AI reviews."""
import argparse
import json
from pathlib import Path
import sys
from review_common import capabilities, load_json, no_review, validate_analysis
from merge_reviews import combine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--analysis", type=Path, help="只渲染脚本统计；AI 评审状态为未运行")
    group.add_argument("--report", type=Path, help="merge_reviews.py 输出的 reviewed_analysis.json")
    parser.add_argument("--template", type=Path, default=Path(__file__).resolve().parents[1] / "assets" / "dashboard.html")
    parser.add_argument("--out", required=True, type=Path)
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
        payload = json.dumps(bundle, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(template.replace("__FLYWHEEL_DATA__", payload), encoding="utf-8")
        print("已生成静态看板：%s" % args.out)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print("看板生成失败: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
