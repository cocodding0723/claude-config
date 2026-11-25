"""coord_watch 판정 표, 대화 시각 읽기, 한 회차 흐름 시험."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix="cc-coordwatch-")
os.environ["LRM_HUB_DIR"] = TMP
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts"))
import coord_watch as cw  # noqa: E402

NOW = 1_795_600_000.0
L = cw.Limits()


def epic(stage="build", **kw):
    return {"id": "t-e", "title": "[LRM-1] 기능", "status": "open", "issue_type": "epic", "labels": ["flow:v1", "feature:x", f"stage:{stage}"], **kw}


def seen(mins=30, said=100, working=False):
    return cw.Seen(NOW - mins * 60, said, working)


def rec(wakes=0, said=100, ago=30, **kw):
    return {"said": said, "wakes": wakes, "at": NOW - ago * 60, **kw}


class JudgeTest(unittest.TestCase):
    # (이름, 카드, 세션, 사용자 답 대기, 작업자, 기록, 기대)
    CASES = [
        ("20분 넘게 쉬면 깨움", epic(), seen(), False, False, {}, "wake"),
        ("20분이 안 됐으면 둠", epic(), seen(19), False, False, {}, "skip"),
        ("하루 넘게 쉰 창은 끝난 창으로 봄", epic(), seen(1441), False, False, {}, "skip"),
        ("사용자 답을 기다리면 정상 대기", epic(), seen(), True, False, {}, "skip"),
        ("작업자가 일하면 정상 대기", epic(), seen(), False, True, {}, "skip"),
        ("백그라운드 명령이 돌면 작업 중", epic(), seen(working=True), False, False, {}, "skip"),
        ("접수는 사용자 답 차례", epic("intake"), seen(), False, False, {}, "skip"),
        ("검수 대기는 사용자 차례", epic("review"), seen(), False, False, {}, "skip"),
        ("닫힌 기능", epic(status="closed"), seen(), False, False, {}, "skip"),
        ("모아 검수·병합을 기다리는 작은 기능(11-25 LRM-835)", epic(labels=["flow:v1", "stage:build", "review:batched"]), seen(), False, False, {}, "skip"),
        ("세션 기록이 없으면 판단하지 않음", epic(), cw.Seen(), False, False, {}, "skip"),
        ("깨운 지 20분이 안 됐으면 둠", epic(), seen(), False, False, rec(1, ago=10), "skip"),
        ("한 번 깨운 뒤 또 멈추면 한 번 더", epic(), seen(), False, False, rec(1), "wake"),
        ("두 번 깨워도 멈추면 경고", epic(), seen(), False, False, rec(2), "warn"),
        ("경고는 한 번만", epic(), seen(), False, False, rec(2, warned=True), "skip"),
        ("경고 전송이 실패했으면 다시 시도", epic(), seen(), False, False, rec(2, warned=False), "warn"),
        ("새 입력 뒤 다시 멈추면 처음부터 셈", epic(), seen(said=200), False, False, rec(2, warned=True), "wake"),
    ]

    def test_cases(self):
        for name, e, s, wait, worker, r, want in self.CASES:
            with self.subTest(name):
                self.assertEqual(cw.judge(e, s, wait, worker, r, L, NOW)[0], want)


def row(kind, text, at, **kw):
    return {"type": kind, "timestamp": dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat(), "message": {"content": text}, **kw}


class LastTalkTest(unittest.TestCase):
    def read(self, *rows):
        p = os.path.join(TMP, "s.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows) + "깨진 줄\n")
        return cw.last_talk(p)

    def test_idle_compaction_and_system_lines_are_not_talk(self):
        got = self.read(row("user", "이어서 해", 100), row("assistant", [{"type": "text", "text": "네"}], 110),
                        row("user", "This session is being continued from", 900), row("user", "<command-name>/clear", 950), row("user", "x", 990, isMeta=True))
        self.assertEqual(got, (110, 100))

    def test_wake_line_and_own_notifications_do_not_start_a_new_pause(self):
        got = self.read(row("user", "이어서 해", 100), row("user", cw.WAKE_TEXT.format(mins=20, epic="t-e"), 200),
                        row("assistant", [{"type": "text", "text": "확인"}], 210), row("user", "<task-notification> 끝", 300))
        self.assertEqual(got, (300, 100))

    def test_wake_line_is_not_a_user_instruction(self):
        self.assertFalse(cw.board_said.is_said(cw.WAKE_TEXT.format(mins=20, epic="t-e")))
        self.assertFalse(cw.flow.typed_plan_ok(cw.WAKE_TEXT.format(mins=20, epic="t-e")))


class CheckTest(unittest.TestCase):
    def run_check(self, epics, terms=1, result="sent", workers=frozenset(), waiting=frozenset(), dry=False, now=NOW, limits=L):
        sends, warns = [], []
        def deliver(ws, text):
            sends.append(text)
            return (result, 1, [], 1)
        with mock.patch.multiple(cw.card_actions, deliver_ws=deliver, ws_terminals=lambda ws, writable_only=False: [{"handle": str(i)} for i in range(terms)]), \
                mock.patch.object(cw.board_cards, "bd_json", lambda *a: epics), mock.patch.object(cw, "busy_hubs", lambda n: workers), \
                mock.patch.object(cw, "waiting_hubs", lambda n: set(waiting)), mock.patch.object(cw, "live_status", dict), \
                mock.patch.object(cw, "seen_of", lambda ws, st: cw.Seen(NOW - 1800, 100, False)), \
                mock.patch.object(cw, "warn", lambda e, ws, what: warns.append(what) or True):
            rc, res = cw.check(limits, dry, now)
        return rc, res, sends, warns

    def setUp(self):
        for p in (cw.STATE, cw.EPICS):
            if os.path.exists(p):
                os.unlink(p)
        self.ws = tempfile.mkdtemp(prefix="coord-", dir=TMP)

    def feature(self, i=0):
        return {**epic(), "id": f"t-{i}", "description": f"- 기능 코디네이터 허브: {self.ws}\n", "labels": ["flow:v1", f"feature:x{i}", "stage:build"]}

    def test_two_wakes_then_one_warning_then_silence(self):
        f, out = [self.feature()], []
        for step in range(5):
            _, _, sends, warns = self.run_check(f, now=NOW + step * 1300)
            out.append((len(sends), len(warns)))
        self.assertEqual(out, [(1, 0), (1, 0), (0, 1), (0, 0), (0, 0)])

    def test_blocked_input_is_not_counted_and_is_retried(self):
        self.run_check([self.feature()], result=cw.card_actions.BLOCKED)
        self.assertFalse(os.path.exists(cw.STATE))
        self.assertEqual(len(self.run_check([self.feature()])[2]), 1)

    def test_two_windows_are_warned_not_woken(self):
        _, _, sends, warns = self.run_check([self.feature()], terms=2)
        self.assertEqual((len(sends), len(warns)), (0, 1))
        self.assertEqual(self.run_check([self.feature()], terms=2, now=NOW + 1300)[3], [])

    def test_wakes_per_run_are_capped(self):
        self.assertEqual(len(self.run_check([self.feature(i) for i in range(5)])[2]), L.per_run)

    def test_waiting_and_worker_hubs_are_left_alone(self):
        self.assertEqual(self.run_check([self.feature()], waiting={"t-0"})[2], [])
        self.assertEqual(self.run_check([self.feature()], waiting={"feat-x0"})[2], [])
        self.assertEqual(self.run_check([self.feature()], workers={"feat-x0"})[2], [])

    def test_unknown_workers_means_no_wake(self):
        rc, _, sends, _ = self.run_check([self.feature()], workers=None)
        self.assertEqual((rc, sends), (1, []))

    def test_board_failure_is_reported_without_waking(self):
        def broken(*a):
            raise cw.board_cards.Fail(3, "bd 실패")
        with mock.patch.object(cw.board_cards, "bd_json", broken):
            self.assertEqual(cw.check(L, False, NOW)[0], 1)

    def test_slow_board_uses_the_last_good_list_for_ten_minutes(self):
        self.run_check([self.feature()], waiting={"t-0"})  # 성공한 조회를 남김
        def broken(*a):
            raise cw.board_cards.Fail(3, "시간 초과")
        with mock.patch.object(cw.board_cards, "bd_json", broken):
            self.assertEqual(len(cw.open_epics(L, NOW + 590, True)), 1)
            with self.assertRaises(cw.board_cards.Fail):
                cw.open_epics(L, NOW + 610, True)

    def test_bd_is_found_outside_a_short_path(self):
        # 11-25: 수신기(launchd)의 짧은 PATH 에서 bd 를 못 찾아 첫 회차가 실패함
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}), mock.patch.object(cw.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="[]", stderr="")
            with mock.patch.object(cw, "busy_hubs", lambda n: set()), mock.patch.object(cw, "waiting_hubs", lambda n: set()):
                self.assertEqual(cw.check(L, False, NOW)[0], 0)
            self.assertEqual(run.call_args[0][0][0], cw.board_cards.find_bd())

    def test_quiet_hours_and_dry_run_send_nothing(self):
        hour = dt.datetime.fromtimestamp(NOW).hour
        quiet = cw.Limits(quiet=(hour, hour + 1))
        self.assertEqual(self.run_check([self.feature()], limits=quiet)[2], [])
        _, res, sends, _ = self.run_check([self.feature()], dry=True, limits=quiet)
        self.assertEqual((sends, res["rows"][0]["act"], os.path.exists(cw.STATE)), ([], "wake", False))


if __name__ == "__main__":
    unittest.main()
