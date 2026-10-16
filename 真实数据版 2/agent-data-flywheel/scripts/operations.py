"""Attach deterministic membership to statistics and exported-sample usage.

Python 3.9 standard library only. This module never reads source files, executes
record text, calls an AI service, or infers people from sessions or tenants.
``enrich`` mutates and returns the analysis object; existing metric values are
preserved. Membership follows analyze.py's actual counting rules.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re


SOURCES = ("feedback", "guardrail", "dialogue", "inference")
SHANGHAI = timezone(timedelta(hours=8))
TIME_FIELDS = {"feedback": "createTime", "dialogue": "body.detail.askTime",
               "guardrail": "updateTime", "inference": "updateTime"}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _identifier(value):
    return isinstance(value, str) and bool(value.strip())


def _refs(rows):
    return [row["id"] for row in rows]


def _difference(universe, included):
    included = set(included)
    return [ref for ref in universe if ref not in included]


def _scalar_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _month(value):
    if not _number(value):
        return None
    try:
        # Guard obviously invalid epoch values, including zero placeholders.
        date = datetime.fromtimestamp(value / 1000, SHANGHAI)
        return date.strftime("%Y-%m") if 2000 <= date.year <= 2100 else None
    except (ValueError, OSError, OverflowError):
        return None


def _project_id(value):
    if not _identifier(value) or value.strip().lower() in {"null", "none", "undefined", "n/a", "-"}:
        return None
    return value.strip()


def _placeholder(value):
    return isinstance(value, str) and value.lower() in {
        "cloud_campus_project_id", "default", "default-project", "default_project",
        "default_project_id", "unknown", "unknown-project", "unknown_project_id",
        "placeholder", "project_placeholder"}


class _Enricher:
    def __init__(self, analysis):
        self.analysis = analysis
        self.lineage = analysis.setdefault("lineage", {})
        self.records = [r for r in analysis.get("records", []) if isinstance(r, dict) and _identifier(r.get("id"))]
        self.record_map = {r["id"]: r for r in self.records}
        self.groups = {source: [r for r in self.records if r.get("source") == source] for source in SOURCES}
        self.sources = analysis.setdefault("sources", {})
        self.raw = {source: [] for source in SOURCES}
        self.snapshot = analysis.get("provenance", {}).get("mode") == "document_snapshot"
        for item in analysis.get("source_rows", []):
            if isinstance(item, dict) and item.get("source") in self.raw:
                row = dict(item)
                row.setdefault("ref", "raw:" + item["source"] + ":" + str(item.get("index")))
                self.raw[item["source"]].append(row)
        for source in SOURCES:
            if self.raw[source]:
                continue
            # Fallback is intentionally limited to observed locators; never
            # fabricate unknown raw rows from an aggregate count.
            seen = set()
            for record in self.groups[source]:
                loc = record.get("raw_locator") or {}
                index = loc.get("index")
                if not isinstance(index, int) or isinstance(index, bool) or index in seen:
                    continue
                seen.add(index)
                self.raw[source].append({"ref": "raw:" + source + ":" + str(index), "source": source,
                    "index": index, "line": loc.get("line"), "sha256": loc.get("sha256"),
                    "log_retained": True, "retained_record_id": record["id"], "time": record.get("time")})

    def imported(self, source):
        return self.sources.get(source, {}).get("status") == "imported"

    def trace(self, trace_id, label, value, unit, formula, members=(), numerator=(), denominator=(), excluded=(), **extra):
        result = {"label": label, "value": value, "unit": unit, "formula": formula,
                  "member_refs": list(members), "numerator_refs": list(numerator),
                  "denominator_refs": list(denominator), "excluded_refs": list(excluded)}
        result.update(extra)
        self.lineage[trace_id] = result
        return trace_id

    def log_rows(self, source):
        source_data = self.sources.get(source, {})
        rows = self.raw[source]
        if source_data.get("records") == len(rows) and not source_data.get("duplicates", 0):
            return [row for row in rows if row.get("log_retained") is not False]
        normalized_indexes = {r.get("raw_locator", {}).get("index") for r in self.groups[source]}
        return [row for row in rows if row.get("log_retained") is True or
                ("log_retained" not in row and row.get("index") in normalized_indexes)]

    def raw_time(self, row):
        if "time" in row:
            return row["time"]
        target = self.record_map.get(row.get("retained_record_id"))
        # A duplicate's winner is not evidence of the duplicate's event time.
        if target is not None and target.get("raw_locator", {}).get("index") == row.get("index"):
            return target.get("time")
        return None

    def source_traces(self):
        issues = self.analysis.get("quality_issues", [])
        for source in SOURCES:
            info = self.sources.setdefault(source, {"status": "not_provided", "records": 0,
                        "raw_records": 0, "duplicates": 0, "errors": 0})
            ids = info.setdefault("trace_ids", {})
            all_rows = [row["ref"] for row in self.raw[source]]
            retained = [row["ref"] for row in self.log_rows(source)]
            dropped = _difference(all_rows, retained)
            for field, label, members, excluded, formula in [
                ("raw_records", "解析的原始对象数", all_rows, [], "COUNT 原始 JSON 数组对象；不是文件文本行数"),
                ("records", "来源内去重日志数", retained, dropped, "COUNT 来源内去重后日志；详情仍包含不同 requestId 的重复业务快照"),
                ("duplicates", "来源内重复日志数", dropped, retained, "COUNT 来源内去重淘汰原始行；反馈按整行指纹，其余按 requestId")]:
                reason = None
                if self.snapshot:
                    members, excluded = [], []
                    reason = "文档报告值，缺少全量原始行，不用当前实例冒充统计成员。"
                elif len(members) != info.get(field, 0):
                    reason = "原始行索引不足以覆盖报告计数；未伪造缺失的记录引用。"
                tid = "source:" + source + ":" + field
                extra = {"source": source, "entity_type": "raw_row"}
                if reason:
                    extra["reason"] = reason
                ids[field] = self.trace(tid, source + " · " + label, info.get(field, 0), "count", formula,
                                        members=members, excluded=excluded, **extra)
            error_refs = []
            indexes = defaultdict(list)
            unlocated = []
            for index, issue in enumerate(issues):
                if not isinstance(issue, dict) or issue.get("source") != source or issue.get("severity") != "error":
                    continue
                ref = issue.get("raw_ref")
                if not ref and issue.get("record_id") in self.record_map:
                    ref = issue["record_id"]
                loc = issue.get("raw_locator")
                if not ref and isinstance(loc, dict) and isinstance(loc.get("index"), int):
                    ref = "raw:" + source + ":" + str(loc["index"])
                if ref:
                    error_refs.append(ref)
                    indexes[ref].append(index)
                else:
                    unlocated.append(index)
            extra = {"source": source, "entity_type": "quality_issue", "issue_indexes": dict(indexes)}
            if unlocated:
                extra["unlocated_issue_indexes"] = unlocated
                extra["reason"] = "部分质量问题没有行定位；可按 quality_issues 索引查看，不猜测所属记录。"
            if self.snapshot:
                error_refs = []
                extra["reason"] = "文档报告值，不能按当前实例反推全量错误行。"
            ids["errors"] = self.trace("source:" + source + ":errors", source + " · 错误项数",
                info.get("errors", 0), "count", "COUNT severity=error 的质量问题项；同一记录可有多项错误及重复引用",
                members=error_refs, **extra)

    def core_metrics(self):
        feedback, guards, dialogues, inference = (self.groups[s] for s in SOURCES)
        identified = [r for r in dialogues if _identifier(r.get("chat_id"))]
        rated = [r for r in feedback if isinstance(r.get("feedback"), int) and not isinstance(r["feedback"], bool)
                 and r["feedback"] in (-1, 3)]
        valid_checks = [(record, check, index) for record in guards for index, check in enumerate(record.get("checks", []))
                        if isinstance(check, dict) and isinstance(check.get("is_legal"), bool)]
        check_ref = lambda r, c, i: r["id"] + "#check:" + str(c.get("original_index", i))
        checks = [check_ref(r, c, i) for r, c, i in valid_checks]
        all_check_refs = []
        for record in guards:
            indexes = {check.get("original_index", index) for index, check in enumerate(record.get("checks", [])) if isinstance(check, dict)}
            for evidence in record.get("evidence", []):
                if not isinstance(evidence, dict):
                    continue
                match = re.fullmatch(r"responseData\.checkResults\[(\d+)\]", str(evidence.get("path", "")))
                if match:
                    indexes.add(int(match.group(1)))
            all_check_refs.extend(record["id"] + "#check:" + str(index) for index in sorted(indexes))
        blocked_checks = [check_ref(r, c, i) for r, c, i in valid_checks if c["is_legal"] is False]
        valid_guard_ids = {r["id"] for r, _, _ in valid_checks}
        eligible_guards = [r for r in guards if r["id"] in valid_guard_ids]
        blocked_guard_ids = {r["id"] for r, c, _ in valid_checks if c["is_legal"] is False}
        comparable = [r for r in inference if r.get("requested_model") and r.get("actual_models")]
        valid_dialogues = {r["id"] for r in identified}
        matches = [r for r in feedback if _identifier(r.get("chat_id")) and r.get("linked_dialogue_id") in valid_dialogues]
        matched_dialogues = {r["linked_dialogue_id"] for r in matches}
        eligible_feedback = [r for r in feedback if _identifier(r.get("chat_id"))]
        specs = {}

        def count(key, universe, members, formula, entity="record", **extra):
            specs[key] = {"members": list(members), "excluded": _difference(list(universe), members),
                          "formula": formula, "entity_type": entity, **extra}

        def ratio(key, universe, numerator, denominator, formula, entity="record", **extra):
            specs[key] = {"members": list(denominator), "numerator": list(numerator), "denominator": list(denominator),
                          "excluded": _difference(list(universe), denominator), "formula": formula,
                          "entity_type": entity, **extra}

        for source, key in (("feedback", "feedback_records"), ("guardrail", "guardrail_records"), ("inference", "inference_records")):
            refs = _refs(self.groups[source])
            count(key, refs, refs, "COUNT 当前规范化的来源内去重记录", source + "_record")
        count("dialogue_records", _refs(dialogues), _refs(identified), "COUNT 有有效 detail.id 的去重业务快照", "dialogue")
        count("dialogue_missing_id", _refs(dialogues), _refs([r for r in dialogues if not _identifier(r.get("chat_id"))]),
              "COUNT 缺少有效 detail.id 的保留详情，不能伪造业务ID", "dialogue")
        nodes = [r["id"] + "#node:" + str(node.get("original_index", index)) for r in dialogues
                 for index, node in enumerate(r.get("nodes", [])) if isinstance(node, dict)]
        count("answer_nodes", nodes, nodes, "COUNT 所有保留详情的节点；包括缺业务ID记录和 END 后的异步节点", "answer_node")
        session_records = [r for r in dialogues if _identifier(r.get("conversation_id"))]
        count("conversation_count", _refs(dialogues), _refs(session_records),
              "COUNT DISTINCT conversation_id；成员可多于会话数；缺业务ID但有会话ID的详情仍参与", "conversation",
              group_values={r["id"]: r["conversation_id"] for r in session_records})
        count("rated_feedback", _refs(feedback), _refs(rated), "COUNT feedback 为整数 -1 或 3；0不计评价", "feedback_event")
        ratio("negative_feedback_rate", _refs(feedback), _refs([r for r in rated if r["feedback"] == -1]), _refs(rated),
              "COUNT feedback=-1 / COUNT feedback IN {-1,3}；0、非法值排除", "feedback_event")
        ratio("reason_coverage", _refs(feedback), _refs([r for r in feedback if r.get("has_reason")]), _refs(feedback),
              "COUNT 非空且非空白 reason / COUNT 反馈事件", "feedback_event")
        ratio("scene_missing_rate", _refs(dialogues), _refs([r for r in identified if r.get("scene") is None]), _refs(identified),
              "COUNT scene为空的独立详情 / COUNT 有有效detail.id的独立详情", "dialogue")
        ratio("end_marker_coverage", _refs(dialogues), _refs([r for r in identified if any(n.get("status") == "END" for n in r.get("nodes", []) if isinstance(n, dict))]),
              _refs(identified), "COUNT 含END节点的独立详情 / COUNT 有有效detail.id的独立详情；不是成功率", "dialogue")
        ratio("guardrail_block_rate", _refs(guards), _refs([r for r in eligible_guards if r["id"] in blocked_guard_ids]), _refs(eligible_guards),
              "COUNT 至少一个有效is_legal=false的请求 / COUNT 至少一个布尔检查结果的请求", "guardrail_request")
        ratio("guardrail_question_block_rate", all_check_refs, blocked_checks, checks,
              "COUNT is_legal=false的检查项 / COUNT is_legal为布尔值的检查项；按原始检查索引定位", "guardrail_check")
        ratio("inference_http_200_rate", _refs(inference), _refs([r for r in inference if r.get("response_status") == 200]), _refs(inference),
              "COUNT status=200的推理日志 / COUNT 推理日志；不是准确率", "inference_record")
        ratio("model_route_difference_rate", _refs(inference), _refs([r for r in comparable if any(model != r["requested_model"] for model in r["actual_models"])]),
              _refs(comparable), "COUNT 可比较且任一实际模型不同于请求模型 / COUNT 请求模型与实际模型均可比较的请求", "inference_record")
        for key, rows, predicate, formula in [
            ("dialogue_cost_p95_ms", dialogues, lambda r: _number(r.get("cost_ms")) and r["cost_ms"] >= 0,
             "SORT 全部保留详情有效非负detail.costTime；P95取第ceil(0.95*N)项；含缺业务ID详情"),
            ("first_token_p95_ms", inference, lambda r: r.get("stream") is True and _number(r.get("first_token_ms")) and r["first_token_ms"] > 0,
             "SORT 流式且first_token_ms>0的有效测量；P95取第ceil(0.95*N)项；0、缺失、非流式排除")]:
            eligible = [r for r in rows if predicate(r)]
            value_key = "cost_ms" if key.startswith("dialogue") else "first_token_ms"
            samples = {r["id"]: r[value_key] for r in eligible}
            ranked = sorted(samples.items(), key=lambda item: (item[1], item[0]))
            rank = math.ceil(len(ranked) * .95) if ranked else None
            count(key, _refs(rows), _refs(eligible), formula, "measurement", sample_values=samples,
                  denominator=_refs(eligible), rank=rank, selected_ref=ranked[rank-1][0] if rank else None)
        count("first_token_zero_records", _refs(inference), _refs([r for r in inference if r.get("first_token_ms") == 0]),
              "COUNT first_token_ms=0的推理日志（仅反映原始零值，不视为有效首token测量）", "inference_record")
        joined = self.imported("feedback") and self.imported("dialogue")
        join_reason = "须同时提供反馈与对话原始来源；不沿用文档基线或推测关联。"
        count("feedback_match_count", _refs(feedback), _refs(matches) if joined else [],
              "COUNT chat_id确定匹配到有效独立详情的反馈事件；多次反馈分别保留", "feedback_event")
        ratio("feedback_link_rate", _refs(feedback), _refs(matches) if joined else [], _refs(eligible_feedback) if joined else [],
              "COUNT 确定匹配反馈事件 / COUNT 有有效chat_id的反馈事件", "feedback_event")
        ratio("dialogue_feedback_coverage", _refs(dialogues), _refs([r for r in identified if r["id"] in matched_dialogues]) if joined else [],
              _refs(identified) if joined else [], "COUNT 有匹配反馈的独立详情 / COUNT 有有效detail.id的独立详情", "dialogue")
        count("feedback_missing_chat_id", _refs(feedback), _refs([r for r in feedback if not _identifier(r.get("chat_id"))]),
              "COUNT 反馈缺少有效chat_id", "feedback_event")
        count("orphan_feedback", _refs(feedback), _refs([r for r in eligible_feedback if r not in matches]) if joined else [],
              "COUNT 有有效chat_id但没有确定匹配详情的反馈事件；关联来源缺失时未知", "feedback_event")
        if not joined:
            for key in ("feedback_match_count", "feedback_link_rate", "dialogue_feedback_coverage", "orphan_feedback"):
                specs[key]["reason"] = join_reason
        for key in ("accuracy", "rag_recall", "resolution_rate"):
            specs[key] = {"formula": "缺少该指标所需的人类评测/检索金标/修复闭环依据，不从HTTP200、END或mark推算",
                          "members": [], "numerator": [], "denominator": [], "excluded": _refs(self.records),
                          "entity_type": "unavailable", "reason": "没有可计算的评测、检索或复测分母；未知不等于0。"}
        for key, metric in self.analysis.get("metrics", {}).items():
            if not isinstance(metric, dict):
                continue
            tid = "metric:" + key
            metric["trace_id"] = tid
            spec = dict(specs.get(key, {"formula": "保留原指标值；尚未定义可验证的成员选择规则",
                                      "reason": "不能从汇总数量反推参与记录。"}))
            formula = spec.pop("formula")
            if metric.get("reason"):
                spec.setdefault("reason", metric["reason"])
            if self.snapshot:
                spec = {"reason": "文档报告值，当前少量实例不充当全量统计分子或分母。", "entity_type": "document_aggregate"}
                formula = "文档报告值；未经当前原始JSON重算"
            source = metric.get("source")
            source_names = source.split("+") if isinstance(source, str) else []
            spec["input_trace_ids"] = [self.sources[s]["trace_ids"]["records"] for s in source_names if s in self.sources]
            self.trace(tid, metric.get("label", key), metric.get("value"), metric.get("unit", "count"), formula, **spec)

    def distributions(self):
        identified = [r for r in self.groups["dialogue"] if _identifier(r.get("chat_id"))]
        for group, items in self.analysis.get("distributions", {}).items():
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                tid = "distribution:" + group + ":" + str(index)
                item["trace_id"] = tid
                name = _scalar_text(item.get("name"))
                universe, members, extra = [], [], {}
                formula = "COUNT 当前分布组满足分组条件的记录"
                if group == "scenes":
                    universe = _refs(identified)
                    members = [r["id"] for r in identified if (r.get("scene") or "无场景") == name]
                    formula = "COUNT 有有效detail.id且(scene或无场景)等于当前组名的独立详情"
                elif group in ("request_models", "http_status", "response_formats"):
                    rows = self.groups["inference"]
                    universe = _refs(rows)
                    key = {"request_models": "requested_model", "http_status": "response_status", "response_formats": "response_format"}[group]
                    value_for = lambda r: (_scalar_text(r.get(key)) or "未知")
                    members = [r["id"] for r in rows if value_for(r) == name]
                    formula = "COUNT 推理日志的 " + key + " 分组（缺失标记未知；不是业务正确性）"
                elif group == "recipe_dialogue_coverage":
                    universe = _refs(identified)
                    members = [r["id"] for r in identified if any(n.get("recipe") == name for n in r.get("nodes", []) if isinstance(n, dict))]
                    extra["denominator"] = universe
                    extra["numerator"] = members
                    extra["ratio"] = item.get("ratio")
                    formula = "COUNT 含该Recipe的独立详情；每详情只计1次；覆盖分母为所有有效detail.id"
                elif group in ("user_types", "user_type", "products", "project_products", "feedback_values"):
                    rows = self.groups["feedback"] if group == "feedback_values" else self.records
                    if item.get("source") in SOURCES:
                        rows = [r for r in rows if r.get("source") == item["source"]]
                    field = "user_type" if group in ("user_type", "user_types") else "feedback" if group == "feedback_values" else "product"
                    universe = _refs(rows)
                    members = [r["id"] for r in rows if (_scalar_text(r.get(field)) or "未知") == name]
                    formula = "COUNT " + field + " 原始枚举分组；用户类型不等于用户身份"
                else:
                    extra["reason"] = "尚未定义此分布的确定成员规则；保留原值，不推测参与者。"
                    formula = "保留原分布值，成员选择规则待补充"
                if self.snapshot:
                    members, universe = [], []
                    extra = {"reason": "文档分布值，当前实例不能反推全量分布成员。"}
                    formula = "文档报告分布，未经当前原始JSON重算"
                self.trace(tid, group + " · " + name, item.get("value"), "count", formula,
                           members=members, excluded=_difference(universe, members), **extra)

    def usage(self):
        usage = {"timezone": "Asia/Shanghai", "months": [], "source_months": [], "project_rows": [],
                 "metrics": {}, "monthly_metrics": {}, "unknown_time": {}, "notes": [
                     "统计仅覆盖当前导出样本；不同来源日志数不相加为业务问题量。",
                     "详情使用提问时间，反馈使用反馈时间，护栏/推理使用完成时间；各自按北京时间分月。",
                     "活跃项目以有有效chat_id的业务详情为基础；缺失及占位项目单列，排除在已识别项目数之外。",
                     "会话是conversation_id分组，不是用户；缺少稳定提问人ID，人数、DAU、MAU和留存未知。"]}
        self.analysis["usage"] = usage
        if self.snapshot:
            usage["notes"].append("当前为文档快照，少量实例不用于重算全量月度及项目使用。")
            for key, label in (("active_projects", "项目数"), ("active_sessions", "会话数"), ("user_count", "用户人数")):
                usage["metrics"][key] = self.trace("usage:" + key, label, None, "count", "文档快照没有全量原始记录",
                                                   reason="不能由少量实例推算全量使用统计。")
            return
        selected = {source: self.log_rows(source) for source in SOURCES}
        months = {_month(self.raw_time(row)) for source in SOURCES for row in selected[source]}
        months.update(_month(r.get("time")) for r in self.records)
        usage["months"] = sorted(month for month in months if month is not None)
        for source in SOURCES:
            universe = [row["ref"] for row in selected[source]]
            unknown = [row["ref"] for row in selected[source] if _month(self.raw_time(row)) is None]
            usage["unknown_time"][source] = self.trace("usage:unknown_time:" + source, source + " · 时间未知日志",
                len(unknown) if self.imported(source) else None, "count", "COUNT 来源内去重日志中没有可分月有效事件时间的原始行",
                members=unknown, excluded=_difference(universe, unknown), entity_type="source_log", source=source)
            for month in usage["months"]:
                members = [row["ref"] for row in selected[source] if _month(self.raw_time(row)) == month]
                tid = "usage:source_month:" + source + ":" + month
                self.trace(tid, month + " · " + source + "日志", len(members) if self.imported(source) else None,
                    "count", "COUNT 来源内去重日志 WHERE " + TIME_FIELDS[source] + "按Asia/Shanghai分月=" + month,
                    members=members, excluded=_difference(universe, members), source=source, time_field=TIME_FIELDS[source],
                    entity_type="source_log", reason=None if self.imported(source) else "未提供该来源，不把缺失来源显示为0次使用。")
                usage["source_months"].append({"month": month, "source": source, "trace_id": tid})
        dialogues = self.groups["dialogue"]
        business = [r for r in dialogues if _identifier(r.get("chat_id"))]
        all_dialogue_refs = _refs(dialogues)
        available = self.imported("dialogue")
        projects = defaultdict(list)
        for record in business:
            projects[_project_id(record.get("project_id"))].append(record)
        for project_id in sorted(projects, key=lambda value: (value is None, value or "")):
            rows = projects[project_id]
            stable = hashlib.sha256(("missing" if project_id is None else "id:" + project_id).encode()).hexdigest()[:20]
            key = "project:" + stable
            is_placeholder = _placeholder(project_id)
            project_name = next((r.get("project") for r in rows if _identifier(r.get("project"))), project_id)
            if project_id is None:
                project_name = "项目ID缺失"
            elif is_placeholder:
                project_name = (project_name or project_id) + "（占位项目）"
            row = {"key": key, "project_id": project_id, "project": project_name, "months": {},
                   "is_placeholder": is_placeholder, "is_missing": project_id is None}
            row["total"] = self.trace("usage:" + key + ":total", str(project_name) + " · 业务提问记录",
                len(rows), "count", "COUNT 有有效chat_id且project_id归入本桶的业务详情；占位/缺失桶不冒充已识别项目",
                members=_refs(rows), excluded=_difference(all_dialogue_refs, _refs(rows)), entity_type="dialogue", project_id=project_id)
            for month in usage["months"]:
                members = [r["id"] for r in rows if _month(r.get("time")) == month]
                tid = "usage:" + key + ":" + month
                row["months"][month] = self.trace(tid, str(project_name) + " · " + month, len(members), "count",
                    "COUNT 该项目桶的业务详情 WHERE time按Asia/Shanghai分月=" + month,
                    members=members, excluded=_difference(_refs(rows), members), entity_type="dialogue", project_id=project_id,
                    time_field=TIME_FIELDS["dialogue"])
            usage["project_rows"].append(row)

        def group_count(tid, label, rows, field, universe, valid=None):
            included = [r for r in rows if valid(r)] if valid is not None else list(rows)
            groups = {r["id"]: r.get(field) for r in included}
            return self.trace(tid, label, len(set(groups.values())) if available else None, "count",
                "COUNT DISTINCT " + field + "，成员列出每个参与详情，group_values提供去重组；不是人数",
                members=_refs(included), excluded=_difference(universe, _refs(included)), group_values=groups,
                entity_type="project" if field=="project_id" else "conversation")

        known = lambda r: _project_id(r.get("project_id")) is not None and not _placeholder(_project_id(r.get("project_id")))
        has_session = lambda r: _identifier(r.get("conversation_id"))
        usage["metrics"]["active_projects"] = group_count("usage:active_projects", "本批已识别活跃项目", business,
                                                               "project_id", all_dialogue_refs, known)
        usage["metrics"]["active_sessions"] = group_count("usage:active_sessions", "本批业务活跃会话（非用户）", business,
                                                               "conversation_id", all_dialogue_refs, has_session)
        usage["metrics"]["user_count"] = self.trace("usage:user_count", "用户人数", None, "count",
            "未提供经确认的稳定提问人ID，不将conversation_id、tenant_id、project_id或user_type当用户标识",
            excluded=_refs(self.records), entity_type="unavailable", reason="无法计算真实用户数、DAU、MAU或留存。")
        for month in usage["months"]:
            rows = [r for r in business if _month(r.get("time")) == month]
            tids = {}
            tids["business_questions"] = self.trace("usage:business_questions:" + month, month + " · 业务提问记录",
                len(rows) if available else None, "count", "COUNT 有有效chat_id的业务详情 WHERE time按Asia/Shanghai分月=" + month,
                members=_refs(rows), excluded=_difference(all_dialogue_refs, _refs(rows)), entity_type="dialogue", time_field=TIME_FIELDS["dialogue"])
            tids["active_projects"] = group_count("usage:active_projects:" + month, month + " · 已识别活跃项目", rows,
                                                    "project_id", all_dialogue_refs, known)
            tids["active_sessions"] = group_count("usage:active_sessions:" + month, month + " · 活跃会话（非用户）", rows,
                                                    "conversation_id", all_dialogue_refs, has_session)
            usage["monthly_metrics"][month] = tids


def enrich(analysis):
    """Mutate and return analysis with metric/source/distribution traces and usage.

    Raw-row log retention is taken from source_rows.log_retained. source_rows.time
    should carry the normalized source event time, including business snapshots
    removed from the final record set. Without a time, that row remains visible
    under usage.unknown_time; its month is never guessed from another snapshot.
    """
    worker = _Enricher(analysis)
    worker.source_traces()
    worker.core_metrics()
    worker.distributions()
    worker.usage()
    return analysis
