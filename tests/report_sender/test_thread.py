"""hub_report --thread·--now 시험: 지정 스레드 답글·진행 줄 없음·ts 검증·채널 지정 거부·즉시 전송. 가짜 전송 스크립트만 씀."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import hub_report  # noqa: E402
import report_sender  # noqa: E402

FAKE = os.path.join(HERE, "fake_notify.py")
THREAD = "1730790000.123456"
PARENT = "1699999999.000001"


class ThreadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = self.p = lambda n: os.path.join(self.dir.name, n)  # noqa: E731
        with open(p("sync.json"), "w") as f:
            json.dump({"ts": PARENT, "channel": "D1ABCDEFG"}, f)
        self.env = {**os.environ, "LRM_HUB_DIR": self.dir.name, "LRM_REPORT_OUTBOX": p("box.jsonl"), "LRM_REPORT_STATE": p("state.json"),
                    "LRM_REPORT_NOTIFY": FAKE, "LRM_LEDGER": p("ledger.tsv"), "LRM_LEDGER_STATE": p("sync.json"),
                    "LRM_REPORT_SENT": p("sent.jsonl"), "LRM_REPORT_LOG": p("log.txt"), "FAKE_DIR": self.dir.name}

    def cli(self, *args, script="hub_report.py"):
        return subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, script), *args], env=self.env, capture_output=True, text=True)

    def add(self, *extra, kind="warn", text="CI 실패 원인: 시험 X"):
        return self.cli("add", "--hub", "ci-hub", "--task", "없음", "--kind", kind, "--text", text, *extra)

    def calls(self):
        try:
            with open(self.p("calls.jsonl"), encoding="utf-8") as f:
                return [json.loads(ln)["args"] for ln in f]
        except FileNotFoundError:
            return []

    def rows(self):
        return hub_report.read_outbox(self.p("box.jsonl"))

    def test_thread_report_replies_in_given_thread_without_progress_line(self):
        self.assertEqual(self.add("--thread", THREAD).returncode, 0)
        r = self.cli("--once", script="report_sender.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        call, = self.calls()  # 주제 시작·진행 줄 없이 1건
        self.assertEqual(call[call.index("--thread") + 1], THREAD)
        self.assertEqual(call[call.index("--kind") + 1], "warn")
        self.assertIn("CI 실패 원인", call[-1])
        self.assertNotIn(PARENT, " ".join(call))
        self.assertIn("진행 줄 없음(스레드 지정)", r.stdout)
        with open(self.p("state.json")) as f:
            self.assertEqual(json.load(f)["topics"], {})

    def test_info_reply_to_goes_to_given_thread(self):
        """Slack 에서 온 질문의 답: --reply-to 로 info 를 그 스레드에 답글(채널 새 글·진행 줄 없음)"""
        self.assertEqual(self.add("--reply-to", THREAD, kind="info", text="질문 답: 캐시 문제").returncode, 0)
        r = self.cli("--once", script="report_sender.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        call, = self.calls()
        self.assertEqual(call[call.index("--thread") + 1], THREAD)
        self.assertEqual(call[call.index("--kind") + 1], "progress")
        self.assertIn("질문 답", call[-1])

    def test_done_follow_up_goes_to_same_thread(self):
        self.add("--thread", THREAD)
        self.add("--thread", THREAD, kind="done", text="수정 push 완료")
        self.cli("--once", script="report_sender.py")
        kinds = [(c[c.index("--kind") + 1], c[c.index("--thread") + 1]) for c in self.calls()]
        self.assertEqual(kinds, [("warn", THREAD), ("done", THREAD)])

    def test_bad_ts_rejected(self):
        for bad in ("abc", "1730790000", "1730790000.", "1730790000.12x", "17307 90000.1", "1.2;rm"):
            r = self.add("--thread", bad)
            self.assertEqual(r.returncode, 2, bad)
        self.assertEqual(self.rows(), [])

    def test_thread_only_for_info_done_and_warn(self):
        self.assertEqual(self.add("--thread", THREAD, kind="block").returncode, 2)
        r = self.add("--thread", THREAD, kind="decide", text="제목\n배경")
        self.assertEqual(r.returncode, 2)

    def test_channel_option_is_not_supported(self):
        for ch in ("D1ABCDEFG", "C1OTHERCH1"):  # 알림 스크립트는 봇 DM 에만 게시하므로 채널 지정 자체를 받지 않음
            r = self.add("--thread", THREAD, "--channel", ch)
            self.assertEqual(r.returncode, 2)
        self.assertEqual(self.rows(), [])
        self.assertNotIn("--channel", self.cli("add", "--help").stdout)
        self.env["LRM_REPORT_CHANNELS"] = "C1OTHERCH1"  # 옛 허용 목록 변수도 효과 없음
        self.assertEqual(self.add("--thread", THREAD, "--channel", "C1OTHERCH1").returncode, 2)

    def test_sender_isolates_legacy_record_with_channel_and_continues(self):
        rep = hub_report.make_report("ci-hub", "없음", "warn", "x", [], thread=THREAD)
        rep["channel"] = "C1EVILCH01"  # 채널 지원 제거 전에 기록된 레코드
        hub_report.add(self.p("box.jsonl"), rep, 1.0, False)
        self.add("--thread", THREAD, text="정상")
        res = json.loads(self.cli("--once", "--json", script="report_sender.py").stdout)
        self.assertEqual((res["bad"], res["sent"]), (1, 1))
        self.assertEqual(len(self.calls()), 1)
        self.assertNotIn("C1EVILCH01", json.dumps(self.calls()))

    def test_secret_in_thread_rejected(self):
        self.assertEqual(self.add("--thread", "xox" + "b-1234567890-abc").returncode, 2)

    def test_now_sends_right_after_recording(self):
        r = self.add("--thread", THREAD, "--now")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("즉시 전송: 전송 1", r.stdout)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.cli("--once", script="report_sender.py").returncode, 0)
        self.assertEqual(len(self.calls()), 1)  # 폴러 회차가 같은 보고를 다시 올리지 않음

    def test_now_when_sender_busy_leaves_report_for_next_round(self):
        with report_sender.exclusive(self.p("state.json")) as got:
            self.assertTrue(got)
            r = self.add("--thread", THREAD, "--now")
        self.assertEqual(r.returncode, 0)
        self.assertIn("다음 폴러 회차", r.stdout)
        self.assertEqual(self.calls(), [])
        self.cli("--once", script="report_sender.py")
        self.assertEqual(len(self.calls()), 1)

    def test_now_send_failure_keeps_record_and_exit_zero(self):
        with open(self.p("fail.all"), "w") as f:
            f.write("1")
        r = self.add("--thread", THREAD, "--now")
        self.assertEqual(r.returncode, 0)
        self.assertIn("재시도", r.stdout)
        self.assertEqual(len(self.rows()), 1)

    def test_now_with_dry_run_does_not_send(self):
        r = self.add("--thread", THREAD, "--now", "--dry-run")
        self.assertEqual((r.returncode, self.calls()), (0, []))

    def test_same_thread_report_is_deduplicated_but_other_thread_is_not(self):
        self.add("--thread", THREAD)
        self.assertIn("중복 무시", self.add("--thread", THREAD).stdout)
        self.add("--thread", "1730790000.999999")
        self.assertEqual(len(self.rows()), 2)


if __name__ == "__main__":
    unittest.main()
