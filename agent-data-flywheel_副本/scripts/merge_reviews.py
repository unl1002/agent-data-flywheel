#!/usr/bin/env python3
"""Validate AI evidence references and aggregate only the reviewed records."""
import argparse
from datetime import datetime, timedelta, timezone
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
V2_FIELDS = {"response_support", "response_evidence_refs", "unsupported_reason",
             "value_level", "value_reason", "opportunity_type"}
RESPONSE_SUPPORT = {"supported", "unsupported", "partial", "uncertain", "not_applicable"}
VALUE_LEVELS = {"high", "medium", "none", "uncertain"}
GAP_TYPES = {'field_mapping', 'execution_failure', 'scope_clarification', 'intent_deviation',
             'content_uncovered', 'safety_boundary', 'semantic_mismatch', 'unknown'}
BEIJING = timezone(timedelta(hours=8))


def check_item(item, record, caps):
    if not isinstance(item, dict):
        raise ValueError("评审项必须为对象")
    rid = record["id"]
    if 'response_gap_type' in item and (not isinstance(item['response_gap_type'], str) or item['response_gap_type'] not in GAP_TYPES):
        raise ValueError(rid + ' 的 response_gap_type 枚举无效')
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
    # A legacy item has none of the V2 fields. Once one appears, the complete
    # response/value judgment is required so partial migrations cannot look final.
    if V2_FIELDS.intersection(item):
        missing = V2_FIELDS - item.keys()
        if missing:
            raise ValueError(rid + " 的 V2 评审缺少字段: " + ", ".join(sorted(missing)))
        response_support = item["response_support"]
        if not isinstance(response_support, str) or response_support not in RESPONSE_SUPPORT:
            raise ValueError(rid + " 的 response_support 枚举无效")
        response_refs = item["response_evidence_refs"]
        if (not isinstance(response_refs, list) or
                any(not isinstance(ref, str) or ref not in available for ref in response_refs)):
            raise ValueError(rid + " 的 response_evidence_refs 必须引用本记录证据")
        if response_support in {"supported", "unsupported", "partial"} and not response_refs:
            raise ValueError(rid + " 的确定响应支持结论缺少响应证据")
        if item["validity"] == "invalid" and response_support not in {"uncertain", "not_applicable"}:
            raise ValueError(rid + " 的无效问题不能计入确定响应支持结论")
        if not isinstance(item["value_level"], str) or item["value_level"] not in VALUE_LEVELS:
            raise ValueError(rid + " 的 value_level 枚举无效")
        for field in ("unsupported_reason", "value_reason", "opportunity_type"):
            if not isinstance(item[field], str):
                raise ValueError(rid + " 的 " + field + " 必须为字符串")
        if response_support in {"unsupported", "partial"} and not item["unsupported_reason"].strip():
            raise ValueError(rid + " 的未支持或部分响应缺少 unsupported_reason")
        if item['value_level'] in {'high','medium'}:
            if not item['value_reason'].strip():
                raise ValueError(rid + ' 的高/中价值缺少业务价值依据')
            if not isinstance(item.get('suggested_action'), str) or not item['suggested_action'].strip():
                raise ValueError(rid + ' 的高/中价值缺少后续行动建议')
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



def record_month(record):
    """Use the recorded millisecond timestamp; never infer missing dates."""
    stamp = record.get("time")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
        return "unknown"
    try:
        return datetime.fromtimestamp(stamp / 1000, BEIJING).strftime("%Y-%m")
    except (ValueError, OverflowError, OSError):
        return "unknown"


