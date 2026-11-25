"""결정 카드 접수 뒤 폴러 깨우기. launchctl 은 부르지 않음."""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hubs", "scripts"))
import hub_report  # noqa: E402

DECIDE = ["add", "--hub", "h", "--task", "없음", "--kind", "decide", "--text", "알림 시각 정하기\n배경: 시험 작업 중 고를 것이 생김\n용어: 없음",
          "--options", "1=예 => 진행함,2=아니오 => 멈춤", "--recommend", "1", "--decision-id", "D-0001", "--no-doc"]


class WakeTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.box = os.path.join(d.name, "box.jsonl")

    def wakes(self, argv):
        with mock.patch.dict(os.environ, {"LRM_REPORT_OUTBOX": self.box}), mock.patch.object(hub_report, "HUB_DIR", os.path.dirname(self.box)), \
                mock.patch.object(hub_report, "wake_poller") as wake:
            self.assertEqual(hub_report.main(argv), 0)
        return wake.call_count

    def test_new_decision_wakes_once_and_duplicate_does_not(self):
        self.assertEqual((self.wakes(DECIDE), self.wakes(DECIDE)), (1, 0))

    def test_info_and_dry_run_do_not_wake(self):
        self.assertEqual(self.wakes(["add", "--hub", "h", "--task", "없음", "--kind", "info", "--text", "진행"]), 0)
        self.assertEqual(self.wakes(DECIDE + ["--dry-run"]), 0)

    def test_only_default_outbox_reaches_launchd(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}), mock.patch.object(hub_report.subprocess, "Popen") as popen:
            hub_report.wake_poller(self.box)
            popen.assert_not_called()
            hub_report.wake_poller(os.path.join(home, ".config/lrm-hubs/hub-reports.jsonl"))
            self.assertIn("kickstart", popen.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
