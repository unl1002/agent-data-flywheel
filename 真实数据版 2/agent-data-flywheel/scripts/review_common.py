"""Shared, deterministic review contracts. No model calls or network access."""
import hashlib
import json
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_analysis(value):
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise ValueError("analysis.json 的 schema_version 必须为 1.0")
    for key in ("provenance", "sources", "metrics", "distributions"):
        if not isinstance(value.get(key), dict):
            raise ValueError("分析文件缺少对象字段: " + key)
    records = value.get("records")
    if not isinstance(records, list):
        raise ValueError("records 必须为数组")
    seen = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
            raise ValueError("规范化记录必须有非空字符串 id")
        if record["id"] in seen:
            raise ValueError("记录 ID 重复: " + record["id"])
        seen.add(record["id"])
        if record.get("source") not in {"dialogue", "feedback", "guardrail", "inference"}:
            raise ValueError("未知记录来源: " + record["id"])
        if not isinstance(record.get("evidence"), list):
            raise ValueError("记录缺少 evidence 数组: " + record["id"])
    return value


def question_records(analysis):
    return [r for r in analysis["records"]
            if r.get("question", "").strip()
            and (r["source"] == "dialogue" or
                 (r["source"] == "feedback" and not r.get("linked_dialogue_id")))]


def capabilities(path=None, *, data=None):
    value = data if data is not None else (load_json(path) if path else {"schema_version": "1.0", "rules": [], "evaluations": []})
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise ValueError("能力文件必须为 schema_version=1.0 的对象")
    for group in ("rules", "evaluations"):
        rows = value.get(group, [])
        if not isinstance(rows, list):
            raise ValueError(group + " 必须是数组")
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                raise ValueError(group + " 中每条依据需要 id")
            if row["id"] in seen:
                raise ValueError(group + " 中 id 重复")
            seen.add(row["id"])
            if not isinstance(row.get("evidence_source"), str) or not row["evidence_source"].strip():
                raise ValueError("依据必须有非空 evidence_source: " + row["id"])
            if group == "rules" and row.get("status") not in {"supported", "unsupported", "partial"}:
                raise ValueError("未知能力状态: " + row["id"])
            if group == "evaluations":
                if row.get("kind") not in {"human_label", "reference_answer"} or row.get("correctness") not in {"correct", "incorrect"}:
                    raise ValueError("评测依据类型或结论无效: " + row["id"])
                if not row.get("record_id"):
                    raise ValueError("评测依据须绑定 record_id: " + row["id"])
    value.setdefault("rules", [])
    value.setdefault("evaluations", [])
    return value


def metric(label, value, unit="count", **kwargs):
    return dict(label=label, value=value, unit=unit, **kwargs)


def no_review():
    return {"status": "not_run", "scope": None, "items": [], "metrics": {},
            "coverage": {"reviewed": 0, "available": 0, "ratio": None, "by_source": [],
                         "note": "尚未进行 AI 语义评审"}, "notes": ["脚本统计不代表 AI 已完成语义判断。"]}
