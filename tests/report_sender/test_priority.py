"""결정 우선 정렬·카드 연결을 전송 뒤로 미루기 시험. Slack·bd 는 부르지 않음."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hubs", "scripts"))
import hub_report  # noqa: E402
import report_sender  # noqa: E402


def rep(rid, kind="info", card="lrm-a"):
    return {"id": rid, "kind": kind, "card": card, "task": "없음", "hub": "h"}


class DecisionsFirstTest(unittest.TestCase):
    def order(self, *reps):
        return [r["id"] for r in report_sender.decisions_first(list(reps), {"reports": {}})]

    def test_decision_jumps_other_topics_and_keeps_own_thread_order(self):
        got = self.order(rep("i1"), rep("b1", card="lrm-b"), rep("i2"), rep("b2", "decide", "lrm-b"), rep("b3", card="lrm-b"))
        self.assertEqual(got, ["b1", "b2", "i1", "i2", "b3"])

    def test_posted_decision_with_files_left_comes_before_plain_reports(self):
        todo = [rep("i1"), rep("d1", "decide", "lrm-b"), rep("d2", "decide", "lrm-c")]
        st = {"reports": {"d1": {"body_ts": "1700000000.000001"}}}
        self.assertEqual([r["id"] for r in report_sender.decisions_first(todo, st)], ["d2", "d1", "i1"])

    def test_without_decision_order_is_unchanged(self):
        self.assertEqual(self.order(rep("1"), rep("2", card="lrm-b"), rep("3")), ["1", "2", "3"])


class AttachLaterTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = lambda n: os.path.join(d.name, n)  # noqa: E731
        self.conf = report_sender.Conf(outbox=p("box"), state=p("state"), sync_state=p("sync"), sent=p("sent"), ledger=p("ledger"), log=p("log"))
        self.events = []

    def attach(self, conf, topic):
        self.events.append("attach:" + topic["card"])

    def test_round_attaches_cards_after_all_sends(self):
        for card in ("lrm-a", "lrm-b"):
            hub_report.add(self.conf.outbox, hub_report.make_report("h", "없음", "info", "진행 " + card, [], card=card), 1.0, False)

        def notify(conf, args, deadline, upload=False):
            self.events.append("send")
            return f"1700000000.{len(self.events):06d}"
        with mock.patch.object(report_sender, "run_notify", notify), mock.patch.object(report_sender, "card_attach", self.attach), \
                mock.patch.object(report_sender, "bd_json", return_value=None):
            res = report_sender.run(self.conf)
        self.assertEqual(res["sent"], 2)
        self.assertEqual(self.events, ["send"] * 4 + ["attach:lrm-a", "attach:lrm-b"])
        report_sender.load_state(self.conf.state)  # 새 상태 칸이 형식 검사에 걸리지 않음

    def test_second_decision_card_goes_out_before_first_cards_files(self):
        for n in ("1", "2"):
            hub_report.add(self.conf.outbox, hub_report.make_report("h" + n, "없음", "decide", "결정 " + n + "\n배경: 시험 작업 중 고를 것이 생김\n용어: 없음", [os.path.abspath(__file__)], options="1=예 => 진행함,2=아니오 => 멈춤",
                                                                    recommend="1", decision_id="D-000" + n), 1.0, False)

        def notify(conf, args, deadline, upload=False):
            self.events.append("file" if upload else "card" if "--decision-id" in args else "etc")
            return f"1700000000.{len(self.events):06d}"
        with mock.patch.object(report_sender, "run_notify", notify), mock.patch.object(report_sender, "bd_json", return_value=None):
            report_sender.run(self.conf)
            self.assertEqual([e for e in self.events if e != "etc"], ["card", "card", "file"])
            report_sender.run(self.conf)  # 미룬 첨부는 다음 회차, 카드는 다시 올리지 않음
        self.assertEqual([e for e in self.events if e != "etc"], ["card", "card", "file", "file"])

    def test_same_thread_card_does_not_defer_files(self):
        a, b = (dict(rep(i, "decide"), files=["x"]) for i in ("1", "2"))
        seen = []
        with mock.patch.object(report_sender, "deliver", lambda conf, r, st, dl, defer=False: seen.append(defer) or {"topic_ts": "", "body_ts": "", "line_ts": ""}), \
                mock.patch.object(report_sender.hub_report, "read_outbox", return_value=[a, b]), \
                mock.patch.object(report_sender, "check_consistency"), mock.patch.object(report_sender, "input_error", return_value=""), \
                mock.patch.object(report_sender, "drop_staged"):
            report_sender.run(self.conf)
        self.assertEqual(seen, [False, False])

    def test_round_stops_starting_reports_after_budget(self):
        seen = []

        def slow(conf, r, st, dl, defer=False):
            seen.append(r["id"])
            time.sleep(0.3)
            raise report_sender.NotifyError("회차 시간 예산 초과", counts=False)
        self.conf.budget_s = 0.2
        with mock.patch.object(report_sender, "deliver", slow), mock.patch.object(report_sender, "check_consistency"), \
                mock.patch.object(report_sender.hub_report, "read_outbox", return_value=[dict(rep(i, card="lrm-" + i), files=[], text="t") for i in "abc"]), \
                mock.patch.object(report_sender, "input_error", return_value=""), mock.patch.object(report_sender, "bd_json", return_value=None):
            report_sender.run(self.conf)
        self.assertEqual(seen, ["a"])

    def test_spent_budget_tries_one_per_rescue_interval_and_rotates(self):
        st = {"topics": {k: {"ts": "1700000000.000001", "channel": "", "card": k, "attached": False} for k in ("a", "b")}, "reports": {}, "warned": {}, "bad": {}}
        with mock.patch.object(report_sender, "card_attach", self.attach):
            report_sender.retry_attach(self.conf, st, time.monotonic() - 1)
            report_sender.retry_attach(self.conf, st, time.monotonic() - 1)
            self.assertEqual(self.events, ["attach:a"])
            st["topics"]["a"]["attach_at"] -= report_sender.ATTACH_RESCUE_S
            report_sender.retry_attach(self.conf, st, time.monotonic() - 1)
        self.assertEqual(self.events, ["attach:a", "attach:b"])


if __name__ == "__main__":
    unittest.main()
