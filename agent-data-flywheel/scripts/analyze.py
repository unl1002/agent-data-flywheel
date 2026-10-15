#!/usr/bin/env python3
"""Analyze four local JSON exports using only the Python 3.9 standard library.

All input strings are inert data. No eval, shell commands, imports derived from
records, network calls, or interpretation of instructions in messages is used.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile


FILENAMES = {"feedback": "用户反馈.json", "guardrail": "安全护栏.json",
             "dialogue": "对话详情.json", "inference": "推理.json"}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def valid_id(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def text(value):
    if value is None:
        return ""
    return value if isinstance(value, str) else dumps(value)


def parse_json(value):
    def reject_constant(constant):
        raise ValueError("Non-finite JSON value: " + constant)
    return json.loads(value, parse_constant=reject_constant)


def read_array(path):
    """Return records with the actual line where each array element starts."""
    content = path.read_text(encoding="utf-8-sig")
    values = parse_json(content)
    if not isinstance(values, list):
        raise ValueError(str(path) + ": expected a JSON object array, got " + type(values).__name__)
    decoder = json.JSONDecoder()
    cursor = content.index("[") + 1
    line_cursor, line_number = 0, 1
    records = []
    for index, value in enumerate(values):
        while cursor < len(content) and (content[cursor].isspace() or content[cursor] == ","):
            cursor += 1
        line_number += content.count("\n", line_cursor, cursor)
        line_cursor = cursor
        _, cursor = decoder.raw_decode(content, cursor)
        records.append({"raw": value, "fingerprint": fingerprint(value),
                        "locator": {"path": str(path.resolve()), "line": line_number, "index": index}})
    return records


class Analyzer:
    def __init__(self, max_text=2000):
        self.max_text = max_text
        self.issues = []
        self.sources = {}
        self.records = []
        self.metrics = {}
        self.distributions = {}
        self.by_source = {}

    def issue(self, code, message, source, record_id=None, severity="warning"):
        item = {"code": code, "severity": severity, "message": message, "source": source}
        if record_id:
            item["record_id"] = record_id
        self.issues.append(item)

    def clip(self, value):
        value = text(value)
        return value[:self.max_text], len(value) > self.max_text

    def evidence(self, record, path, value):
        excerpt, truncated = self.clip(value)
        item = {"id": record["id"] + ":e" + str(len(record["evidence"])),
                "path": path, "excerpt": excerpt}
        if truncated:
            item["truncated"] = True
            record["truncated"] = True
        record["evidence"].append(item)

    def put_text(self, record, key, value):
        record[key], truncated = self.clip(value)
        if truncated:
            record["truncated"] = True

    def obj(self, value, source, record, path, expected=dict):
        if isinstance(value, str):
            try:
                value = parse_json(value)
            except (ValueError, TypeError):
                self.issue("nested_json_error", "无法解析嵌套 JSON: " + path, source, record["id"], "error")
                record["signals"].append("parse_error")
                self.evidence(record, path, value)
                return expected()
        if isinstance(value, expected):
            return value
        self.issue("nested_structure_error", "字段结构无效: " + path, source, record["id"], "error")
        record["signals"].append("parse_error")
        self.evidence(record, path, value)
        return expected()

    def question(self, value, record=None, path="question"):
        scene = None
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{"):
                try:
                    decoded = parse_json(value)
                    if isinstance(decoded, dict):
                        value = decoded
                except ValueError:
                    if record is not None:
                        self.issue("question_json_error", "问题字段疑似 JSON 但无法解析，原文保留: " + path,
                                   record["source"], record["id"], "error")
                        record["signals"].append("parse_error")
            elif stripped.startswith("场景：") and "问题：" in stripped:
                match = re.match(r"场景：(.*?)，?\s*问题：(.*)", stripped, re.S)
                if match:
                    scene, value = match.groups()
                    scene = scene.rstrip("，, ")
        if isinstance(value, dict):
            scene = value.get("scene")
            value = value.get("question", "")
        if scene is None or text(scene).strip().lower() in ("", "none", "null", "无场景"):
            scene = None
        return text(value), text(scene) if scene is not None else None

    def base_record(self, source, entry):
        raw = entry["raw"]
        raw_dict = raw if isinstance(raw, dict) else {}
        request_id = valid_id(raw_dict.get("requestId"))
        if source != "feedback" and request_id:
            record_id = source + ":request:" + request_id
        else:
            record_id = source + ":sha256:" + entry["fingerprint"]
        record = {"id": record_id, "source": source, "question": "", "answer": "",
                  "evidence": [], "signals": [], "raw_locator": entry["locator"], "truncated": False}
        if request_id:
            record["request_id"] = request_id
        for original, normalized in [("projectId", "project_id"), ("projectName", "project"),
                                     ("projectProduct", "product"), ("projectVersion", "version")]:
            if original in raw_dict:
                record[normalized] = raw_dict[original]
        timestamp = raw_dict.get("createTime") if source == "feedback" else raw_dict.get("updateTime")
        record["time"] = number(timestamp)
        if "status" in raw_dict:
            record["response_status"] = raw_dict["status"]
        if not isinstance(raw, dict):
            self.issue("record_not_object", "数组项不是对象，保留定位信息。", source, record_id, "error")
            record["signals"].append("parse_error")
            self.evidence(record, "$", raw)
        return record

    @staticmethod
    def latest(entry):
        raw = entry["raw"] if isinstance(entry["raw"], dict) else {}
        stamp = number(raw.get("updateTime"))
        return (stamp if stamp is not None else -math.inf, entry["fingerprint"])

    def deduplicate(self, source, entries):
        selected = {}
        missing = []
        duplicates = 0
        for entry in entries:
            raw = entry["raw"] if isinstance(entry["raw"], dict) else {}
            if source == "feedback":
                key = entry["fingerprint"]
            else:
                key = valid_id(raw.get("requestId"))
                if not key:
                    # No request key means no safe event deduplication. The index
                    # suffix below identifies a retained occurrence, not a business ID.
                    missing.append(entry)
                    continue
            if key in selected:
                duplicates += 1
                if source != "feedback" and self.latest(entry) > self.latest(selected[key]):
                    selected[key] = entry
                if source != "feedback":
                    self.issue("duplicate_request_id", "重复 requestId，按最新 updateTime 保留；同时间以内容指纹确定。", source)
            else:
                selected[key] = entry
        result = list(selected.values()) + missing
        result.sort(key=lambda entry: entry["locator"]["index"])
        return result, duplicates

    def normalize_feedback(self, record, raw):
        question, scene = self.question(raw.get("question", ""), record)
        self.put_text(record, "question", question)
        record["scene"] = scene
        record["chat_id"] = valid_id(raw.get("chatId"))
        record["feedback"] = raw.get("feedback")
        self.put_text(record, "reason", raw.get("reason"))
        record["has_reason"] = bool(text(raw.get("reason")).strip())
        record["mark"] = raw.get("mark") is True
        if raw.get("feedback") == -1 and not isinstance(raw.get("feedback"), bool):
            record["signals"].append("negative_feedback")
        if raw.get("mark") is True:
            record["signals"].append("human_marked")
        if not isinstance(raw.get("feedback"), int) or isinstance(raw.get("feedback"), bool) or raw.get("feedback") not in (-1, 0, 3):
            self.issue("unknown_feedback", "feedback 不是 -1、0 或 3；不进入已评价分母。", "feedback", record["id"])
        for path in ("question", "feedback", "reason", "mark", "createTime"):
            if path in raw:
                self.evidence(record, path, raw[path])

    def normalize_guardrail(self, record, raw):
        body = self.obj(raw.get("body"), "guardrail", record, "body")
        response = self.obj(raw.get("responseData"), "guardrail", record, "responseData")
        questions = body.get("questions") if isinstance(body.get("questions"), list) else []
        self.put_text(record, "question", "\n".join(text(q) for q in questions))
        record["checks"] = []
        record["ignored_checks"] = [text(item) for item in body.get("ignore_items", [])] if isinstance(body.get("ignore_items"), list) else []
        results = response.get("checkResults")
        if not isinstance(results, list):
            self.issue("invalid_check_results", "checkResults 缺失或不是数组。", "guardrail", record["id"], "error")
            results = []
        messages = []
        for index, result in enumerate(results):
            path = "responseData.checkResults[" + str(index) + "]"
            self.evidence(record, path, result)
            if not isinstance(result, dict) or not isinstance(result.get("isLegal"), bool):
                self.issue("invalid_is_legal", "检查结果 isLegal 必须为布尔值；该项不进入有效分母。", "guardrail", record["id"], "error")
                continue
            record["checks"].append({"is_legal": result["isLegal"],
                                     "question": self.clip(result.get("question", ""))[0]})
            if not result["isLegal"]:
                record["signals"].append("guardrail_rejection")
                messages.append(text(result.get("response") or result.get("error_desc")))
        if not questions:
            self.put_text(record, "question", "\n".join(check["question"] for check in record["checks"]))
        self.put_text(record, "answer", "\n".join(messages))
        self.evidence(record, "body.questions", questions)
        record["cost_ms"] = number(raw.get("responseCost"))

    def normalize_dialogue(self, record, raw):
        body = self.obj(raw.get("body"), "dialogue", record, "body")
        detail = self.obj(body.get("detail"), "dialogue", record, "body.detail")
        record["chat_id"] = valid_id(detail.get("id"))
        record["conversation_id"] = valid_id(detail.get("conversationId"))
        question_value = detail.get("question", body.get("question", ""))
        question, scene = self.question(question_value, record, "body.detail.question")
        self.put_text(record, "question", question)
        record["scene"] = scene
        record["feedback"] = detail.get("feedback")
        record["mark"] = detail.get("mark") is True
        record["time"] = number(detail.get("askTime"))
        record["cost_ms"] = number(detail.get("costTime"))
        self.evidence(record, "body.detail.question", question_value)
        self.evidence(record, "body.detail.feedback", detail.get("feedback"))
        self.evidence(record, "body.detail.costTime", detail.get("costTime"))
        record["nodes"] = []
        answers = detail.get("answers", [])
        if not isinstance(answers, list):
            self.issue("invalid_answers", "answers 不是数组。", "dialogue", record["id"], "error")
            answers = []
        full_answer = []
        for index, node in enumerate(answers):
            path = "body.detail.answers[" + str(index) + "]"
            if not isinstance(node, dict):
                self.issue("invalid_node", "回答节点不是对象。", "dialogue", record["id"], "error")
                self.evidence(record, path, node)
                continue
            node_text, truncated = self.clip(node.get("data"))
            normalized = {"time": number(node.get("answerTime")), "level": node.get("level"),
                          "type": node.get("type"), "status": node.get("status"),
                          "recipe": node.get("recipeName"), "cost_ms": number(node.get("costTime")),
                          "text": node_text, "truncated": truncated, "original_index": index}
            record["nodes"].append(normalized)
            if truncated:
                record["truncated"] = True
            if node.get("level") == "ANSWER":
                full_answer.append((normalized["time"], index, text(node.get("data"))))
            self.evidence(record, path + ".data", node.get("data"))
            self.evidence(record, path + ".status", node.get("status"))
        record["nodes"].sort(key=lambda node: (node["time"] is None, node["time"] or 0, node["original_index"]))
        full_answer.sort(key=lambda item: (item[0] is None, item[0] or 0, item[1]))
        self.put_text(record, "answer", "\n".join(item[2] for item in full_answer))
        ended = False
        for node in record["nodes"]:
            if ended and node["level"] == "ANSWER":
                record["signals"].append("end_before_later_answer")
            if node["status"] == "END":
                ended = True
        outer_time = number(raw.get("updateTime"))
        if outer_time is not None and record["time"] is not None and outer_time < record["time"]:
            self.issue("outer_time_before_ask", "外壳 updateTime 早于 askTime；不用其推导对话耗时。", "dialogue", record["id"])

    def parse_sse(self, record, response):
        generations = {}
        models = []
        terminal = False
        finish_reasons = []
        for line_index, line in enumerate(response.splitlines()):
            if not line.lstrip().startswith("data:"):
                continue
            payload = line.lstrip()[5:].strip()
            if payload == "[DONE]":
                terminal = True
                continue
            try:
                chunk = parse_json(payload)
                if not isinstance(chunk, dict) or not isinstance(chunk.get("choices", []), list):
                    raise ValueError("chunk must be object")
            except (ValueError, TypeError):
                self.issue("invalid_sse_chunk", "SSE data 行无法解析，响应行 " + str(line_index + 1), "inference", record["id"], "error")
                record["signals"].append("parse_error")
                continue
            model = valid_id(chunk.get("model"))
            if model and model not in models:
                models.append(model)
            for choice in chunk.get("choices", []):
                if not isinstance(choice, dict):
                    self.issue("invalid_sse_choice", "SSE choice 不是对象。", "inference", record["id"], "error")
                    continue
                generation = text(chunk.get("id", "request-local"))
                choice_index = choice.get("index", 0)
                key = (generation, text(choice_index))
                if key not in generations:
                    generations[key] = {"id": generation, "choice_index": choice_index, "parts": [], "reasoning": []}
                delta = choice.get("delta")
                if isinstance(delta, dict):
                    if delta.get("content") is not None:
                        generations[key]["parts"].append(text(delta["content"]))
                    if delta.get("reasoning_content") is not None:
                        generations[key]["reasoning"].append(text(delta["reasoning_content"]))
                    if delta.get("tool_calls"):
                        record["signals"].append("tool_call_observed")
                if choice.get("finish_reason") is not None:
                    terminal = True
                    finish_reasons.append(text(choice["finish_reason"]))
        if not terminal:
            record["signals"].append("incomplete_stream")
            record["truncated"] = True
            self.issue("incomplete_sse", "未观察到 finish_reason 或 [DONE]，可能为截断响应。", "inference", record["id"])
        answer = []
        record["generations"] = []
        for item in generations.values():
            content = "".join(item["parts"])
            answer.append(content)
            clipped, cut = self.clip(content)
            record["generations"].append({"id": item["id"], "choice_index": item["choice_index"], "text": clipped, "truncated": cut})
            self.evidence(record, "responseData.generation[" + item["id"] + "].choice[" + text(item["choice_index"]) + "]", content)
            if item["reasoning"]:
                self.evidence(record, "responseData.reasoning_content", "".join(item["reasoning"]))
        return "\n".join(answer), models, finish_reasons

    def normalize_inference(self, record, raw):
        body = self.obj(raw.get("body"), "inference", record, "body")
        record["requested_model"] = valid_id(body.get("model"))
        record["first_token_ms"] = number(raw.get("firstTokenLatency"))
        record["cost_ms"] = number(raw.get("responseCost"))
        record["stream"] = raw.get("isStream") == 1 or body.get("stream") is True
        messages = body.get("messages", [])
        if not isinstance(messages, list):
            self.issue("invalid_messages", "messages 不是数组。", "inference", record["id"], "error")
            messages = []
        for index, message in enumerate(messages):
            if isinstance(message, dict):
                if message.get("role") == "user":
                    self.put_text(record, "question", message.get("content"))
                self.evidence(record, "body.messages[" + str(index) + "].content", message.get("content"))
        self.evidence(record, "body.model", body.get("model"))
        response = raw.get("responseData")
        response_text = text(response)
        parsed = response if isinstance(response, dict) else None
        if parsed is None and isinstance(response, str) and response.lstrip().startswith("{"):
            try:
                parsed = parse_json(response)
            except ValueError:
                pass
        answer = ""
        models = []
        finishes = []
        if raw.get("status") == 403 or isinstance(parsed, dict) and "guardrail_message" in parsed:
            record["response_format"] = "guardrail_rejection"
            record["signals"].append("guardrail_rejection")
            message = parsed.get("guardrail_message") if isinstance(parsed, dict) else None
            answer = text(message.get("content")) if isinstance(message, dict) else response_text
        elif isinstance(response, str) and re.search(r"(?m)^\s*data:", response):
            record["response_format"] = "sse"
            answer, models, finishes = self.parse_sse(record, response)
        elif isinstance(parsed, dict):
            record["response_format"] = "json"
            if valid_id(parsed.get("model")):
                models.append(valid_id(parsed["model"]))
            contents = []
            choices = parsed.get("choices", [])
            if not isinstance(choices, list):
                self.issue("invalid_choices", "响应 choices 不是数组。", "inference", record["id"], "error")
                choices = []
            for index, choice in enumerate(choices):
                if not isinstance(choice, dict):
                    self.issue("invalid_choice", "响应 choice 不是对象。", "inference", record["id"], "error")
                    continue
                message = choice.get("message")
                if isinstance(message, dict):
                    contents.append(text(message.get("content")))
                    if message.get("tool_calls"):
                        record["signals"].append("tool_call_observed")
                        self.evidence(record, "responseData.choices[" + str(index) + "].message.tool_calls", message["tool_calls"])
                if choice.get("finish_reason") is not None:
                    finishes.append(text(choice["finish_reason"]))
            answer = "\n".join(contents)
        else:
            record["response_format"] = "unparseable"
            self.issue("unparseable_response", "推理响应不是可识别的 JSON 或 SSE。", "inference", record["id"], "error")
            record["signals"].append("parse_error")
            answer = response_text
        self.put_text(record, "answer", answer)
        record["actual_model"] = models[0] if models else None
        record["actual_models"] = models
        record["finish_reasons"] = finishes
        self.evidence(record, "responseData", response)
        if "length" in finishes:
            record["signals"].append("output_truncation_candidate")
        if record["requested_model"] and models and any(model != record["requested_model"] for model in models):
            record["signals"].append("model_route_difference")
        status = number(raw.get("status"))
        if status is not None and status >= 400 and status != 403:
            record["signals"].append("http_error")

    def normalize(self, source, entries):
        records = []
        seen = set()
        for entry in entries:
            record = self.base_record(source, entry)
            if record["id"] in seen:
                # Preserve missing-ID repeated occurrences without pretending
                # that this occurrence locator is a joinable business ID.
                record["id"] += ":occurrence:" + str(entry["locator"]["index"])
                record["evidence"] = []
            seen.add(record["id"])
            raw = entry["raw"] if isinstance(entry["raw"], dict) else {}
            getattr(self, "normalize_" + source)(record, raw)
            if not record["evidence"]:
                self.evidence(record, "$", raw)
            record["signals"] = list(dict.fromkeys(record["signals"]))
            record["_update_time"] = number(raw.get("updateTime"))
            record["_fingerprint"] = entry["fingerprint"]
            records.append(record)
        if source == "dialogue":
            selected = {}
            missing = []
            for record in records:
                key = record["chat_id"]
                if not key:
                    self.issue("missing_dialogue_id", "detail.id 缺失；保留记录但不进入独立对话数或关联分母。", source, record["id"])
                    missing.append(record)
                elif key in selected:
                    self.issue("duplicate_dialogue_snapshot", "相同 detail.id 的多份快照只保留最新 updateTime 用于业务指标。", source, record["id"])
                    def rank(item):
                        stamp = item["_update_time"]
                        return (stamp if stamp is not None else -math.inf, item["_fingerprint"])
                    if rank(record) > rank(selected[key]):
                        selected[key] = record
                else:
                    selected[key] = record
            records = list(selected.values()) + missing
        return records

    def metric(self, key, label, value, unit, source, numerator=None, denominator=None, reason=None):
        item = {"label": label, "value": value, "unit": unit, "source": source, "basis": "computed"}
        if numerator is not None:
            item["numerator"] = numerator
        if denominator is not None:
            item["denominator"] = denominator
        if reason:
            item["reason"] = reason
        self.metrics[key] = item

    def count(self, key, label, value, source):
        provided = self.sources[source]["status"] == "imported"
        self.metric(key, label, value if provided else None, "count", source,
                    reason=None if provided else "未提供该来源原始文件。")

    def ratio(self, key, label, numerator, denominator, source, available=True):
        value = numerator / denominator if available and denominator else None
        self.metric(key, label, value, "ratio", source,
                    numerator=numerator if available else None, denominator=denominator if available else None,
                    reason="缺少计算所需的原始来源。" if not available else "有效分母为零。" if not denominator else None)

    @staticmethod
    def p95(values):
        values = sorted(values)
        return values[math.ceil(len(values) * 0.95) - 1] if values else None

    @staticmethod
    def distribution(values):
        return [{"name": key, "value": count} for key, count in sorted(Counter(values).items())]

    def link_feedback(self):
        feedback = self.by_source["feedback"]
        dialogues = {r["chat_id"]: r for r in self.by_source["dialogue"] if r.get("chat_id")}
        available = all(self.sources[source]["status"] == "imported" for source in ("feedback", "dialogue"))
        eligible = [r for r in feedback if r.get("chat_id")]
        matched = []
        histories = {}
        if available:
            for record in eligible:
                target = dialogues.get(record["chat_id"])
                if target is not None:
                    record["linked_dialogue_id"] = target["id"]
                    matched.append(record)
                    histories.setdefault(record["chat_id"], []).append(record)
                    self.evidence(target, "linked_feedback[" + record["id"] + "].feedback", record["feedback"])
                    if record.get("has_reason"):
                        self.evidence(target, "linked_feedback[" + record["id"] + "].reason", record["reason"])
            for chat_id, history in histories.items():
                target = dialogues[chat_id]
                target["feedback_history"] = [record["id"] for record in history]
                target["exported_feedback"] = target.get("feedback")
                valid = [r for r in history if isinstance(r.get("feedback"), int) and not isinstance(r["feedback"], bool)
                         and r["feedback"] in (-1, 0, 3) and r.get("time") is not None]
                if valid:
                    latest_time = max(r["time"] for r in valid)
                    latest = [r for r in valid if r["time"] == latest_time]
                    if len({r["feedback"] for r in latest}) > 1:
                        target["feedback"] = None
                        self.issue("feedback_time_conflict", "最新 createTime 存在不同反馈值，不任意选择。", "feedback", target["id"])
                    else:
                        target["feedback"] = latest[0]["feedback"]
        self.metric("feedback_match_count", "确定匹配对话的反馈事件数", len(matched) if available else None,
                    "count", "feedback+dialogue", reason=None if available else "须同时提供反馈与对话原始文件。")
        self.ratio("feedback_link_rate", "有效 chatId 的反馈事件关联覆盖", len(matched), len(eligible), "feedback+dialogue", available)
        self.ratio("dialogue_feedback_coverage", "去重对话记录的反馈覆盖", len(histories), len(dialogues), "feedback+dialogue", available)
        self.count("feedback_missing_chat_id", "缺少有效 chatId 的反馈事件", len(feedback) - len(eligible), "feedback")
        self.count("orphan_feedback", "未匹配详情的有效 chatId 反馈事件", len(eligible) - len(matched), "feedback")
        if not available:
            self.metrics["orphan_feedback"]["value"] = None
            self.metrics["orphan_feedback"]["reason"] = "须同时提供反馈与对话原始文件。"

    def compute(self):
        feedback = self.by_source["feedback"]
        guardrails = self.by_source["guardrail"]
        dialogues = self.by_source["dialogue"]
        identified = [r for r in dialogues if r.get("chat_id")]
        inference = self.by_source["inference"]
        for source, key, label in [("feedback", "feedback_records", "反馈去重事件数"),
                                    ("guardrail", "guardrail_records", "护栏去重日志数"),
                                    ("inference", "inference_records", "推理去重日志数")]:
            self.count(key, label, len(self.by_source[source]), source)
        self.count("dialogue_records", "按 detail.id 去重的独立对话记录数", len(identified), "dialogue")
        self.count("dialogue_missing_id", "缺少 detail.id 的未去重详情记录", len(dialogues) - len(identified), "dialogue")
        self.count("answer_nodes", "业务快照中保留的回答节点数", sum(len(r["nodes"]) for r in dialogues), "dialogue")
        self.count("conversation_count", "按 conversationId 去重的多轮会话数", len({r["conversation_id"] for r in dialogues if r.get("conversation_id")}), "dialogue")
        rated = [r for r in feedback if isinstance(r.get("feedback"), int) and not isinstance(r["feedback"], bool) and r["feedback"] in (-1, 3)]
        self.count("rated_feedback", "已评价反馈事件数（-1或3）", len(rated), "feedback")
        self.ratio("negative_feedback_rate", "已评价反馈事件中的差评率", sum(r["feedback"] == -1 for r in rated), len(rated), "feedback", self.sources["feedback"]["status"] == "imported")
        self.ratio("reason_coverage", "反馈原因填写率", sum(r.get("has_reason", False) for r in feedback), len(feedback), "feedback", self.sources["feedback"]["status"] == "imported")
        self.ratio("scene_missing_rate", "独立对话记录的场景缺失比例", sum(r["scene"] is None for r in identified), len(identified), "dialogue", self.sources["dialogue"]["status"] == "imported")
        self.ratio("end_marker_coverage", "独立对话记录的结束标志覆盖（不是成功率）", sum(any(n["status"] == "END" for n in r["nodes"]) for r in identified), len(identified), "dialogue", self.sources["dialogue"]["status"] == "imported")
        eligible_checks = [r for r in guardrails if r["checks"]]
        checks = [check for r in guardrails for check in r["checks"]]
        self.ratio("guardrail_block_rate", "有效护栏请求的请求级拦截率", sum(any(not c["is_legal"] for c in r["checks"]) for r in eligible_checks), len(eligible_checks), "guardrail", self.sources["guardrail"]["status"] == "imported")
        self.ratio("guardrail_question_block_rate", "有效护栏检查结果的问题级拦截率", sum(not c["is_legal"] for c in checks), len(checks), "guardrail", self.sources["guardrail"]["status"] == "imported")
        self.ratio("inference_http_200_rate", "推理日志 HTTP 200 占比（不是准确率）", sum(r.get("response_status") == 200 for r in inference), len(inference), "inference", self.sources["inference"]["status"] == "imported")
        comparable = [r for r in inference if r.get("requested_model") and r.get("actual_models")]
        self.ratio("model_route_difference_rate", "可比较请求中的模型路由差异比例", sum(any(m != r["requested_model"] for m in r["actual_models"]) for r in comparable), len(comparable), "inference", self.sources["inference"]["status"] == "imported")
        costs = [r["cost_ms"] for r in dialogues if r["cost_ms"] is not None and r["cost_ms"] >= 0]
        tokens = [r["first_token_ms"] for r in inference if r["stream"] and r["first_token_ms"] is not None and r["first_token_ms"] > 0]
        self.metric("dialogue_cost_p95_ms", "详情业务快照耗时 P95（nearest rank）", self.p95(costs), "ms", "dialogue", denominator=len(costs), reason=None if costs else "无有效非负 detail.costTime。")
        self.metric("first_token_p95_ms", "流式请求正值首 token 延迟 P95（nearest rank）", self.p95(tokens), "ms", "inference", denominator=len(tokens), reason=None if tokens else "无流式请求的有效正值测量；0 值不视为有效测量。")
        self.count("first_token_zero_records", "首 token 延迟为0的推理日志数", sum(r.get("first_token_ms") == 0 for r in inference), "inference")
        for key, label, reason in [("accuracy", "回答准确率", "缺少具有来源的人工正确性标签或参考答案。"),
                                   ("rag_recall", "RAG 召回率", "缺少检索记录与相关文档金标。"),
                                   ("resolution_rate", "历史问题解决率", "缺少确认案例、修复状态和复测依据。")]:
            self.metric(key, label, None, "ratio", "unavailable", reason=reason)
        self.distributions["scenes"] = self.distribution(r["scene"] or "无场景" for r in identified)
        self.distributions["request_models"] = self.distribution(r["requested_model"] or "未知" for r in inference)
        self.distributions["http_status"] = self.distribution(text(r.get("response_status")) or "未知" for r in inference)
        self.distributions["response_formats"] = self.distribution(r["response_format"] for r in inference)
        recipes = Counter()
        for record in identified:
            recipes.update({n["recipe"] for n in record["nodes"] if valid_id(n["recipe"])})
        self.distributions["recipe_dialogue_coverage"] = [{"name": name, "value": count, "denominator": len(identified), "ratio": count / len(identified)} for name, count in sorted(recipes.items())]
        self.link_feedback()

    def run(self, paths):
        source_files = []
        for source in FILENAMES:
            path = paths.get(source)
            if path is None:
                self.sources[source] = {"status": "not_provided", "records": 0, "raw_records": 0, "duplicates": 0, "errors": 0}
                self.by_source[source] = []
                continue
            entries = read_array(path)
            source_files.append(str(path.resolve()))
            retained, duplicates = self.deduplicate(source, entries)
            self.sources[source] = {"status": "imported", "records": len(retained), "raw_records": len(entries), "duplicates": duplicates, "errors": 0}
            self.by_source[source] = self.normalize(source, retained)
        self.compute()
        for source, records in self.by_source.items():
            self.sources[source]["errors"] = sum(issue["source"] == source and issue["severity"] == "error" for issue in self.issues)
            for record in records:
                record.pop("_update_time", None)
                record.pop("_fingerprint", None)
                self.records.append(record)
        return {"schema_version": "1.0", "provenance": {"mode": "raw_json", "generated_at": utc_now(),
                "source_files": source_files, "notes": ["仅统计当前提供的原始文件；不叠加文档摘要或实例。",
                    "sources.dialogue.records 为去重网关日志数；dialogue_records 为去重 detail.id 业务数。",
                    "文本为有长度上限的摘录；原文由 raw_locator 指向输入文件及行号。",
                    "缺少统一关联键的独立护栏和推理日志不与对话做推测关联。"]},
                "sources": self.sources, "metrics": self.metrics, "distributions": self.distributions,
                "records": self.records, "quality_issues": self.issues}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_analysis(analysis, directory):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "analysis.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(directory), prefix=".analysis-", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(analysis, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地分析智能体四类日志，输出 analysis.json。Python 3.9+，无第三方依赖。")
    parser.add_argument("--snapshot", action="store_true", help="加载随包提供的文档快照，不重算实例为总体指标")
    parser.add_argument("--input-dir", type=Path, help="包含四类默认中文文件名的目录；允许部分来源缺失")
    for source, name in FILENAMES.items():
        parser.add_argument("--" + source, type=Path, help="单独指定 " + name + " 的路径")
    parser.add_argument("--out", type=Path, required=True, help="输出目录，写入 analysis.json")
    parser.add_argument("--max-text", type=int, default=2000, help="每个文本摘录的最大字符数，默认2000")
    args = parser.parse_args(argv)
    explicit = {source: getattr(args, source) for source in FILENAMES if getattr(args, source) is not None}
    if args.max_text < 1:
        parser.error("--max-text must be positive")
    if args.snapshot and (args.input_dir is not None or explicit):
        parser.error("--snapshot cannot be combined with raw JSON inputs")
    if not args.snapshot and args.input_dir is None and not explicit:
        parser.error("provide --snapshot, --input-dir, or an individual source path")
    try:
        if args.snapshot:
            snapshot = Path(__file__).resolve().parents[1] / "assets" / "document_snapshot.json"
            analysis = parse_json(snapshot.read_text(encoding="utf-8"))
            if not isinstance(analysis, dict) or not isinstance(analysis.get("provenance"), dict):
                raise ValueError("Invalid document snapshot structure")
            analysis["provenance"]["mode"] = "document_snapshot"
            analysis["provenance"]["generated_at"] = utc_now()
        else:
            if args.input_dir is not None and not args.input_dir.is_dir():
                raise ValueError("Input directory does not exist: " + str(args.input_dir))
            paths = {}
            for source, name in FILENAMES.items():
                if source in explicit:
                    if not explicit[source].is_file():
                        raise ValueError("Source file does not exist: " + str(explicit[source]))
                    paths[source] = explicit[source]
                elif args.input_dir is not None:
                    candidate = args.input_dir / name
                    if candidate.is_file():
                        paths[source] = candidate
            analysis = Analyzer(args.max_text).run(paths)
        target = write_analysis(analysis, args.out)
    except (OSError, ValueError, TypeError, RecursionError) as error:
        print("analysis error: " + str(error), file=sys.stderr)
        return 2
    print(str(target.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
