"""작은 기능 빠른 길 시험: 크기별로 빼는 증거, 모아 받기 카드(board_flow.ask_review_batch)."""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "board", "lib"))
import board_flow as bf  # noqa: E402


def epic(i, size, slug="mail"):
    return {"id": i, "title": f"[LRM-{i}] 기능{i}", "issue_type": "epic", "labels": [f"size:{size}", f"feature:{slug}"],
            "description": f"- 기능 코디네이터 허브: /w/coord-feat-{slug}-hub"}


class Fake:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.code, "", "거부")


class SmallPath(unittest.TestCase):
    def setUp(self):
        self.hub = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.hub, ignore_errors=True))
        open(os.path.join(self.hub, bf.REPORT_NAME), "w").close()
        self._env = os.environ.pop(bf.REPORT_ENV, None)
        self.addCleanup(lambda: self._env is not None and os.environ.__setitem__(bf.REPORT_ENV, self._env))

    def test_skip_by_size(self):
        for size, want in ((bf.SMALL, ("review", "recording")), (bf.NORMAL, ()), (bf.LARGE, ()), ("", ())):
            self.assertEqual(bf.skip_evidence(epic("a", size)), want, size)

    def test_size_reason_is_empty_hook(self):
        self.assertEqual(bf.size_reason(epic("a", bf.SMALL)), "")

    def test_batch_lists_names_in_one_card(self):
        fake = Fake()
        r = bf.ask_review_batch([epic("a", "small"), epic("b", "small")], self.hub, "20261124", run=fake)
        self.assertTrue(r["posted"] and not r["failed"])
        self.assertEqual(len(fake.calls), 1)
        cmd = fake.calls[0]
        text = cmd[cmd.index("--text") + 1]
        self.assertIn("`기능a` (a)", text)
        self.assertIn("`기능b` (b)", text)
        self.assertTrue(cmd[cmd.index("--decision-id") + 1].startswith(bf.BATCH_HEAD))

    def test_batch_id_differs_by_members(self):
        self.assertNotEqual(bf.batch_id("20261124", ["a"]), bf.batch_id("20261124", ["a", "b"]))

    def test_dry_run_and_failures_do_not_post(self):
        fake = Fake()
        self.assertTrue(bf.ask_review_batch([epic("a", "small")], self.hub, "d", dry_run=True, run=fake)["skipped"])
        self.assertTrue(bf.ask_review_batch([{"id": "x", "labels": []}], self.hub, "d", run=fake)["failed"])
        self.assertFalse(fake.calls)
        self.assertTrue(bf.ask_review_batch([epic("a", "small")], self.hub, "d", run=Fake(3))["failed"])


if __name__ == "__main__":
    unittest.main()