def aggregate_metrics(items, population_ids, trace_prefix):
    """Build values and their exact record membership in the same operation."""
    metrics, lineage = {}, {}
    reviewed = [i["record_id"] for i in items]
    valid = [i for i in items if i["validity"] == "valid"]
    valid_refs = [i["record_id"] for i in valid]

    def add(key, label, members, formula, denominator=None, reason=None):
        members = list(members)
        unit = "count" if denominator is None else "ratio"
        if denominator is None:
            value, included, kwargs = len(members), members, {}
            denominator_refs = []
        else:
            denominator_refs = list(denominator)
            value = len(members) / len(denominator_refs) if denominator_refs else None
            included = denominator_refs
            kwargs = {"numerator": len(members), "denominator": len(denominator_refs)}
            if not denominator_refs and reason is None:
                reason = "有效分母为零。"
        if reason is not None:
            kwargs["reason"] = reason
        trace_id = trace_prefix + key
        metrics[key] = metric(label, value, unit, trace_id=trace_id, **kwargs)
        included_set = set(included)
        trace = {"label": label, "value": value, "unit": unit, "formula": formula,
                 "member_refs": included, "numerator_refs": members,
                 "denominator_refs": denominator_refs,
                 "excluded_refs": [rid for rid in population_ids if rid not in included_set]}
        if reason is not None:
            trace["reason"] = reason
        lineage[trace_id] = trace

    add("reviewed_records", "已评审记录", reviewed, "count(reviewed records)")
    add("valid_questions", "已评审有效问题", valid_refs, "count(validity=valid)")
    add("unsupported_data_queries", "已确认不支持的有效问数记录",
        [i["record_id"] for i in valid if "data_query" in i["intent"] and i["support"] == "unsupported"],
        "count(validity=valid and data_query in intent and capability support=unsupported)")
    add("partial_data_queries", "部分支持的有效问数记录",
        [i["record_id"] for i in valid if "data_query" in i["intent"] and i["support"] == "partial"],
        "count(validity=valid and data_query in intent and capability support=partial)")
    for key, state, label in [("confirmed_badcases", "confirmed", "有评测依据的已确认 BadCase"),
                              ("candidate_badcases", "candidate", "待核验 BadCase 候选")]:
        add(key, label, [i["record_id"] for i in items if i["badcase"] == state],
            "count(badcase=" + state + ")")
    response_refs = {}
    for state, label in [("supported", "实际响应已支持的有效问题"),
                         ("unsupported", "实际响应未支持的有效问题"),
                         ("partial", "实际响应部分支持的有效问题")]:
        response_refs[state] = [i["record_id"] for i in valid if i.get("response_support") == state]
        add("response_" + state, label, response_refs[state],
            "count(validity=valid and response_support=" + state + ")")
    judged_states = {"supported", "unsupported", "partial"}
    unknown_refs = [i["record_id"] for i in valid if i.get("response_support") not in judged_states]
    judged_refs = [i["record_id"] for i in valid if i.get("response_support") in judged_states]
    add("response_unknown", "实际响应支持性未知的有效问题", unknown_refs,
        "count(validity=valid and response_support not in {supported,unsupported,partial})")
    add("response_support_rate", "有效问题的实际响应支持率（含未知分母）",
        response_refs["supported"], "count(valid and response_supported) / count(all reviewed valid)",
        denominator=valid_refs)
    add("response_judged_support_rate", "可判响应样本中的支持率",
        response_refs["supported"], "count(valid and response_supported) / count(valid and response_support in {supported,unsupported,partial})",
        denominator=judged_refs)
    add("valid_question_rate", "已评审记录的有效问题率", valid_refs,
        "count(validity=valid) / count(reviewed records)", denominator=reviewed)
    add("value_questions", "有业务价值的有效问题（高或中）",
        [i["record_id"] for i in valid if i.get("value_level") in {"high", "medium"}],
        "count(validity=valid and value_level in {high,medium})")
    evaluated = [i for i in items if i["correctness"] in {"correct", "incorrect"} and i.get("evaluation_refs")]
    add("accuracy", "有依据的评测样本准确率",
        [i["record_id"] for i in evaluated if i["correctness"] == "correct"],
        "count(record-bound evaluated correct) / count(record-bound evaluated correct or incorrect)",
        denominator=[i["record_id"] for i in evaluated],
        reason=None if evaluated else "缺少使用者提供的正确性评测依据")
    return metrics, lineage


