import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import main_inbox as mi  # noqa: E402

CLI = os.path.join(SCRIPTS, "main_inbox.py")


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name
        self.addCleanup(self._t.cleanup)
        self.path = mi.inbox_path(self.d)

    def lines(self, path=None):
        with open(path or self.path, encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def cli(self, *args, stdin=None):
        env = dict(os.environ, LRM_HUB_DIR=self.d)
        return subprocess.run([sys.executable, "-I", CLI, *args], capture_output=True, text=True, env=env, input=stdin)


class AppendTest(Base):
    def test_line_shape_and_mode(self):
        r = mi.append("ci", "CI 실패 PR#1", "ci_watch", ref="PR#1", hub=self.d)
        self.assertEqual(r["status"], "added")
        rec = self.lines()[0]
        self.assertEqual((rec["source"], rec["kind"], rec["ref"]), ("ci_watch", "ci", "PR#1"))
        self.assertRegex(rec["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertTrue(rec["id"].startswith("ci_watch-"))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_idempotent(self):
        for _ in range(3):
            r = mi.append("stall", "응답 없는 워커", "card_actions", hub=self.d)
        self.assertEqual(r["status"], "duplicate")
        self.assertEqual(len(self.lines()), 1)
        mi.append("stall", "다른 알림", "card_actions", hub=self.d)
        self.assertEqual(len(self.lines()), 2)

    def test_dedup_window_is_recent_500(self):
        mi.append("other", "첫 줄", "t", hub=self.d)
        for i in range(mi.DEDUP_LINES):
            mi.append("other", f"채움 {i}", "t", hub=self.d)
        self.assertEqual(mi.append("other", "첫 줄", "t", hub=self.d)["status"], "added")  # 500줄 밖이면 다시 허용

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            mi.append("nope", "x", "s", hub=self.d)
        with self.assertRaises(ValueError):
            mi.append("ci", "  ", "s", hub=self.d)

    def test_oversize_spills_full_text(self):
        text = "가" * 3000  # 9000 바이트
        r = mi.append("answer", text, "slack_socket", hub=self.d)
        self.assertTrue(r["truncated"])
        with open(self.path, "rb") as f:
            raw = f.read()
        self.assertLessEqual(len(raw), mi.LINE_MAX)
        rec = json.loads(raw)
        with open(rec["full"], encoding="utf-8") as f:
            self.assertEqual(f.read(), text)  # 사용자 답 원문 보존
        self.assertEqual(os.stat(rec["full"]).st_mode & 0o777, 0o600)

    def test_concurrent_appends(self):
        with ThreadPoolExecutor(16) as ex:
            list(ex.map(lambda i: mi.append("other", f"동시 {i}", "t", hub=self.d), range(120)))
        recs = self.lines()
        self.assertEqual(len({r["id"] for r in recs}), 120)

    def test_concurrent_processes_same_alert_once(self):
        ps = [subprocess.Popen([sys.executable, "-I", CLI, "append", "--kind", "ci", "--source", "p", "같은 알림"],
                               env=dict(os.environ, LRM_HUB_DIR=self.d), stdout=subprocess.DEVNULL) for _ in range(8)]
        for p in ps:
            self.assertEqual(p.wait(), 0)
        self.assertEqual(len(self.lines()), 1)

    def test_sender_returns_false_on_failure(self):
        ok = mi.sender("s", "other", hub=self.d)
        self.assertTrue(ok("한 줄"))
        blocked = os.path.join(self.d, "file")
        open(blocked, "w").close()
        self.assertFalse(mi.sender("s", "other", hub=blocked)("x"))  # 폴더를 못 만들면 False


class RotateTest(Base):
    def test_rotation_keeps_unread_tail(self):
        mi.append("other", "읽기 전", "t", hub=self.d)
        mi.read_new(self.d)
        mi.append("other", "회전 전 안 읽음", "t", hub=self.d)
        with open(self.path, "ab") as f:
            f.write(b" " * mi.ROTATE_BYTES)  # 크기를 넘겨 다음 append 가 회전
        mi.append("other", "회전 뒤", "t", hub=self.d)
        self.assertTrue(os.path.exists(self.path + ".1"))
        recs, note = mi.read_new(self.d)
        texts = [r["text"] for r in recs if r.get("source") == "t"]
        self.assertEqual(texts, ["회전 전 안 읽음", "회전 뒤"])
        self.assertIn("회전 감지", note)


class ReadTest(Base):
    def test_offset_advances(self):
        mi.append("ci", "하나", "a", hub=self.d)
        recs, _ = mi.read_new(self.d)
        self.assertEqual([r["text"] for r in recs], ["하나"])
        self.assertEqual(mi.read_new(self.d)[0], [])
        mi.append("ci", "둘", "a", hub=self.d)
        self.assertEqual([r["text"] for r in mi.read_new(self.d)[0]], ["둘"])
        with open(mi.offset_path(self.d), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual((st["inode"], st["offset"]), (os.stat(self.path).st_ino, os.path.getsize(self.path)))
        self.assertEqual(os.stat(mi.offset_path(self.d)).st_mode & 0o777, 0o600)

    def test_peek_does_not_advance(self):
        mi.append("ci", "하나", "a", hub=self.d)
        self.assertEqual(len(mi.read_new(self.d, commit=False)[0]), 1)
        self.assertEqual(len(mi.read_new(self.d)[0]), 1)

    def test_truncation_restarts(self):
        mi.append("ci", "긴 첫 줄 " * 5, "a", hub=self.d)
        mi.read_new(self.d)
        ino = os.stat(self.path).st_ino
        with open(self.path, "w", encoding="utf-8") as f:  # 같은 inode 로 잘라 다시 씀
            f.write(json.dumps({"kind": "other", "text": "새"}) + "\n")
        self.assertEqual(os.stat(self.path).st_ino, ino)
        recs, note = mi.read_new(self.d)
        self.assertEqual([r["text"] for r in recs], ["새"])
        self.assertIn("잘림", note)

    def test_replaced_file_restarts(self):
        mi.append("ci", "옛", "a", hub=self.d)
        mi.read_new(self.d)
        os.remove(self.path)
        mi.append("ci", "새 파일", "a", hub=self.d)
        recs, _ = mi.read_new(self.d)
        self.assertEqual([r["text"] for r in recs], ["새 파일"])

    def test_partial_last_line_waits(self):
        mi.append("ci", "완전", "a", hub=self.d)
        with open(self.path, "ab") as f:
            f.write(b'{"kind":"other","te')
        self.assertEqual(len(mi.read_new(self.d)[0]), 1)
        self.assertEqual(mi.read_new(self.d)[0], [])

    def test_missing_file_is_empty(self):
        self.assertEqual(mi.read_new(self.d), ([], ""))


class CliTest(Base):
    def test_append_dry_run_writes_nothing(self):
        p = self.cli("append", "--kind", "review", "--source", "adv", "--dry-run", "--json", "지적 2건")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "added")
        self.assertFalse(os.path.exists(os.path.join(self.d, "hub-inbox")))

    def test_append_and_read_json(self):
        self.assertEqual(self.cli("append", "--kind", "intake", "--source", "intake", "--ref", "12", "-", stdin="접수 #12\n본문").returncode, 0)
        p = self.cli("read", "--since-offset", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        rec = json.loads(p.stdout.splitlines()[0])
        self.assertEqual((rec["kind"], rec["ref"], rec["text"]), ("intake", "12", "접수 #12\n본문"))
        self.assertEqual(self.cli("read", "--since-offset").stdout.strip(), "받은 편지함 새 줄 0건")

    def test_usage_error_exit_2(self):
        self.assertEqual(self.cli("append", "--kind", "bad", "--source", "x", "t").returncode, 2)
        self.assertEqual(self.cli("read").returncode, 2)

    def test_io_error_exit_1(self):
        blocked = os.path.join(self.d, "file")
        open(blocked, "w").close()
        env = dict(os.environ, LRM_HUB_DIR=blocked)
        p = subprocess.run([sys.executable, "-I", CLI, "append", "--kind", "ci", "--source", "x", "t"], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 1)

    def test_help(self):
        self.assertIn("종료 코드", self.cli("--help").stdout)


if __name__ == "__main__":
    unittest.main()
