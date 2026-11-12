"""slack_socket.LedgerTick 시험: 장부 없으면 호출 0, 있으면 sync 1회, 동시 실행 1개. 실제 Slack 미사용."""
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "hubs", "scripts"))
import ledger_sync  # noqa: E402
import slack_socket  # noqa: E402


class TickTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "l.tsv")
        self.env = mock.patch.dict(os.environ, {"LRM_LEDGER": self.path, "LRM_LEDGER_STATE": os.path.join(self.dir.name, "s.json")})
        self.env.start()
        self.tick = slack_socket.LedgerTick(object(), "U1")

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def wait(self):
        for t in threading.enumerate():
            if t is not threading.current_thread() and not t.name.startswith("MainThread"):
                t.join(2)

    def test_no_ledger_no_sync(self):
        with mock.patch.object(ledger_sync, "sync") as sync:
            self.tick.tick()
            self.wait()
        sync.assert_not_called()

    def test_ledger_present_runs_sync_with_user_id(self):
        open(self.path, "w").close()
        with mock.patch.object(ledger_sync, "sync", return_value={"status": "unchanged", "calls": 0, "replies": 0}) as sync:
            self.tick.tick()
            self.wait()
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(sync.call_args[0][0].user_id, "U1")

    def test_overlapping_tick_skipped_and_error_does_not_raise(self):
        open(self.path, "w").close()
        gate = threading.Event()
        with mock.patch.object(ledger_sync, "sync", side_effect=lambda *a, **k: gate.wait(2)) as sync:
            self.tick.tick()
            self.tick.tick()  # 앞 회차 진행 중 → 건너뜀
            gate.set()
            self.wait()
        self.assertEqual(sync.call_count, 1)
        with mock.patch.object(ledger_sync, "sync", side_effect=RuntimeError("x")):
            self.tick.tick()
            self.wait()
        self.assertFalse(self.tick._busy.locked())

    def test_thread_start_failure_releases_lock_and_does_not_raise(self):
        """리뷰 지적: Thread.start 실패가 감시 루프로 번지고 잠금이 남아 이후 회차가 전부 건너뛰어짐"""
        open(self.path, "w").close()
        with mock.patch.object(slack_socket.threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            self.tick.tick()
        self.assertFalse(self.tick._busy.locked())
        with mock.patch.object(ledger_sync, "sync", return_value={"status": "unchanged", "calls": 0, "replies": 0}) as sync:
            self.tick.tick()
            self.wait()
        self.assertEqual(sync.call_count, 1)  # 다음 회차는 정상 실행

    def test_ledger_error_logs_cause_once_until_it_clears(self):
        open(self.path, "w").close()
        err = ledger_sync.ledger.LedgerError("장부 잘못된 행 — F1: 증거 없는 완료")
        with mock.patch.object(slack_socket, "log") as log:
            with mock.patch.object(ledger_sync, "sync", side_effect=err):
                for _ in range(3):
                    self.tick.tick()
                    self.wait()
            lines = [c.args[0] for c in log.call_args_list]
        self.assertEqual(lines, ["장부 동기화 중단: 장부 잘못된 행 — F1: 증거 없는 완료"])

    def test_same_failure_logs_once_and_again_when_wait_changes(self):
        open(self.path, "w").close()
        fail = lambda wait: {"status": "failed", "calls": 1, "replies": 0, "error": "parent_search_incomplete", "wait": wait}
        wait = {"status": "deferred", "calls": 0, "replies": 0}
        results = [fail(30), wait, wait, fail(60), wait, fail(60)]
        with mock.patch.object(slack_socket, "log") as log:
            with mock.patch.object(ledger_sync, "sync", side_effect=results):
                for _ in results:
                    self.tick.tick()
                    self.wait()
            lines = [c.args[0] for c in log.call_args_list]
        self.assertEqual(len(lines), 2)
        self.assertIn("30초", lines[0])
        self.assertIn("60초", lines[1])


if __name__ == "__main__":
    unittest.main()
