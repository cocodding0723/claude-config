import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hubs/scripts"))
import board_mirror as bm


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = dict(os.environ)
        os.environ.update(LRM_HUB_DIR=str(self.home), LRM_REPORT_OUTBOX=str(self.home / "queue"))
        self.policy = ROOT / "hubs/board_mirror.tsv"
        self.root = dict(id="lrm-test", title="시험 기능", issue_type="epic", status="open",
                         labels=["flow:v1", "feature:test", "slack-thread:123.456"])
        self.child = dict(id="lrm-test.1", parent="lrm-test", status="closed", labels=["ws:/tmp/test"])
        self.rows = {"lrm-test": [], "lrm-test.1": []}
        self.cards = [self.root, self.child]

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        self.temp.cleanup()

    def mirror(self, dry=False):
        mirror = bm.Mirror(self.policy, dry)
        mirror.bd = lambda *a: self.cards if a[0] == "list" else self.rows[a[1]]
        return mirror

    def run_mirror(self, dry=False):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.mirror(dry).run()
        return out.getvalue()

    def add(self, text, cid="lrm-test.1", author="worker-test"):
        n = len(self.rows[cid]) + 1
        self.rows[cid].append(dict(id=f"id-{n}", created_at=f"2026-11-21T00:00:{n:02d}Z",
                                  text=text, author=author))

    def queue(self):
        path = self.home / "queue"
        return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []

    def test_baseline_batch_duplicate_and_format(self):
        self.add("지난 기록")
        self.run_mirror()
        self.assertEqual([], self.queue())
        self.add("동작 수정 완료 a12bc34 worker-test")
        self.add("관련 시험 통과")
        self.run_mirror()
        q = self.queue()
        self.assertEqual(1, len(q))
        rep = q[0]
        self.assertEqual(["id-2", "id-3"], rep["mirror_ids"])
        self.assertEqual(("feat-test", "lrm-test.1", "/tmp/test"), (rep["hub"], rep["card"], rep["ws"]))
        self.assertIn("관련 시험 통과", rep["text"])
        self.assertNotIn("worker-test", rep["text"].split("참고:")[0])
        self.assertTrue(bm.report.valid_record(rep))
        bm.report.make_report(rep["hub"], rep["task"], rep["kind"], rep["text"], [], card=rep["card"])
        self.run_mirror()
        self.assertEqual(q, self.queue())
        state = self.home / "board-mirror.state.json"
        data = json.loads(state.read_text()); data["lrm-test.1"] = "id-1"
        data["_boundary"]["lrm-test.1"] = ["id-1"]
        state.write_text(json.dumps(data))
        self.run_mirror()
        self.assertEqual(q, self.queue())

    def test_filters_manual_and_long_paste(self):
        self.run_mirror()
        for text in ["Slack 결정 답: 승인", "Slack 스레드 123.456 연결. 답", "Slack 답글: 확인",
                     "Slack 피드백(원문): 확인", "Slack 결정 질문: 확인"]:
            self.add(text)
        self.run_mirror()
        self.assertEqual([], self.queue())
        self.add("지시 #1: 브리프\n" + "원문" * 200, author="사람")
        self.run_mirror()
        self.assertIn("원문 411자", self.queue()[0]["text"])
        self.assertNotIn("원문원문", self.queue()[0]["text"])

    def test_existing_report_and_identical_new_ids(self):
        self.run_mirror()
        rep = bm.report.make_report("feat-test", "없음", "info", "이미 보낸 보고", [], card="lrm-test.1")
        bm.report.add(str(self.home / "queue"), rep, 0, False)
        self.add("이미 보낸 보고")
        self.run_mirror()
        self.assertEqual(1, len(self.queue()))
        for _ in range(2):
            self.add("같은 내용의 새 기록")
            self.run_mirror()
        self.assertEqual(3, len(self.queue()))

    def test_overflow_attachment_and_dry_no_write(self):
        self.run_mirror()
        for _ in range(8):
            self.add("설명" * 80)
        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        self.assertIn("새 기록 8건", self.run_mirror(True))
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()})
        self.run_mirror()
        rep = self.queue()[0]
        self.assertEqual(1, len(self.queue()))
        self.assertEqual(8, Path(rep["files"][0]).read_text().count("설명" * 80))
        self.assertTrue(rep["staged"])

    def test_bd_failure_and_dry_bootstrap(self):
        self.assertIn("baseline", self.run_mirror(True))
        self.assertEqual([], list(self.home.iterdir()))
        env = dict(os.environ, BOARD_BD="/usr/bin/false")
        p = subprocess.run([sys.executable, "-B", str(ROOT / "hubs/scripts/board_mirror.py"), "--once"],
                           env=env, capture_output=True)
        self.assertEqual(1, p.returncode)
        self.assertFalse((self.home / "board-mirror.state.json").exists())

    def test_uuid_order_with_same_second(self):
        self.add("기존 기록")
        self.rows["lrm-test.1"][0]["id"] = "z-uuid"
        self.run_mirror()
        self.add("같은 초에 추가한 기록")
        self.rows["lrm-test.1"][-1].update(id="a-uuid", created_at="2026-11-21T00:00:01Z")
        self.run_mirror()
        self.assertEqual(["a-uuid"], self.queue()[0]["mirror_ids"])
        self.run_mirror()
        self.assertEqual(1, len(self.queue()))

    def test_cli_fake_bd(self):
        fixture = self.home / "fixture"
        fake = self.home / "bd"
        fake.write_text("#!" + sys.executable + "\nimport json,sys\n"
                        + "data=json.load(open(" + repr(str(fixture)) + "))\n"
                        + "print(json.dumps(data['cards'] if sys.argv[1]=='list' else data['rows'][sys.argv[2]]))\n")
        fake.chmod(0o755)
        env = dict(os.environ, BOARD_BD=str(fake))
        cmd = [sys.executable, "-B", str(ROOT / "hubs/scripts/board_mirror.py"), "--once"]
        for text in [None, "실제 CLI 새 기록", None]:
            if text:
                self.add(text)
            fixture.write_text(json.dumps(dict(cards=self.cards, rows=self.rows)))
            p = subprocess.run(cmd, env=env, capture_output=True, text=True)
            self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual(1, len(self.queue()))


if __name__ == "__main__":
    unittest.main()
