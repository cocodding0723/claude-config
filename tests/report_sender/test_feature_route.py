"""기능 허브 보고를 기능 스레드 하나로 모으기, 본문 티켓·커밋 번호를 참고 줄로 옮기기 시험."""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hubs", "scripts"))
import hub_report  # noqa: E402
import report_sender  # noqa: E402

EPIC_TS = "1795001319.536929"
EPIC_ROWS = [{"id": "lrm-1yd", "labels": ["feature:lrm-677", f"slack-thread:{EPIC_TS}"]}]


class MoveRefsTest(unittest.TestCase):
    def test_body_numbers_move_into_single_ref_line(self):
        out = hub_report.bullet_body("LRM-673 완료 보고\n수치 반영(c4f817b85698)\n참고: 허브 x")
        lines = out.splitlines()
        self.assertEqual(lines[0], "완료 보고")
        self.assertIn("- 수치 반영", lines)
        self.assertEqual(lines[-1], "참고: 허브 x · LRM-673 · c4f817b85698")
        self.assertEqual(sum(ln.startswith("참고:") for ln in lines), 1)

    def test_code_and_url_numbers_stay(self):
        out = hub_report.bullet_items(["링크 https://x/browse/LRM-1", "`LRM-9` 코드"])
        self.assertEqual(out[:2], ["- 링크 https://x/browse/LRM-1", "- `LRM-9` 코드"])
        self.assertFalse(any(ln.startswith("참고:") for ln in out))


class FeatureThreadTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.conf = mock.Mock(state=os.path.join(d.name, "state.json"), log=os.path.join(d.name, "log.txt"))
        self.st = {"topics": {}}

    def route(self, rep, rows):
        with mock.patch.object(report_sender, "bd_json", return_value=rows), \
                mock.patch.object(report_sender, "run_notify", side_effect=AssertionError("새 스레드를 열면 안 됨")):
            report_sender.open_topic(self.conf, rep, self.st, 0)

    def test_ticket_and_card_topics_share_feature_thread(self):
        for rep in ({"hub": "feat-lrm-677", "task": "LRM-677"}, {"hub": "feat-lrm-677", "task": "없음", "card": "lrm-1yd.1"}):
            self.route(rep, EPIC_ROWS)
        self.assertEqual({t["ts"] for t in self.st["topics"].values()}, {EPIC_TS})
        self.assertEqual(set(self.st["topics"]), {"LRM-677", "card:lrm-1yd.1"})

    def test_non_feature_hub_is_not_routed(self):
        self.assertEqual(report_sender.feature_thread(self.conf, {"hub": "ci-hub"}, self.st), ("", ""))

    def test_feature_without_thread_yet_is_not_routed(self):
        with mock.patch.object(report_sender, "bd_json", return_value=[{"id": "lrm-1yd", "labels": ["feature:lrm-677"]}]):
            self.assertEqual(report_sender.feature_thread(self.conf, {"hub": "feat-lrm-677"}, self.st), ("", ""))

    def test_thread_not_attached_to_card_yet_is_found_in_state(self):
        self.st["topics"]["card:lrm-1yd"] = {"ts": EPIC_TS, "card": "lrm-1yd", "attached": False}
        self.route({"hub": "feat-lrm-677", "task": "LRM-677"}, [{"id": "lrm-1yd", "labels": ["feature:lrm-677"]}])
        self.assertEqual(self.st["topics"]["LRM-677"]["ts"], EPIC_TS)