def aggregate_distributions(items, lineage):
    """Summarize AI labels without replacing free-text reasons with keyword rules."""
    output = {}
    unsupported = [i for i in items if i['validity'] == 'valid' and
                   i.get('response_support') in {'unsupported', 'partial'}]
    specs = [('unsupported_intents', unsupported, 'intent'),
             ('unsupported_reasons', unsupported, 'response_gap_type'),
             ('unsupported_causes', unsupported, 'root_cause'),
             ('opportunity_types', [i for i in items if i['validity'] == 'valid' and
                                    i.get('value_level') in {'high', 'medium'}], 'opportunity_type')]
    for key, selected, field in specs:
        groups = {}
        for item in selected:
            values = item[field] if field == 'intent' else [item.get(field) or 'unknown']
            for value in dict.fromkeys(values):
                groups.setdefault(value, []).append(item['record_id'])
        output[key] = []
        for index, (name, members) in enumerate(sorted(groups.items(), key=lambda x: (-len(x[1]), x[0]))):
            tid = 'review:primary:distribution:' + key + ':' + str(index)
            lineage[tid] = {'label': name, 'value': len(members), 'unit': 'count',
                            'formula': 'count(dialogue AI labels: ' + field + '=' + name + ')',
                            'member_refs': members, 'numerator_refs': [], 'denominator_refs': [],
                            'excluded_refs': [i['record_id'] for i in items if i['record_id'] not in set(members)]}
            output[key].append({'name': name, 'value': len(members), 'trace_id': tid})
    return output

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
    # The default review population is question records, not the independent
    # inference/guardrail exports. Explicitly selected other records remain valid.
    population = {r["id"] for r in question_records(analysis)} | seen
    population_ids = [rid for rid in records if rid in population]
    metrics, lineage = aggregate_metrics(items, population_ids, "review:")
    month_items = {}
    for item in items:
        month = record_month(records[item["record_id"]])
        month_items.setdefault(month, []).append(item)
    monthly = []
    for month in sorted(month_items, key=lambda value: (value == "unknown", value)):
        month_population = [rid for rid in population_ids if record_month(records[rid]) == month]
        month_metrics, month_lineage = aggregate_metrics(
            month_items[month], month_population, "review:monthly:" + month + ":")
        lineage.update(month_lineage)
        monthly.append({"month": month, "metrics": month_metrics})
    by_source = []
    by_source_metrics = {}
    for source in ("dialogue", "feedback", "guardrail", "inference"):
        available = sum(r["source"] == source for r in records.values())
        count = sum(records[i["record_id"]]["source"] == source for i in items)
        by_source.append({"source": source, "reviewed": count, "available": available})
        source_items = [i for i in items if records[i['record_id']]['source'] == source]
        source_population = [rid for rid in population_ids if records[rid]['source'] == source]
        source_metrics, source_lineage = aggregate_metrics(source_items, source_population, 'review:source:' + source + ':')
        by_source_metrics[source] = source_metrics
        lineage.update(source_lineage)
    primary_items = [i for i in items if records[i['record_id']]['source'] == 'dialogue']
    primary_population = [rid for rid in population_ids if records[rid]['source'] == 'dialogue']
    primary_monthly = []
    for month in sorted({record_month(records[rid]) for rid in primary_population}, key=lambda x: (x == 'unknown', x)):
        selected = [i for i in primary_items if record_month(records[i['record_id']]) == month]
        eligible = [rid for rid in primary_population if record_month(records[rid]) == month]
        month_metrics, month_lineage = aggregate_metrics(selected, eligible, 'review:primary:monthly:' + month + ':')
        lineage.update(month_lineage)
        primary_monthly.append({'month': month, 'metrics': month_metrics})
    primary_distributions = aggregate_distributions(primary_items, lineage)
    scope = "document_examples" if snapshot else ("all_question_records" if "all_question_records" in scopes else "selected_records")
    coverage = {"reviewed": len(items), "available": len(records),
                "ratio": len(items) / len(records) if records else None, "by_source": by_source,
                "note": "仅覆盖本文件提供的实例，不能外推文档全量" if snapshot else "按来源分别统计，记录数量不是独立业务问题总量"}
    return {"schema_version": "1.0", "analysis": analysis,
            "review": {"status": "completed" if items else "not_run", "scope": scope, "items": items,
                       "metrics": metrics, "lineage": lineage, "monthly": monthly, "coverage": coverage,
                       "primary_source": "dialogue", "primary_metrics": by_source_metrics['dialogue'],
                       "by_source_metrics": by_source_metrics, "primary_monthly": primary_monthly,
                       "primary_distributions": primary_distributions,
                       "analysis_fingerprint": digest,
                       "capabilities_fingerprint": fingerprint(caps),
                       "capabilities": caps,
                       "notes": [coverage["note"], "脚本检查引用存在与标签一致性，AI 的语义结论仍需复核。",
                                 "响应支持率基于实际响应覆盖，不是正确率或产品能力承诺；主分母包含有效问题中的未知响应。",
                                 "主指标与主月度表只含对话请求；孤立反馈单列，跨来源记录不视作独立用户或独立问题总量。",
                                 "月度按 record.time 的北京时间月份统计；缺失或非法时间归入 unknown。"]}}


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
