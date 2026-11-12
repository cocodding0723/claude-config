"""hub_report 시험: 기록·중복 무시·비밀 값 거부·decide 필드·동시 기록·CLI 종료 코드"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO, "hubs", "scripts", "hub_report.py")
sys.path.insert(0, os.path.dirname(SCRIPT))
import hub_report  # noqa: E402


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.box = os.path.join(self.dir.name, "box.jsonl")
        self.env = {**os.environ, "LRM_REPORT_OUTBOX": self.box}

    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", SCRIPT, *args], env=self.env, capture_output=True, text=True)

    def add(self, text="수정 완료", **kw):
        a = ["add", "--hub", kw.get("hub", "ui-layer"), "--task", kw.get("task", "C0012"), "--kind", kw.get("kind", "done"), "--text", text]
        for f in kw.get("files", []):
            a += ["--file", f]
        return self.cli(*a, *kw.get("extra", []))

    def rows(self):
        return hub_report.read_outbox(self.box)

    def test_add_writes_one_line_without_slack(self):
        r = self.add(files=["docs/a.html"])
        self.assertEqual(r.returncode, 0, r.stderr)
        row, = self.rows()
        self.assertEqual((row["hub"], row["task"], row["kind"], row["files"]), ("ui-layer", "C0012", "done", ["docs/a.html"]))
        self.assertEqual(oct(os.stat(self.box).st_mode & 0o777), "0o600")

    def test_same_content_is_duplicate_and_ignored(self):
        self.add()
        r = self.add()
        self.assertEqual(r.returncode, 0)
        self.assertIn("중복 무시", r.stdout)
        self.assertEqual(len(self.rows()), 1)
        self.add(text="다른 내용")
        self.assertEqual(len(self.rows()), 2)

    def test_secret_values_rejected_with_exit_2(self):
        for bad in ("토큰 xoxb-1234567890-abcdef", "password=hunter22secret", "ghp_" + "a" * 24):
            r = self.add(text=bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("비밀 값", r.stderr)
        r = self.add(files=["xapp-1-ABCDEFG"])
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.rows(), [])

    def test_text_over_three_lines_rejected(self):
        r = self.add(text="a\nb\nc\nd")
        self.assertEqual(r.returncode, 2)
        self.assertIn("3줄", r.stderr)

    def test_bad_hub_task_kind(self):
        self.assertEqual(self.add(hub="Bad Hub").returncode, 2)
        self.assertEqual(self.add(task="12 3").returncode, 2)
        self.assertEqual(self.add(kind="nope").returncode, 2)
        self.assertEqual(self.add(task="없음").returncode, 0)

    def test_decide_requires_card_fields(self):
        r = self.add(text="시각 정하기\n배경", kind="decide")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--options", r.stderr)
        ok = self.add(text="시각 정하기\n배경", kind="decide",
                      extra=["--options", "1=9시,2=10시", "--recommend", "1", "--decision-id", "D-0101"])
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.rows()[0]["decision_id"], "D-0101")
        self.assertEqual(self.add(extra=["--recommend", "1"]).returncode, 2)  # decide 가 아닌데 카드 필드

    def test_dry_run_writes_nothing(self):
        r = self.cli("add", "--hub", "ui", "--task", "C1", "--kind", "info", "--text", "x", "--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("[dry-run]", r.stdout)
        self.assertFalse(os.path.exists(self.box))

    def test_concurrent_adds_keep_every_line_intact(self):
        ts = [threading.Thread(target=self.add, kwargs={"text": f"보고 {i}"}) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(self.rows()), 8)
        with open(self.box, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 8)

    def test_json_output_and_list(self):
        r = self.cli("add", "--hub", "ui", "--task", "C1", "--kind", "info", "--text", "x", "--json")
        self.assertTrue(json.loads(r.stdout)["added"])
        ls = self.cli("list", "--json")
        self.assertEqual(len(json.loads(ls.stdout)), 1)

    def test_help_and_usage_error_exit_codes(self):
        self.assertEqual(self.cli("--help").returncode, 0)
        self.assertEqual(self.cli("add").returncode, 2)


if __name__ == "__main__":
    unittest.main()
