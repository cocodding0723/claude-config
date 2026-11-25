"""느린 bd 가 전송 예산을 다 쓰지 않게: 시간 초과·조회 시간 소진이면 내보내기 파일로 읽음."""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hubs", "scripts"))
import report_sender  # noqa: E402

ROWS = [{"_type": "issue", "id": "lrm-a", "issue_type": "epic", "labels": ["feature:x"], "title": "기능"},
        {"_type": "issue", "id": "lrm-a.1", "issue_type": "task", "labels": ["feature:x"], "status": "closed",
         "dependencies": [{"type": "parent-child", "depends_on_id": "lrm-a"}]},
        {"_type": "issue", "id": "lrm-b", "issue_type": "task", "dependencies": [{"type": "blocks", "depends_on_id": "lrm-a"}]},
        {"_type": "memory", "id": "m"}]


class BdBudgetTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name
        self.calls = os.path.join(d.name, "calls")
        self.conf = report_sender.Conf(board=d.name, bd=os.path.join(d.name, "bd"), log=os.path.join(d.name, "log"))

    def bd(self, body):
        with open(self.conf.bd, "w") as f:
            f.write(f"#!/bin/sh\necho x >> '{self.calls}'\n{body}\n")
        os.chmod(self.conf.bd, 0o755)

    def export(self, text=None):
        os.makedirs(os.path.join(self.dir, ".beads"))
        with open(os.path.join(self.dir, ".beads", "issues.jsonl"), "w", encoding="utf-8") as f:
            f.write(text or "".join(json.dumps(r) + "\n" for r in ROWS))

    def ids(self, *args):
        return [r["id"] for r in report_sender.bd_json(self.conf, *args)]

    def test_timeout_falls_back_and_later_lookups_do_not_wait(self):
        self.bd("exec sleep 20")
        self.export()
        self.conf.bd_left = 1
        start = time.monotonic()
        self.assertEqual(self.ids("show", "lrm-a"), ["lrm-a"])
        self.assertEqual(self.ids("list", "--parent", "lrm-a", "--all", "-n", "0", "--brief"), ["lrm-a.1"])
        self.assertEqual(self.ids("list", "-t", "epic", "-l", "feature:x", "--all", "--limit", "0"), ["lrm-a"])
        self.assertLess(time.monotonic() - start, 5)
        with open(self.calls) as f:
            self.assertEqual(len(f.readlines()), 1)

    def test_slow_successes_add_up(self):
        self.bd("sleep 0.6; echo '[]'")
        self.export()
        self.conf.bd_left = 1.5
        for _ in range(4):
            report_sender.bd_json(self.conf, "show", "lrm-a")
        with open(self.calls) as f:
            self.assertEqual(len(f.readlines()), 1)

    def test_bd_error_is_not_replaced_by_export(self):
        self.bd("exit 1")
        self.export()
        self.assertIsNone(report_sender.bd_json(self.conf, "show", "lrm-a"))

    def test_no_export_or_broken_export_is_none(self):
        self.conf.bd_left = 0
        self.assertIsNone(report_sender.bd_json(self.conf, "show", "lrm-a"))
        self.export("{깨짐\n")
        self.assertIsNone(report_sender.bd_json(self.conf, "show", "lrm-a"))


if __name__ == "__main__":
    unittest.main()
