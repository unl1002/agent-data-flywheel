#!/usr/bin/env python3
"""Validate AI evidence references and aggregate only the reviewed records."""
import argparse
import math
from pathlib import Path
import sys
from review_common import capabilities, fingerprint, load_json, metric, question_records, validate_analysis, write_json

ENUMS = {
    "validity": {"valid", "invalid", "uncertain"},
    "support": {"supported", "unsupported", "partial", "uncertain", "not_applicable"},
    "badcase": {"confirmed", "candidate", "not_observed", "uncertain"},
    "correctness": {"correct", "incorrect", "uncertain", "not_applicable"},
    "root_cause": {"strategy", "rag", "model", "data", "product_gap", "safety", "unknown"}}
INTENTS = {"data_query", "diagnosis", "knowledge", "report", "conversation", "other", "uncertain"}


def check_item(item, record, caps):
    if not isinstance(item, dict):
        raise ValueError("评审项必须为对象")
    rid = record["id"]
    for key, values in ENUMS.items():
        if item.get(key) not in values:
            raise ValueError(rid + " 的 " + key + " 枚举无效")
    intents = item.get("intent")
    if not isinstance(intents, list) or not intents or any(x not in INTENTS for x in intents):
        raise ValueError(rid + " 的 intent 必须是非空枚举数组")
    if not isinstance(item.get("reason"), str) or not item["reason"].strip():
        raise ValueError(rid + " 缺少判定原因")
    confidence = item.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError(rid + " 的 confidence 必须在 0–1 之间")
    refs = item.get("evidence_refs")
    available = {e.get("id") for e in record["evidence"] if isinstance(e, dict)}
    if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or x not in available for x in refs):
        raise ValueError(rid + " 缺少有效的本记录 evidence_refs")
    rules = {r["id"]: r for r in caps["rules"]}
    cap_refs = item.get("capability_refs", [])
    if not isinstance(cap_refs, list) or any(not isinstance(x, str) or x not in rules for x in cap_refs):
        raise ValueError(rid + " 引用了不存在的能力规则")
    if item["support"] in {"supported", "unsupported", "partial"}:
        if not cap_refs or not any(rules[x]["status"] == item["support"] for x in cap_refs):
            raise ValueError(rid + " 的确定支持结论缺少匹配能力依据")
    if item["validity"] == "invalid" and item["support"] not in {"uncertain", "not_applicable"}:
        raise ValueError(rid + " 无效问题不能计入已确认的支持/不支持能力")
    evals = {e["id"]: e for e in caps["evaluations"]}
    eval_refs = item.get("evaluation_refs", [])
    if not isinstance(eval_refs, list) or any(not isinstance(x, str) or x not in evals for x in eval_refs):
        raise ValueError(rid + " 引用了不存在的评测依据")
    for ref in eval_refs:
        evaluation = evals[ref]
        if evaluation["record_id"] != rid or evaluation["correctness"] != item["correctness"]:
            raise ValueError(rid + " 的评测依据属于其他记录或结论不一致")
    if item["correctness"] in {"correct", "incorrect"} and not eval_refs:
        raise ValueError(rid + " 的正确性结论缺少使用者提供的评测依据")
    if item["badcase"] == "confirmed" and (item["correctness"] != "incorrect" or not eval_refs):
        raise ValueError(rid + " 的 confirmed BadCase 缺少错误确认依据")


