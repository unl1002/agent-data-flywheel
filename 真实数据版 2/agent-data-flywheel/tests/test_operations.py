"""Synthetic boundary tests for metric membership and project/month lineage."""
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "operations.py"


def ms(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def record(rid, source, index, **values):
    value = {"id": rid, "source": source, "question": "synthetic", "answer": "",
             "time": ms("2026-01-15T00:00:00Z"), "evidence": [], "signals": [],
             "raw_locator": {"path": "/synthetic/" + source + ".json", "index": index, "line": index * 10 + 2}}
    value.update(values)
    return value


def fixture():
    rows = [
        record("f-", "feedback", 0, chat_id="a", feedback=-1, has_reason=False, linked_dialogue_id="d-a"),
        record("f0", "feedback", 1, chat_id="orphan", feedback=0, has_reason=False),
        record("f+", "feedback", 2, chat_id="a", feedback=3, has_reason=True, linked_dialogue_id="d-a"),
        record("d-a", "dialogue", 0, chat_id="a", conversation_id="session-1", project_id="project-1", project="Project One", scene="data", cost_ms=30,
               time=ms("2026-01-31T16:30:00Z"), nodes=[
                   {"original_index": 2, "status": "MESSAGE", "recipe": "R", "time": 30},
                   {"original_index": 0, "status": "END", "recipe": None, "time": 10},
                   {"original_index": 3, "status": "MESSAGE", "recipe": "R", "time": 40}]),
        record("d-b", "dialogue", 1, chat_id="b", conversation_id="session-1", project_id="project-1", project="Project One", scene=None, cost_ms=0,
               time=ms("2026-02-15T00:00:00Z"), nodes=[]),
        record("d-missing", "dialogue", 2, chat_id=None, conversation_id="session-2", project_id="not-a-business-id", scene="other", cost_ms=99, time=None,
               nodes=[{"original_index": 7, "status": "MESSAGE", "recipe": "R", "time": 10}]),
        record("g1", "guardrail", 0, checks=[{"original_index": 0, "is_legal": False}, {"original_index": 2, "is_legal": True}]),
        record("g2", "guardrail", 1, checks=[]),
        record("i1", "inference", 0, requested_model="M", actual_models=["M"], stream=True, first_token_ms=10, response_status=200, response_format="sse"),
        record("i2", "inference", 1, requested_model="M", actual_models=[], stream=False, first_token_ms=30, response_status=403, response_format="guardrail_rejection"),
        record("i3", "inference", 2, requested_model="M", actual_models=["N"], stream=True, first_token_ms=0, response_status=200, response_format="json", time=None),
    ]
    metrics = {}
    def metric(key, value, unit="count", numerator=None, denominator=None):
        metrics[key] = {"label": key, "value": value, "unit": unit, "source": key.split("_")[0], "basis": "computed"}
        if numerator is not None: metrics[key]["numerator"] = numerator
        if denominator is not None: metrics[key]["denominator"] = denominator
    for key, value in {"feedback_records": 3, "guardrail_records": 2, "inference_records": 3,
                       "dialogue_records": 2, "dialogue_missing_id": 1, "answer_nodes": 4,
                       "conversation_count": 2, "rated_feedback": 2, "first_token_zero_records": 1,
                       "feedback_match_count": 2, "feedback_missing_chat_id": 0, "orphan_feedback": 1}.items(): metric(key, value)
    for key, numerator, denominator in [("negative_feedback_rate", 1, 2), ("reason_coverage", 1, 3),
             ("scene_missing_rate", 1, 2), ("end_marker_coverage", 1, 2), ("guardrail_block_rate", 1, 1),
             ("guardrail_question_block_rate", 1, 2), ("inference_http_200_rate", 2, 3),
             ("model_route_difference_rate", 1, 2), ("feedback_link_rate", 2, 3), ("dialogue_feedback_coverage", 1, 2)]:
        metric(key, numerator / denominator, "ratio", numerator, denominator)
    metric("dialogue_cost_p95_ms", 99, "ms", denominator=3)
    metric("first_token_p95_ms", 10, "ms", denominator=1)
    for key in ("accuracy", "rag_recall", "resolution_rate"): metric(key, None, "ratio")
    raw_rows = [{"ref": "raw:" + r["source"] + ":" + str(r["raw_locator"]["index"]),
                 "source": r["source"], "index": r["raw_locator"]["index"], "line": r["raw_locator"]["line"],
                 "sha256": "synthetic-sha", "log_retained": True, "retained_record_id": r["id"], "time": r["time"]} for r in rows]
    raw_rows += [{"ref": "raw:feedback:3", "source": "feedback", "index": 3, "line": 32, "sha256": "synthetic-sha", "log_retained": False, "retained_record_id": "f0", "time": rows[1]["time"]},
                 {"ref": "raw:dialogue:3", "source": "dialogue", "index": 3, "line": 32, "sha256": "synthetic-sha", "log_retained": True, "time": ms("2026-01-20T00:00:00Z")},
                 {"ref": "raw:inference:3", "source": "inference", "index": 3, "line": 32, "sha256": "synthetic-sha", "log_retained": False, "retained_record_id": "i1", "time": rows[-3]["time"]}]
    return {"schema_version": "1.0", "provenance": {"mode": "raw_json", "files": {}},
            "sources": {source: {"status": "imported", "raw_records": raw, "records": kept,
                                  "duplicates": raw-kept, "errors": 2 if source=="guardrail" else 0}
                        for source, raw, kept in [("feedback",4,3),("guardrail",2,2),("dialogue",4,4),("inference",4,3)]},
            "metrics": metrics, "records": rows, "source_rows": raw_rows,
            "quality_issues": [{"source": "guardrail", "severity": "error", "record_id": "g2", "code": "body-error"},
                               {"source": "guardrail", "severity": "error", "record_id": "g2", "code": "response-error"}],
            "distributions": {"scenes": [{"name": "data", "value": 1}, {"name": "无场景", "value": 1}],
                              "request_models": [{"name": "M", "value": 3}],
                              "http_status": [{"name": "200", "value": 2}, {"name": "403", "value": 1}],
                              "response_formats": [{"name": "sse", "value": 1}, {"name": "json", "value": 1}, {"name": "guardrail_rejection", "value": 1}],
                              "recipe_dialogue_coverage": [{"name": "R", "value": 1, "denominator": 2, "ratio": .5}]}}


class OperationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("operations_under_test", MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def enrich(self, value=None):
        value = fixture() if value is None else value
        returned = self.module.enrich(value)
        self.assertIs(returned, value)
        return value

    def test_all_core_metrics_and_distributions_have_lineage(self):
        data = self.enrich()
        for key, metric in data["metrics"].items():
            self.assertEqual(metric["trace_id"], "metric:" + key)
            trace = data["lineage"][metric["trace_id"]]
            self.assertEqual(trace["value"], metric["value"])
            for field in ("formula", "member_refs", "numerator_refs", "denominator_refs", "excluded_refs"):
                self.assertIn(field, trace)
        for group in data["distributions"].values():
            for item in group:
                self.assertEqual(data["lineage"][item["trace_id"]]["value"], item["value"])

    def test_rated_feedback_denominator_preserves_events_and_excludes_zero(self):
        trace = self.enrich()["lineage"]["metric:negative_feedback_rate"]
        self.assertEqual(trace["numerator_refs"], ["f-"])
        self.assertEqual(set(trace["denominator_refs"]), {"f-", "f+"})
        self.assertEqual(trace["excluded_refs"], ["f0"])

    def test_check_and_node_refs_use_original_indexes(self):
        data = self.enrich()
        trace = data["lineage"]["metric:guardrail_question_block_rate"]
        self.assertEqual(trace["numerator_refs"], ["g1#check:0"])
        self.assertEqual(trace["denominator_refs"], ["g1#check:0", "g1#check:2"])
        nodes = data["lineage"]["metric:answer_nodes"]["member_refs"]
        self.assertEqual(set(nodes), {"d-a#node:2", "d-a#node:0", "d-a#node:3", "d-missing#node:7"})

    def test_distinct_dialogue_sessions_and_recipe_have_different_membership(self):
        data = self.enrich()
        self.assertEqual(set(data["lineage"]["metric:dialogue_records"]["member_refs"]), {"d-a", "d-b"})
        sessions = data["lineage"]["metric:conversation_count"]
        self.assertEqual(sessions["value"], 2)
        self.assertEqual(sessions["group_values"], {"d-a": "session-1", "d-b": "session-1", "d-missing": "session-2"})
        row = data["distributions"]["recipe_dialogue_coverage"][0]
        trace = data["lineage"][row["trace_id"]]
        self.assertEqual(trace["member_refs"], ["d-a"])
        self.assertEqual(set(trace["denominator_refs"]), {"d-a", "d-b"})

    def test_p95_keeps_all_eligible_samples_and_selected_rank(self):
        data = self.enrich()
        trace = data["lineage"]["metric:dialogue_cost_p95_ms"]
        self.assertEqual(trace["sample_values"], {"d-a": 30, "d-b": 0, "d-missing": 99})
        self.assertEqual(trace["selected_ref"], "d-missing")
        self.assertEqual(trace["rank"], 3)
        first = data["lineage"]["metric:first_token_p95_ms"]
        self.assertEqual(first["member_refs"], ["i1"])
        self.assertEqual(set(first["excluded_refs"]), {"i2", "i3"})

    def test_source_log_counts_keep_business_discarded_snapshot_and_raw_duplicates(self):
        data = self.enrich()
        sources = data["sources"]
        kept = data["lineage"][sources["dialogue"]["trace_ids"]["records"]]
        self.assertEqual(len(kept["member_refs"]), 4)
        self.assertIn("raw:dialogue:3", kept["member_refs"])
        duplicates = data["lineage"][sources["feedback"]["trace_ids"]["duplicates"]]
        self.assertEqual(duplicates["member_refs"], ["raw:feedback:3"])
        errors = data["lineage"][sources["guardrail"]["trace_ids"]["errors"]]
        self.assertEqual(errors["member_refs"], ["g2", "g2"])
        self.assertEqual(errors["issue_indexes"], {"g2": [0, 1]})

    def test_join_membership_uses_linked_feedback_and_dedup_dialogues(self):
        data = self.enrich()
        self.assertEqual(set(data["lineage"]["metric:feedback_match_count"]["member_refs"]), {"f-", "f+"})
        trace = data["lineage"]["metric:dialogue_feedback_coverage"]
        self.assertEqual(trace["numerator_refs"], ["d-a"])
        self.assertEqual(set(trace["denominator_refs"]), {"d-a", "d-b"})

    def test_months_use_shanghai_time_and_project_business_records_only(self):
        data = self.enrich()
        usage = data["usage"]
        self.assertEqual(usage["timezone"], "Asia/Shanghai")
        self.assertEqual(usage["months"], ["2026-01", "2026-02"])
        self.assertEqual(len(usage["project_rows"]), 1)
        project = usage["project_rows"][0]
        self.assertEqual(project["project_id"], "project-1")
        self.assertEqual(data["lineage"][project["months"]["2026-02"]]["member_refs"], ["d-a", "d-b"])
        self.assertEqual(data["lineage"][usage["metrics"]["active_projects"]]["value"], 1)
        self.assertEqual(data["lineage"][usage["metrics"]["active_sessions"]]["value"], 1)
        self.assertIsNone(data["lineage"][usage["metrics"]["user_count"]]["value"])
        jan = next(item for item in usage["source_months"] if item["month"]=="2026-01" and item["source"]=="dialogue")
        self.assertEqual(data["lineage"][jan["trace_id"]]["member_refs"], ["raw:dialogue:3"])
        feb = usage["monthly_metrics"]["2026-02"]
        self.assertEqual(data["lineage"][feb["business_questions"]]["value"], 2)

    def test_placeholder_and_missing_projects_are_buckets_not_active_projects(self):
        data = fixture()
        data["records"] += [record("d-default", "dialogue", 4, chat_id="default", conversation_id="session-default", project_id="cloud_campus_project_id", project="Default", nodes=[], cost_ms=None, scene=None),
                            record("d-no-project", "dialogue", 5, chat_id="no-project", conversation_id="session-missing", project_id=None, nodes=[], cost_ms=None, scene=None)]
        data["metrics"] = {}; data["distributions"] = {}
        data = self.enrich(data)
        self.assertEqual(len(data["usage"]["project_rows"]), 3)
        trace = data["lineage"][data["usage"]["metrics"]["active_projects"]]
        self.assertEqual(trace["value"], 1)
        self.assertEqual(set(trace["excluded_refs"]), {"d-default", "d-no-project", "d-missing"})
        self.assertEqual(sum(row.get("is_placeholder", False) for row in data["usage"]["project_rows"]), 1)

    def test_zero_denominator_and_unavailable_gold_do_not_become_zero(self):
        data = fixture()
        data["records"] = [r for r in data["records"] if r["source"]=="feedback"]
        for record_ in data["records"]: record_["feedback"] = 0
        for key in ("negative_feedback_rate", "feedback_match_count", "feedback_link_rate", "dialogue_feedback_coverage"):
            data["metrics"][key]["value"] = None
        data["sources"]["dialogue"]["status"] = "not_provided"
        data = self.enrich(data)
        self.assertEqual(data["lineage"]["metric:negative_feedback_rate"]["denominator_refs"], [])
        self.assertIsNone(data["lineage"]["metric:negative_feedback_rate"]["value"])
        self.assertIsNone(data["lineage"]["metric:accuracy"]["value"])
        self.assertEqual(data["lineage"]["metric:feedback_link_rate"]["denominator_refs"], [])

    def test_invalid_check_is_excluded_with_its_original_index(self):
        data = fixture()
        guard = next(r for r in data["records"] if r["id"] == "g1")
        # analyze.py omits this malformed check from normalized checks, but its
        # source evidence retains the original check index.
        guard["evidence"].append({"id": "g1:e1", "path": "responseData.checkResults[1]", "excerpt": '{"isLegal":"false"}'})
        data = self.enrich(data)
        trace = data["lineage"]["metric:guardrail_question_block_rate"]
        self.assertEqual(trace["excluded_refs"], ["g1#check:1"])
        self.assertEqual(len(trace["denominator_refs"]), 2)

    def test_document_snapshot_totals_are_not_attributed_to_example_records(self):
        data = fixture(); data["provenance"]["mode"] = "document_snapshot"
        data["metrics"]["dialogue_records"]["value"] = 468
        data = self.enrich(data)
        trace = data["lineage"]["metric:dialogue_records"]
        self.assertEqual(trace["value"], 468)
        self.assertEqual(trace["member_refs"], [])
        self.assertIn("文档", trace["reason"])
        self.assertEqual(data["usage"]["months"], [])
        self.assertIsNone(data["lineage"][data["usage"]["metrics"]["active_projects"]]["value"])

    def test_missing_snapshot_time_is_unknown_not_copied_from_winner(self):
        data = fixture()
        older = next(r for r in data["source_rows"] if r["ref"] == "raw:dialogue:3")
        older.pop("time")
        older["retained_record_id"] = "d-a"
        data = self.enrich(data)
        trace = data["lineage"][data["usage"]["unknown_time"]["dialogue"]]
        self.assertIn("raw:dialogue:3", trace["member_refs"])
        self.assertEqual(trace["value"], 2)

    def test_enrichment_is_idempotent_and_does_not_change_computed_values(self):
        data = fixture(); values = copy.deepcopy(data["metrics"])
        self.enrich(data); once = copy.deepcopy(data); self.enrich(data)
        self.assertEqual(data, once)
        self.assertEqual({key:item["value"] for key,item in data["metrics"].items()}, {key:item["value"] for key,item in values.items()})


if __name__ == "__main__":
    unittest.main()
