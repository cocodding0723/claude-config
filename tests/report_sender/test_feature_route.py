"""기능 허브 보고를 기능 스레드 하나로 모으기, 본문 티켓·커밋 번호를 참고 줄로 옮기기 시험."""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hubs", "scripts"))
import hub_report  # noqa: E402
import report_sender  # noqa: E402

EPIC_TS = "1795001319.536929"
EPIC_ROWS = [{"id": "lrm-1yd", "labels": ["feature:lrm-677", f"slack-thread:{EPIC_TS}"]}]


class MoveRefsTest(unittest.TestCase):
    def test_body_numbers_move_into_single_ref_line(self):
        out = hub_report.bullet_body("LRM-673 완료 보고\n수치 반영(c4f817b85698)\n참고: 허브 x")
        lines = out.splitlines()
        self.assertEqual(lines[0], "완료 보고")
        self.assertIn("- 수치 반영", lines)
        self.assertEqual(lines[-1], "참고: 허브 x · LRM-673 · c4f817b85698")
        self.assertEqual(sum(ln.startswith("참고:") for ln in lines), 1)

    def test_code_and_url_numbers_stay(self):
        out = hub_report.bullet_items(["링크 https://x/browse/LRM-1", "`LRM-9` 코드"])
        self.assertEqual(out[:2], ["- 링크 https://x/browse/LRM-1", "- `LRM-9` 코드"])
        self.assertFalse(any(ln.startswith("참고:") for ln in out))


class FeatureThreadTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.conf = mock.Mock(state=os.path.join(d.name, "state.json"), log=os.path.join(d.name, "log.txt"))
        self.st = {"topics": {}}

    def route(self, rep, rows):
        with mock.patch.object(report_sender, "bd_json", return_value=rows), \
                mock.patch.object(report_sender, "run_notify", side_effect=AssertionError("새 스레드를 열면 안 됨")):
            report_sender.open_topic(self.conf, rep, self.st, 0)

    def test_ticket_and_card_topics_share_feature_thread(self):
        for rep in ({"hub": "feat-lrm-677", "task": "LRM-677"}, {"hub": "feat-lrm-677", "task": "없음", "card": "lrm-1yd.1"}):
            self.route(rep, EPIC_ROWS)
        self.assertEqual({t["ts"] for t in self.st["topics"].values()}, {EPIC_TS})
        self.assertEqual(set(self.st["topics"]), {"LRM-677", "card:lrm-1yd.1"})

    def test_non_feature_hub_is_not_routed(self):
        self.assertEqual(report_sender.feature_thread(self.conf, {"hub": "ci-hub"}), ("", ""))

    def test_feature_without_thread_yet_is_not_routed(self):
        with mock.patch.object(report_sender, "bd_json", return_value=[{"id": "lrm-1yd", "labels": ["feature:lrm-677"]}]):
            self.assertEqual(report_sender.feature_thread(self.conf, {"hub": "feat-lrm-677"}), ("", ""))


if __name__ == "__main__":
    unittest.main()