def combine(analysis, documents, caps):
    records = {r["id"]: r for r in analysis["records"]}
    digest = fingerprint(analysis)
    items = []
    seen = set()
    scopes = set()
    for document in documents:
        if not isinstance(document, dict) or document.get("schema_version") != "1.0":
            raise ValueError("评审文件必须为 schema_version=1.0 的对象")
        if document.get("analysis_fingerprint") != digest:
            raise ValueError("评审文件与当前分析指纹不一致；请根据当前批次重新评审")
        if document.get("capabilities_fingerprint") != fingerprint(caps):
            raise ValueError("能力或评测依据已改变，评审指纹不一致；请根据当前能力材料重新评审")
        scope = document.get("scope")
        if scope not in {"document_examples", "selected_records", "all_question_records"}:
            raise ValueError("评审范围 scope 无效")
        scopes.add(scope)
        if not isinstance(document.get("items"), list):
            raise ValueError("评审文件 items 必须是数组")
        for item in document["items"]:
            rid = item.get("record_id") if isinstance(item, dict) else None
            if rid not in records or rid in seen:
                raise ValueError("不存在或重复的评审 record_id: " + str(rid))
            check_item(item, records[rid], caps)
            seen.add(rid)
            items.append(item)
    snapshot = analysis["provenance"].get("mode") == "document_snapshot"
    if snapshot and scopes - {"document_examples"}:
        raise ValueError("文档快照只允许 document_examples 评审范围")
    if not snapshot and "document_examples" in scopes:
        raise ValueError("导入数据不能冒充文档实例评审")
    if "all_question_records" in scopes:
        eligible = {r["id"] for r in question_records(analysis)}
        if not eligible.issubset(seen):
            raise ValueError("尚有未评审的问题记录，不能声明 all_question_records")
    metrics = {
        "reviewed_records": metric("已评审记录", len(items)),
        "valid_questions": metric("已评审有效问题", sum(i["validity"] == "valid" for i in items)),
        "unsupported_data_queries": metric("已确认不支持的有效问数记录", sum(
            i["validity"] == "valid" and "data_query" in i["intent"] and i["support"] == "unsupported" for i in items)),
        "partial_data_queries": metric("部分支持的有效问数记录", sum(
            i["validity"] == "valid" and "data_query" in i["intent"] and i["support"] == "partial" for i in items)),
        "confirmed_badcases": metric("有评测依据的已确认 BadCase", sum(i["badcase"] == "confirmed" for i in items)),
        "candidate_badcases": metric("待核验 BadCase 候选", sum(i["badcase"] == "candidate" for i in items))}
    evaluated = [i for i in items if i["correctness"] in {"correct", "incorrect"} and i.get("evaluation_refs")]
    correct = sum(i["correctness"] == "correct" for i in evaluated)
    metrics["accuracy"] = metric("有依据的评测样本准确率", correct / len(evaluated) if evaluated else None,
                                 "ratio", numerator=correct, denominator=len(evaluated),
                                 reason=None if evaluated else "缺少使用者提供的正确性评测依据")
    by_source = []
    for source in ("dialogue", "feedback", "guardrail", "inference"):
        available = sum(r["source"] == source for r in records.values())
        count = sum(records[i["record_id"]]["source"] == source for i in items)
        by_source.append({"source": source, "reviewed": count, "available": available})
    scope = "document_examples" if snapshot else ("all_question_records" if "all_question_records" in scopes else "selected_records")
    coverage = {"reviewed": len(items), "available": len(records),
                "ratio": len(items) / len(records) if records else None, "by_source": by_source,
                "note": "仅覆盖本文件提供的实例，不能外推文档全量" if snapshot else "按来源分别统计，记录数量不是独立业务问题总量"}
    return {"schema_version": "1.0", "analysis": analysis,
            "review": {"status": "completed" if items else "not_run", "scope": scope, "items": items,
                       "metrics": metrics, "coverage": coverage,
                       "analysis_fingerprint": digest,
                       "capabilities_fingerprint": fingerprint(caps),
                       "capabilities": caps,
                       "notes": [coverage["note"], "脚本检查引用存在与标签一致性，AI 的语义结论仍需复核。"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--reviews", required=True, nargs="+", type=Path)
    parser.add_argument("--capabilities", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        analysis = validate_analysis(load_json(args.analysis))
        result = combine(analysis, [load_json(p) for p in args.reviews], capabilities(args.capabilities))
        write_json(args.out, result)
        print("已验证并合并 %d 条 AI 评审：%s" % (len(result["review"]["items"]), args.out))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print("评审合并失败: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
