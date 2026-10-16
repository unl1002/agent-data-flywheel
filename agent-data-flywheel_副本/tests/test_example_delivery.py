"""The shipped preview must exercise the current UI and its actual data lineage."""
import base64
import gzip
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DATA = re.compile(r'(<script id="flywheel-data" type="application/json">)(.*?)(</script>)', re.S)


def read_preview(path):
    html = path.read_text(encoding="utf-8")
    match = DATA.search(html)
    if match is None:
        raise AssertionError("示例页缺少可读取的内嵌分析包")
    bundle = json.loads(match[2])
    if bundle.get("encoding") == "gzip-base64":
        bundle = json.loads(gzip.decompress(base64.b64decode(bundle["data"])))
    shell = html[:match.start(2)] + "__FLYWHEEL_DATA__" + html[match.end(2):]
    return shell, bundle


class ExampleDeliveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell, cls.bundle = read_preview(ROOT / "examples" / "dashboard.html")

    def test_default_preview_uses_the_current_template(self):
        self.assertEqual(self.shell, (ROOT / "assets" / "dashboard.html").read_text(encoding="utf-8"),
                         "默认示例仍含旧界面，应使用当前模板重新渲染")

    def test_default_preview_contains_reviewed_real_data_and_support_members(self):
        analysis, review = self.bundle["analysis"], self.bundle["review"]
        self.assertEqual(analysis["provenance"]["mode"], "raw_json")
        self.assertEqual(review["status"], "completed")
        self.assertEqual(review["scope"], "all_question_records")
        self.assertTrue(analysis["source_rows"])
        metric = review["primary_metrics"]["response_support_rate"]
        trace = review["lineage"][metric["trace_id"]]
        self.assertGreater(metric["denominator"], 0)
        self.assertEqual(len(trace["numerator_refs"]), metric["numerator"])
        self.assertEqual(len(trace["denominator_refs"]), metric["denominator"])
        self.assertAlmostEqual(metric["value"], metric["numerator"] / metric["denominator"])

    def test_document_examples_are_separate_and_use_the_current_template(self):
        folder = ROOT / "examples" / "document_snapshot"
        shell, bundle = read_preview(folder / "dashboard.html")
        self.assertEqual(shell, self.shell)
        self.assertEqual(bundle["analysis"]["provenance"]["mode"], "document_snapshot")
        self.assertEqual(bundle["review"]["scope"], "document_examples")
        self.assertTrue((folder / "analysis.json").is_file())
        self.assertTrue((folder / "ai_reviews.json").is_file())


if __name__ == "__main__":
    unittest.main()
