"""Behavioral tests: evidence, dataset binding, denominators and safe HTML."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class ReviewPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.analysis = {
            "schema_version": "1.0", "provenance": {"mode": "raw_json"},
            "sources": {}, "metrics": {}, "distributions": {}, "quality_issues": [],
            "records": [
                {"id": "dialogue:a", "source": "dialogue", "question": "查询设备数",
                 "answer": "", "signals": [],
                 "evidence": [{"id": "dialogue:a:e0", "path": "question", "excerpt": "查询设备数"}]},
                {"id": "feedback:b", "source": "feedback", "question": "生成报告",
                 "answer": "", "feedback": -1, "signals": ["negative_feedback"],
                 "evidence": [{"id": "feedback:b:e0", "path": "question", "excerpt": "生成报告"}]},
                {"id": "feedback:joined", "source": "feedback", "question": "查询设备数",
                 "linked_dialogue_id": "dialogue:a", "answer": "", "signals": [],
                 "evidence": [{"id": "feedback:joined:e0", "path": "question", "excerpt": "查询设备数"}]},
            ]}
        self.analysis_path = self.write("analysis.json", self.analysis)

    def write(self, name, value):
        path = self.work / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def run_script(self, script, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                              capture_output=True, text=True)

    def item(self, record_id="dialogue:a", **changes):
        value = {"record_id": record_id, "validity": "valid", "intent": ["data_query"],
                 "support": "uncertain", "badcase": "uncertain", "correctness": "uncertain",
                 "root_cause": "unknown", "reason": "只有问题，没有回答和能力依据。",
                 "evidence_refs": [record_id + ":e0"], "capability_refs": [],
                 "evaluation_refs": [], "confidence": 0.8, "suggested_action": "补齐证据"}
        value.update(changes)
        return value

    def merge(self, items, scope="selected_records", capabilities=None, digest=None, cap_digest=None):
        cap_data = capabilities or {"schema_version": "1.0", "rules": [], "evaluations": []}
        reviews = {"schema_version": "1.0", "analysis_fingerprint": digest or fingerprint(self.analysis),
                   "capabilities_fingerprint": cap_digest or fingerprint(cap_data),
                   "scope": scope, "items": items}
        review_path = self.write("reviews.json", reviews)
        out = self.work / "reviewed.json"
        args = ["--analysis", self.analysis_path, "--reviews", review_path, "--out", out]
        if capabilities is not None:
            args += ["--capabilities", self.write("capabilities.json", capabilities)]
        result = self.run_script("merge_reviews.py", *args)
        return result, out

    def test_batches_skip_feedback_already_attached_to_a_dialogue(self):
        out = self.work / "batches"
        result = self.run_script("prepare_review.py", "--analysis", self.analysis_path,
                                 "--out", out, "--batch-size", 1)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertEqual(manifest["selected_records"], 2)
        self.assertEqual(manifest["analysis_fingerprint"], fingerprint(self.analysis))
        records = []
        for name in manifest["batches"]:
            batch = json.loads((out / name).read_text())
            records.extend(x["id"] for x in batch["records"])
        self.assertEqual(records, ["dialogue:a", "feedback:b"])

    def test_uncertain_reviews_do_not_become_unsupported_or_zero_accuracy(self):
        result, out = self.merge([self.item(), self.item("feedback:b", intent=["report"], badcase="candidate")])
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(out.read_text())
        self.assertEqual(report["review"]["metrics"]["valid_questions"]["value"], 2)
        self.assertEqual(report["review"]["metrics"]["unsupported_data_queries"]["value"], 0)
        self.assertIsNone(report["review"]["metrics"]["accuracy"]["value"])
        self.assertEqual(report["review"]["coverage"]["available"], 3)
        self.assertEqual(report["review"]["coverage"]["reviewed"], 2)

    def test_unknown_evidence_and_stale_dataset_are_rejected(self):
        result, out = self.merge([self.item(evidence_refs=["made-up:e0"])])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())
        result, out = self.merge([self.item()], digest="old-dataset")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())

    def test_support_requires_a_real_matching_capability_rule(self):
        result, _ = self.merge([self.item(support="unsupported")])
        self.assertNotEqual(result.returncode, 0)
        capabilities = {"schema_version": "1.0", "rules": [
            {"id": "cap-1", "title": "跨域查询", "status": "unsupported",
             "description": "此测试明确不支持", "evidence_source": "test-fixture"}], "evaluations": []}
        result, out = self.merge([self.item(support="unsupported", capability_refs=["cap-1"])],
                                 capabilities=capabilities)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(out.read_text())["review"]["metrics"]["unsupported_data_queries"]["value"], 1)

    def test_accuracy_uses_only_record_bound_user_evaluation_evidence(self):
        capabilities = {"schema_version": "1.0", "rules": [], "evaluations": [
            {"id": "gold-a", "record_id": "dialogue:a", "kind": "human_label",
             "correctness": "incorrect", "evidence_source": "reviewer-worksheet", "note": "错误"}]}
        result, _ = self.merge([self.item(correctness="incorrect", badcase="confirmed")],
                               capabilities=capabilities)
        self.assertNotEqual(result.returncode, 0)
        result, out = self.merge([self.item(correctness="incorrect", badcase="confirmed", evaluation_refs=["gold-a"]),
                                  self.item("feedback:b", intent=["report"])], capabilities=capabilities)
        self.assertEqual(result.returncode, 0, result.stderr)
        metric = json.loads(out.read_text())["review"]["metrics"]["accuracy"]
        self.assertEqual((metric["value"], metric["numerator"], metric["denominator"]), (0.0, 0, 1))
        result, _ = self.merge([self.item("feedback:b", correctness="incorrect", evaluation_refs=["gold-a"])],
                               capabilities=capabilities)
        self.assertNotEqual(result.returncode, 0)

    def test_incomplete_scope_cannot_claim_all_questions_were_reviewed(self):
        result, _ = self.merge([self.item()], scope="all_question_records")
        self.assertNotEqual(result.returncode, 0)
        result, _ = self.merge([self.item(), self.item("feedback:b")], scope="all_question_records")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_record_reviews_are_rejected_not_counted_twice(self):
        result, _ = self.merge([self.item(), self.item()])
        self.assertNotEqual(result.returncode, 0)

    def test_changed_capability_text_cannot_reuse_old_review_with_same_rule_id(self):
        caps = {"schema_version": "1.0", "rules": [{"id": "cap-1", "title": "查询边界",
                "status": "unsupported", "description": "旧版本不支持跨园区查询",
                "evidence_source": "version-a"}], "evaluations": []}
        old_digest = fingerprint(caps)
        caps["rules"][0]["description"] = "新版本仅不支持跨国家查询"
        caps["rules"][0]["evidence_source"] = "version-b"
        result, _ = self.merge([self.item(support="unsupported", capability_refs=["cap-1"])],
                               capabilities=caps, cap_digest=old_digest)
        self.assertNotEqual(result.returncode, 0)

    def test_render_rejects_replaced_analysis_inside_a_completed_report(self):
        result, report_path = self.merge([self.item()])
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(report_path.read_text())
        report["analysis"]["records"][0]["question"] = "另一个问题"
        report["analysis"]["records"][0]["evidence"][0]["excerpt"] = "另一个问题"
        self.write("reviewed.json", report)
        out = self.work / "tampered.html"
        result = self.run_script("render_dashboard.py", "--report", report_path, "--out", out)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())

    def test_render_recomputes_metrics_from_validated_items(self):
        result, report_path = self.merge([self.item()])
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(report_path.read_text())
        report["review"]["metrics"]["accuracy"]["value"] = 1
        self.write("reviewed.json", report)
        template = self.work / "template.html"
        template.write_text('<script type="application/json">__FLYWHEEL_DATA__</script>')
        out = self.work / "recalculated.html"
        result = self.run_script("render_dashboard.py", "--report", report_path,
                                 "--template", template, "--out", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        embedded = json.loads(out.read_text().split('type="application/json">')[1].split('</script>')[0])
        self.assertIsNone(embedded["review"]["metrics"]["accuracy"]["value"])

    def test_render_escapes_html_breakout_and_preserves_json(self):
        self.analysis["records"][0]["question"] = "</script><script>globalThis.compromised=true</script>"
        self.write("analysis.json", self.analysis)
        template = self.work / "template.html"
        template.write_text('<html><script id="flywheel-data" type="application/json">__FLYWHEEL_DATA__</script></html>')
        out = self.work / "dashboard.html"
        result = self.run_script("render_dashboard.py", "--analysis", self.analysis_path,
                                 "--template", template, "--out", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        html = out.read_text()
        self.assertEqual(html.count("</script>"), 1)
        embedded = html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
        parsed = json.loads(embedded)
        self.assertEqual(parsed["analysis"]["records"][0]["question"], self.analysis["records"][0]["question"])
        self.assertEqual(parsed["review"]["status"], "not_run")


if __name__ == "__main__":
    unittest.main()
