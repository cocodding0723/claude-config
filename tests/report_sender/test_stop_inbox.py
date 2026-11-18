"""전송 중단 경고가 보낸 허브의 받은 편지함에도 남는지(채팅 경고를 허브가 못 보는 경우, 11-19 D-ORIGHOME49Q)"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts"))
import main_inbox  # noqa: E402
import report_sender  # noqa: E402


class StopInboxTest(unittest.TestCase):
    def test_stop_warning_lands_in_hub_box(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LRM_HUB_DIR": d}), \
                mock.patch.object(report_sender, "run_notify") as rn:
            rep = {"id": "060f5336487a", "hub": "feat-footprint-move", "kind": "decide", "task": "없음"}
            st = {"topics": {}, "reports": {}, "warned": {}}
            report_sender.warn_stop(SimpleNamespace(fail_limit=3), rep, st, "--options 범위 오류", True, 0)
            rn.assert_called_once()
            rows, _ = main_inbox.read_new(d, commit=False, box="feat-footprint-move")
            self.assertEqual(len(rows), 1)
            self.assertIn("060f5336487a", rows[0]["text"])


if __name__ == "__main__":
    unittest.main()
