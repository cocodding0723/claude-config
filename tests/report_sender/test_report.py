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

    def test_malformed_decision_id_rejected_at_intake(self):
        for did in ("D-bad id", "x-1", "D-", "D-" + "a" * 17, "D-한글"):
            r = self.add(text="제목\n배경", kind="decide", extra=["--options", "1=a,2=b", "--recommend", "1", "--decision-id", did])
            self.assertEqual(r.returncode, 2, did)
            self.assertIn("형식 오류: D-영문·숫자 1~16자", r.stderr)
        self.assertEqual(self.rows(), [])
        r = self.add(text="제목\n배경", kind="decide", extra=["--options", "1=a,2=b", "--recommend", "1", "--decision-id", "D-" + "a" * 16])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_secret_values_rejected_with_exit_2(self):
        for bad in ("토큰 " + "xox" + "b-1234567890-abcdef", "pass" + "word=hunter22secret", "gh" + "p_" + "a" * 24):
            r = self.add(text=bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("비밀 값", r.stderr)
        r = self.add(files=["xa" + "pp-1-ABCDEFG"])
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

    # --- Codex 리뷰 회귀(1·2·5) ---
    def test_truncated_tail_is_quarantined_and_new_report_readable(self):
        with open(self.box, "w") as f:
            f.write('{"id":"cut')  # 개행 없는 잘린 JSON
        r = self.add()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.rows()), 1)
        with open(self.box + ".corrupt", encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"id":"cut\n')
        with open(self.box, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 1)

    def test_whole_json_tail_without_newline_keeps_record(self):
        self.add()
        with open(self.box, encoding="utf-8") as f:
            line = f.read().rstrip("\n")
        with open(self.box, "w", encoding="utf-8") as f:
            f.write(line)  # 온전한 줄인데 개행만 없음
        self.assertEqual(self.add(text="둘째").returncode, 0)
        self.assertEqual(len(self.rows()), 2)
        self.assertFalse(os.path.exists(self.box + ".corrupt"))

    def test_unrecoverable_tail_fails_with_nonzero_exit_and_no_write(self):
        with open(self.box, "w") as f:
            f.write('{"id":"cut')
        os.mkdir(self.box + ".corrupt")  # 격리 파일을 못 쓰는 상황
        r = self.add()
        self.assertEqual(r.returncode, 1)
        with open(self.box) as f:
            self.assertEqual(f.read(), '{"id":"cut')

    def test_secret_in_hub_task_kind_rejected_without_echo(self):
        bad = "xox" + "b-review-sentinel"
        for kw in ({"hub": bad}, {"task": bad}, {"kind": bad}):
            r = self.add(**kw)
            self.assertEqual(r.returncode, 2, kw)
            self.assertNotIn(bad, r.stderr + r.stdout)  # 형식 오류·argparse 메시지가 입력을 되비추지 않음
        self.assertEqual(self.rows(), [])
        self.assertFalse(os.path.exists(self.box))

    def test_secret_in_decide_fields_rejected(self):
        bad = "xox" + "b-review-sentinel"
        r = self.add(text="제목\n배경", kind="decide", extra=["--options", f"1={bad},2=b", "--recommend", "1", "--decision-id", "D-1"])
        self.assertEqual(r.returncode, 2)

    def test_invalid_records_are_isolated_not_fatal(self):
        good = hub_report.make_report("ui", "C1", "done", "정상", [])
        good = {"id": "g1", "at": 1, **good}
        lines = [json.dumps({"id": "bad", "hub": "ui", "kind": "done"}),  # task 없음
                 json.dumps({**good, "id": "b2", "files": "x"}), json.dumps([1]), "not json", json.dumps(good)]
        with open(self.box, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        bad = []
        rows = hub_report.read_outbox(self.box, bad)
        self.assertEqual([r["id"] for r in rows], ["g1"])
        self.assertEqual(len(bad), 4)

    def test_record_without_files_key_is_invalid(self):
        rec = {"id": "nf", "at": 1, "hub": "ui", "task": "C1", "kind": "done", "text": "x"}  # files 키 없음
        self.assertFalse(hub_report.valid_record(rec))
        self.assertTrue(hub_report.valid_record({**rec, "files": []}))
        with open(self.box, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        bad = []
        self.assertEqual(hub_report.read_outbox(self.box, bad), [])
        self.assertEqual(len(bad), 1)

    def test_help_and_usage_error_exit_codes(self):
        self.assertEqual(self.cli("--help").returncode, 0)
        self.assertEqual(self.cli("add").returncode, 2)


if __name__ == "__main__":
    unittest.main()