class DecideThreadTest(unittest.TestCase):
    """결정 카드를 스레드 안에 올릴지 맨 위 글로 올릴지. 경우를 더할 때는 CASES 에 한 줄"""
    CASES = [
        ("정보 보고는 언제나 스레드 안", {"kind": "info", "hub": "ci-hub"}, {"ts": "1.1"}, True),
        ("카드를 준 결정은 그 카드 스레드 안", {"kind": "decide", "hub": "ci-hub", "card": "t-1"}, {"ts": "1.1"}, True),
        ("기능 코디네이터의 결정은 기능 스레드 안", {"kind": "decide", "hub": "feat-x"}, {"ts": "1.1", "shared": "t-e"}, True),
        ("기능 코디네이터가 하위 카드로 올린 결정도 기능 스레드 안", {"kind": "decide", "hub": "feat-x", "card": "t-e.1"}, {"ts": "1.1", "shared": "t-e"}, True),
        ("작업 공간 세션이 올린 결정은 맨 위 글(글 답이 그 세션이 아니라 코디네이터로 가므로)",
         {"kind": "decide", "hub": "feat-x", "card": "t-e.1", "ws": "/w/1"}, {"ts": "1.1", "shared": "t-e"}, False),
        ("기능 허브가 아닌 곳이 같이 쓰는 스레드의 결정은 맨 위 글", {"kind": "decide", "hub": "ui-hub", "card": "t-e.9"}, {"ts": "1.1", "shared": "t-e"}, False),
        ("스레드가 아직 없으면 맨 위 글", {"kind": "decide", "hub": "feat-x"}, {}, False),
        ("사용자가 새 메시지로 올리라고 한 결정(--top)은 맨 위 글", {"kind": "decide", "hub": "feat-x", "card": "t-e", "top": True}, {"ts": "1.1"}, False),
    ]

    def test_cases(self):
        for name, rep, topic, want in self.CASES:
            with self.subTest(name):
                self.assertEqual(report_sender.in_thread(rep, topic), want)

    def deliver(self, rep, rows):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        conf = mock.Mock(state=os.path.join(d.name, "state.json"), log=os.path.join(d.name, "log.txt"), sync_state=os.path.join(d.name, "sync.json"))
        st = {"topics": {}, "reports": {}}
        with mock.patch.object(report_sender, "bd_json", return_value=rows), \
                mock.patch.object(report_sender, "run_notify", return_value="1795009999.000001") as sent, \
                mock.patch.object(report_sender, "send_files"):
            report_sender.deliver(conf, rep, st, 0)
        return sent.call_args_list[0][0][1]

    def decide(self, **extra):
        return dict({"id": "r1", "hub": "feat-lrm-677", "task": "없음", "kind": "decide", "decision_id": "D-PLANX", "recommend": "1",
                     "options": {"1": "승인", "2": "수정 요청"}, "text": "계획 승인: 배너\n배경: 계획서를 올림\n용어: 없음", "files": []}, **extra)

    def test_coordinator_decision_without_card_goes_into_the_feature_thread(self):
        """다시 올릴 때 카드 지정을 빼먹어도 맨 위 글로 흩어지지 않음(11-19 한 기능이 맨 위 글 6개로 갈라짐)"""
        args = self.deliver(self.decide(), EPIC_ROWS)
        self.assertEqual(args[args.index("--thread") + 1], EPIC_TS)
        self.assertIn("--broadcast", args)

    def test_top_flag_posts_the_decision_as_a_new_message(self):
        """스레드 안 카드는 눈에 안 띌 수 있음(11-19 "slack에 안 옴" → "새 메시지로 다시 올려"). 카드 지정은 남겨 답이 그 세션으로 가게 함"""
        args = self.deliver(self.decide(top=True, card="lrm-1yd", ws="/w/coord"), EPIC_ROWS)
        self.assertNotIn("--thread", args)

    def test_top_flag_is_accepted_only_for_decisions(self):
        rep = hub_report.make_report("feat-x", "없음", "decide", "계획 승인: x\n배경: 계획서를 올리고 사용자 승인을 기다리는 중\n용어: 없음", [], options="1=승인 => 구현 시작,2=수정 요청 => 계획을 고침",
                                     recommend="1", decision_id="D-PLANX", no_doc=True, top=True)
        self.assertIs(rep["top"], True)
        with self.assertRaises(hub_report.ReportError):
            hub_report.make_report("feat-x", "없음", "info", "진행 보고", [], top=True)

    def test_coordinator_decision_stays_top_level_until_the_feature_thread_exists(self):
        args = self.deliver(self.decide(), [{"id": "lrm-1yd", "labels": ["feature:lrm-677"]}])
        self.assertNotIn("--thread", args)


if __name__ == "__main__":
    unittest.main()
