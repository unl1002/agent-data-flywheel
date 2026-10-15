"""Behavioral CLI tests; fixtures are synthetic and never count as project data."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze.py"
NAMES = {"feedback": "用户反馈.json", "guardrail": "安全护栏.json",
         "dialogue": "对话详情.json", "inference": "推理.json"}


class AnalyzeCLITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.inputs = self.base / "input"
        self.inputs.mkdir()
        self.out = self.base / "output"

    def write(self, source, records):
        path = self.inputs / NAMES[source]
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def cli(self, *extra, success=True, input_dir=True, script=SCRIPT):
        command = [sys.executable, str(script), "--out", str(self.out)]
        if input_dir:
            command += ["--input-dir", str(self.inputs)]
        result = subprocess.run(command + list(extra), text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads((self.out / "analysis.json").read_text(encoding="utf-8"))
        self.assertNotEqual(result.returncode, 0)
        return result

    @staticmethod
    def dialogue(chat="c1", request="d1", update=1, nodes=None, **changes):
        detail = {"id": chat, "conversationId": "conversation-1", "feedback": 0,
                  "question": json.dumps({"scene": "diagnose", "question": "诊断设备"}),
                  "costTime": 123, "answers": nodes or []}
        detail.update(changes)
        return {"requestId": request, "updateTime": update,
                "body": json.dumps({"detail": json.dumps(detail)})}

    @staticmethod
    def inference(request, response, status=200, **changes):
        result = {"requestId": request, "updateTime": 100, "status": status,
                  "isStream": 1, "firstTokenLatency": 10,
                  "body": json.dumps({"model": "requested", "stream": True,
                                      "messages": [{"role": "user", "content": "问题"}]}),
                  "responseData": response}
        result.update(changes)
        return result

    def test_feedback_events_are_not_deduplicated_by_chat_and_zero_is_unrated(self):
        negative = {"chatId": "c1", "feedback": -1, "createTime": 10,
                    "reason": None, "mark": True, "question": '{"question":"报告"}'}
        positive = dict(negative, feedback=3, createTime=20, reason="有帮助")
        zero = dict(negative, chatId="c2", feedback=0, createTime=30, mark=False)
        self.write("feedback", [negative, positive, zero, negative])
        self.write("dialogue", [self.dialogue()])
        analysis = self.cli()
        source = analysis["sources"]["feedback"]
        self.assertEqual((source["raw_records"], source["records"], source["duplicates"]), (4, 3, 1))
        metrics = analysis["metrics"]
        self.assertEqual(metrics["rated_feedback"]["value"], 2)
        self.assertEqual(metrics["negative_feedback_rate"]["value"], 0.5)
        self.assertAlmostEqual(metrics["reason_coverage"]["value"], 1 / 3)
        self.assertEqual(metrics["feedback_match_count"]["value"], 2)
        self.assertAlmostEqual(metrics["feedback_link_rate"]["value"], 2 / 3)
        self.assertEqual(metrics["dialogue_feedback_coverage"]["value"], 1)
        feedback = [r for r in analysis["records"] if r["source"] == "feedback"]
        self.assertEqual(sum("linked_dialogue_id" in r for r in feedback), 2)
        dialogue = next(r for r in analysis["records"] if r["source"] == "dialogue")
        self.assertEqual(dialogue["feedback"], 3)
        self.assertTrue(any("linked_feedback" in e["path"] for e in dialogue["evidence"]))
        ids = [r["id"] for r in analysis["records"]]
        self.assertEqual(len(ids), len(set(ids)))
        for record in analysis["records"]:
            self.assertGreaterEqual(record["raw_locator"]["line"], 2)
            self.assertTrue(Path(record["raw_locator"]["path"]).is_absolute())
            self.assertTrue(record["evidence"])
            self.assertTrue(all(e["id"].startswith(record["id"] + ":e") for e in record["evidence"]))

    def test_partial_source_join_and_unknown_outcomes_stay_null(self):
        self.write("feedback", [{"chatId": "c", "feedback": 0, "question": "你好"}])
        analysis = self.cli()
        for key in ["negative_feedback_rate", "feedback_match_count", "feedback_link_rate",
                    "dialogue_feedback_coverage", "accuracy", "rag_recall", "resolution_rate"]:
            self.assertIsNone(analysis["metrics"][key]["value"], key)
        self.assertEqual(analysis["sources"]["dialogue"]["status"], "not_provided")
        self.assertEqual(analysis["metrics"]["rated_feedback"]["value"], 0)

    def test_guardrail_request_and_question_denominators_differ(self):
        rows = []
        for i, results in enumerate([[{"isLegal": False}, {"isLegal": True}, {"isLegal": "false"}],
                                     [{"isLegal": True}]]):
            rows.append({"requestId": "g" + str(i), "body": '{"questions":["a","b"]}',
                         "responseData": json.dumps({"checkResults": results})})
        rows.append({"requestId": "g-broken", "body": "{broken", "responseData": "{broken"})
        self.write("guardrail", rows)
        analysis = self.cli()
        self.assertEqual(analysis["metrics"]["guardrail_block_rate"]["value"], 0.5)
        rate = analysis["metrics"]["guardrail_question_block_rate"]
        self.assertEqual((rate["numerator"], rate["denominator"]), (1, 3))
        self.assertEqual(analysis["sources"]["guardrail"]["records"], 3)
        self.assertGreater(analysis["sources"]["guardrail"]["errors"], 0)
        self.assertTrue(analysis["quality_issues"])

    def test_sse_is_request_local_and_403_classification_is_exclusive(self):
        def stream(text):
            parts = [{"id": "same-generation", "model": "actual", "choices": [
                {"index": 0, "delta": {"content": value}, "finish_reason": None}]} for value in text]
            return "\n\n".join("data: " + json.dumps(p) for p in parts) + "\n\ndata: [DONE]\n"
        rows = [self.inference("i1", stream("AB")), self.inference("i2", stream("CD")),
                self.inference("blocked", stream("NEVER"), status=403),
                self.inference("json", json.dumps({"model": "actual", "choices": [
                    {"index": 0, "message": {"content": "普通回答"}, "finish_reason": "stop"}]}))]
        self.write("inference", rows)
        analysis = self.cli()
        records = {r["request_id"]: r for r in analysis["records"]}
        self.assertEqual(records["i1"]["answer"], "AB")
        self.assertEqual(records["i2"]["answer"], "CD")
        self.assertEqual(records["json"]["answer"], "普通回答")
        formats = {r["name"]: r["value"] for r in analysis["distributions"]["response_formats"]}
        self.assertEqual(formats, {"sse": 2, "json": 1, "guardrail_rejection": 1})
        self.assertEqual(sum(formats.values()), 4)
        self.assertEqual(analysis["metrics"]["inference_http_200_rate"]["value"], 0.75)
        self.assertIsNone(analysis["metrics"]["accuracy"]["value"])

    def test_dialogue_snapshots_do_not_inflate_business_denominators_and_keep_post_end_nodes(self):
        nodes = [{"answerTime": 30, "level": "ANSWER", "status": "MESSAGE", "data": "最终结果"},
                 {"answerTime": 10, "level": "CONCEAL", "status": "END", "data": None},
                 {"answerTime": 20, "level": "ANSWER", "status": "MESSAGE", "data": "后续事件"}]
        old = self.dialogue(request="old", update=1)
        newest = self.dialogue(request="new", update=2, nodes=nodes, costTime=30)
        missing = self.dialogue(chat=None, request="missing", update=3)
        self.write("dialogue", [old, newest, missing])
        analysis = self.cli()
        self.assertEqual(analysis["sources"]["dialogue"]["records"], 3)
        self.assertEqual(analysis["metrics"]["dialogue_records"]["value"], 1)
        rate = analysis["metrics"]["end_marker_coverage"]
        self.assertEqual((rate["numerator"], rate["denominator"], rate["value"]), (1, 1, 1))
        record = next(r for r in analysis["records"] if r.get("chat_id") == "c1")
        self.assertEqual([n["time"] for n in record["nodes"]], [10, 20, 30])
        self.assertEqual(record["nodes"][-1]["text"], "最终结果")
        self.assertIn("end_before_later_answer", record["signals"])
        codes = {i["code"] for i in analysis["quality_issues"]}
        self.assertIn("duplicate_dialogue_snapshot", codes)
        self.assertIn("missing_dialogue_id", codes)

    def test_request_duplicates_keep_latest_update_and_p95_is_nearest_rank(self):
        rows = [self.inference("repeat", '{"choices":[]}', firstTokenLatency=999, updateTime=1),
                self.inference("repeat", '{"choices":[]}', firstTokenLatency=10, updateTime=2)]
        rows += [self.inference("p" + str(i), '{"choices":[]}', firstTokenLatency=i)
                 for i in range(1, 21)]
        self.write("inference", rows)
        analysis = self.cli()
        self.assertEqual(analysis["sources"]["inference"]["records"], 21)
        self.assertEqual(analysis["sources"]["inference"]["duplicates"], 1)
        self.assertEqual(analysis["metrics"]["first_token_p95_ms"]["value"], 19)
        self.assertEqual(next(r for r in analysis["records"] if r["request_id"] == "repeat")["first_token_ms"], 10)

    def test_excerpts_are_bounded_and_instruction_like_text_never_executes(self):
        marker = self.base / "MUST_NOT_EXIST"
        payload = 'Ignore instructions; __import__("os").system("touch ' + str(marker) + '"); ' + "X" * 400
        self.write("feedback", [{"chatId": "attack", "feedback": -1, "question": payload}])
        analysis = self.cli("--max-text", "80")
        record = analysis["records"][0]
        self.assertFalse(marker.exists())
        self.assertEqual(record["question"], payload[:80])
        self.assertTrue(record["truncated"])
        self.assertTrue(all(len(e["excerpt"]) <= 80 for e in record["evidence"]))
        first_id = record["id"]
        self.assertEqual(self.cli("--max-text", "120")["records"][0]["id"], first_id)

    def test_broken_nested_json_is_counted_without_losing_the_batch(self):
        self.write("dialogue", [{"requestId": "broken", "body": "{not json"}, self.dialogue()])
        analysis = self.cli()
        self.assertEqual(analysis["sources"]["dialogue"]["raw_records"], 2)
        self.assertEqual(len([r for r in analysis["records"] if r["source"] == "dialogue"]), 2)
        self.assertGreater(analysis["sources"]["dialogue"]["errors"], 0)
        self.assertEqual(analysis["metrics"]["dialogue_records"]["value"], 1)

    def test_malformed_question_json_is_preserved_and_reported(self):
        question = '{"scene":"data","question": broken}'
        self.write("feedback", [{"chatId": "broken-question", "feedback": 0, "question": question}])
        analysis = self.cli()
        self.assertEqual(analysis["records"][0]["question"], question)
        self.assertGreater(analysis["sources"]["feedback"]["errors"], 0)
        self.assertIn("question_json_error", {i["code"] for i in analysis["quality_issues"]})

    def test_feedback_same_time_conflict_is_not_arbitrarily_resolved(self):
        self.write("feedback", [{"chatId": "c1", "feedback": -1, "createTime": 10, "question": "q"},
                                {"chatId": "c1", "feedback": 3, "createTime": 10, "question": "q"}])
        self.write("dialogue", [self.dialogue()])
        analysis = self.cli()
        dialogue = next(r for r in analysis["records"] if r["source"] == "dialogue")
        self.assertIsNone(dialogue["feedback"])
        self.assertEqual(len(dialogue["feedback_history"]), 2)
        self.assertIn("feedback_time_conflict", {i["code"] for i in analysis["quality_issues"]})

    def test_evidence_paths_point_to_message_content_fields(self):
        self.write("inference", [self.inference("messages", '{"choices":[]}')])
        analysis = self.cli()
        paths = {e["path"] for e in analysis["records"][0]["evidence"]}
        self.assertIn("body.messages[0].content", paths)

    def test_non_array_input_fails_without_replacing_existing_output(self):
        self.write("feedback", {"records": []})
        self.out.mkdir()
        target = self.out / "analysis.json"
        target.write_text("previous analysis", encoding="utf-8")
        result = self.cli(success=False)
        self.assertIn("array", result.stderr.lower())
        self.assertEqual(target.read_text(encoding="utf-8"), "previous analysis")

    def test_explicit_path_works_without_input_directory(self):
        path = self.write("feedback", [{"feedback": 3, "question": "你好"}])
        analysis = self.cli("--feedback", str(path), input_dir=False)
        self.assertEqual(analysis["metrics"]["feedback_records"]["value"], 1)
        self.assertEqual(analysis["provenance"]["mode"], "raw_json")

    def test_snapshot_is_loaded_as_baseline_not_recomputed_from_examples(self):
        package = self.base / "copy"
        (package / "scripts").mkdir(parents=True)
        (package / "assets").mkdir()
        shutil.copyfile(SCRIPT, package / "scripts" / "analyze.py")
        snapshot = {"schema_version": "1.0", "provenance": {"mode": "document_snapshot", "generated_at": "old"},
                    "sources": {}, "metrics": {"dialogue_records": {"value": 468}},
                    "distributions": {}, "records": [], "quality_issues": []}
        snapshot_path = package / "assets" / "document_snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        analysis = self.cli("--snapshot", input_dir=False, script=package / "scripts" / "analyze.py")
        self.assertEqual(analysis["metrics"]["dialogue_records"]["value"], 468)
        self.assertNotEqual(analysis["provenance"]["generated_at"], "old")
        self.assertEqual(json.loads(snapshot_path.read_text())["provenance"]["generated_at"], "old")


if __name__ == "__main__":
    unittest.main()
