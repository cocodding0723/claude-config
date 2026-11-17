"""카드 주제 보고 시험: 작업 공간 카드 하나 = Slack 스레드 하나. 가짜 알림 스크립트·임시 작업판(bd)만 씀(실제 Slack·작업판 무관).
다룸: 첫 보고 때만 스레드 열기·카드에 ts 등록·담당 작업 공간 기록·같은 ts 두 카드 거부·붙이기 실패 재시도·기능당 스레드 상한."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SCRIPTS = os.path.join(ROOT, "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
FAKE = os.path.join(ROOT, "tests", "report_sender", "fake_notify.py")
import board_cards  # noqa: E402
import feedback_route  # noqa: E402
import hub_report  # noqa: E402
import report_sender  # noqa: E402

THREAD = "slack-thread:"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cc-wst-")
        self.addCleanup(self.tmp.cleanup)
        self.p = lambda n: os.path.join(self.tmp.name, n)
        self.board = self.p("board")
        os.makedirs(self.board)
        subprocess.run("git init -q && bd init --prefix tt -q", shell=True, cwd=self.board, capture_output=True, check=True)
        self.env = dict(os.environ)
        os.environ.update({"BOARD_DIR": self.board, "FAKE_DIR": self.tmp.name, "BOARD_INCIDENTS": self.p("incidents.jsonl")})
        os.environ.pop("BOARD_BD", None)
        self.addCleanup(self._restore)
        self.conf = report_sender.Conf(outbox=self.p("box.jsonl"), state=self.p("state.json"), notify=FAKE, preview="/nonexistent",
                                       ledger=self.p("ledger.tsv"), sync_state=self.p("sync.json"), sent=self.p("sent.jsonl"),
                                       log=self.p("log.txt"), backoff_s=0)

    def _restore(self):
        os.environ.clear()
        os.environ.update(self.env)

    def bd(self, *args):
        out = subprocess.run(["bd", *args, "--json"], cwd=self.board, capture_output=True, text=True).stdout
        d = json.loads(out or "null")
        return d[0] if isinstance(d, list) and d and args[0] == "show" else d

    def make(self, title, *extra):
        return self.bd("create", title, *extra)["id"]

    def card(self, cid):
        return next(r for r in self.bd("list", "--all", "--limit", "0") if r["id"] == cid)

    def labels(self, cid):
        return self.card(cid).get("labels") or []

    def comments(self, cid):
        return [c["text"] for c in self.bd("comments", cid) or []]

    def report(self, card, text="시작", ws=None, kind="info", hub="feat-x"):
        rep = hub_report.make_report(hub, "없음", kind, text, [], card=card, ws=ws)
        return hub_report.add(self.conf.outbox, rep, 1.0, False)[0]

    def calls(self):
        try:
            with open(self.p("calls.jsonl"), encoding="utf-8") as f:
                return [json.loads(ln)["args"] for ln in f]
        except FileNotFoundError:
            return []

    def topics(self):
        return report_sender.load_state(self.conf.state)["topics"]

    def run_sender(self):
        return report_sender.run(self.conf)


class IntakeTest(unittest.TestCase):
    """hub_report 접수 검사(작업판 불필요)"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cc-wst-")
        self.addCleanup(self.tmp.cleanup)
        self.outbox = os.path.join(self.tmp.name, "box.jsonl")

    def err(self, **kw):
        with self.assertRaises(hub_report.ReportError) as cm:
            hub_report.make_report("feat-x", "없음", kw.pop("kind", "info"), "본문", [], **kw)
        return str(cm.exception)

    def test_card_and_ws_are_recorded(self):
        rep = hub_report.make_report("feat-x", "없음", "info", "본문", [], card="lrm-q5o.4", ws="/w/a")
        self.assertEqual((rep["card"], rep["ws"]), ("lrm-q5o.4", "/w/a"))
        self.assertTrue(hub_report.valid_record(dict(rep, id="a1")))

    def test_card_rules(self):
        self.assertIn("decide", self.err(kind="decide", card="t-1", options="1=가 => 나,2=다 => 라", recommend="1", decision_id="D-0001", no_doc=True))
        self.assertIn("--thread", self.err(card="t-1", thread="1.1"))
        self.assertIn("형식 오류", self.err(card="bad card"))
        self.assertIn("--card 와 함께", self.err(ws="/w/a"))
        self.assertIn("절대 경로", self.err(card="t-1", ws="rel/path"))

    def test_same_card_text_is_deduplicated(self):
        rep = hub_report.make_report("feat-x", "없음", "info", "시작", [], card="t-1", ws="/w/a")
        self.assertTrue(hub_report.add(self.outbox, rep, 1.0, False)[1])
        self.assertFalse(hub_report.add(self.outbox, dict(rep), 2.0, False)[1])
        self.assertEqual(len(hub_report.read_outbox(self.outbox)), 1)


class CardThreadTest(Base):
    def test_first_report_opens_thread_and_registers_ts_and_session(self):
        c = self.make("작업", "-t", "task")
        self.report(c, "시작: 작업 공간 열림", ws="/w/a")
        res = self.run_sender()
        self.assertEqual((res["status"], res["sent"]), ("done", 1))
        ts = self.topics()[f"card:{c}"]["ts"]
        self.assertIn(THREAD + ts, self.labels(c))
        self.assertIn("ws:/w/a", self.labels(c))
        self.assertTrue(any(f"Slack 스레드 {ts} 연결" in t and "/w/a" in t for t in self.comments(c)))

    def test_ws_thread_parent_uses_easy_format_of_its_own_card(self):
        """작업 공간 카드의 스레드 첫 메시지도 쉬운 양식. 내부 번호는 참고 줄에만"""
        c = self.make("내부 제목", "-t", "task", "-d", "쉬운 이름: 개인 작업 공간 스레드\n무엇: 작업 공간 하나를 스레드 하나로 묶는다.\n왜: 사용자 요청: 바로바로 소통")
        self.report(c, "시작", ws="/w/a")
        self.run_sender()
        text = next(a for a in self.calls() if "progress" in a and "--thread" not in a)[-1]
        self.assertEqual(text.splitlines()[0], "개인 작업 공간 스레드")
        self.assertIn("- 무엇: 작업 공간 하나를 스레드 하나로 묶는다.", text)
        self.assertEqual(text.splitlines()[-1], f"참고: {c} · 허브 feat-x")
        self.assertNotIn(c, "\n".join(text.splitlines()[:-1]))

    def test_card_with_blank_fields_holds_ws_thread_report(self):
        c = self.make("작업", "-t", "task")  # 설명 칸 없음: 무엇·왜 폴백도 없음
        self.report(c, "시작", ws="/w/a")
        self.assertEqual(self.run_sender()["sent"], 0)
        self.assertNotIn(f"card:{c}", self.topics())

    def test_later_reports_reply_in_same_thread_without_new_topic(self):
        c = self.make("작업", "-t", "task")
        self.report(c, "시작", ws="/w/a")
        self.run_sender()
        ts = self.topics()[f"card:{c}"]["ts"]
        self.report(c, "진행: 절반 끝", ws="/w/a")
        self.run_sender()
        progress = [a for a in self.calls() if "progress" in a and "--thread" not in a and "--quote" not in a]
        self.assertEqual(len(progress), 1, "스레드 시작 메시지는 카드당 1번")
        replies = [a for a in self.calls() if "--thread" in a and a[a.index("--thread") + 1] == ts and "--tag" in a]
        self.assertEqual(len(replies), 2)
        self.assertEqual([x for x in self.labels(c) if x.startswith(THREAD)], [THREAD + ts])

    def test_two_cards_get_separate_threads(self):
        a, b = self.make("가", "-t", "task"), self.make("나", "-t", "task")
        self.report(a, "시작", ws="/w/a")
        self.report(b, "시작", ws="/w/b")
        self.run_sender()
        ta, tb = self.topics()[f"card:{a}"]["ts"], self.topics()[f"card:{b}"]["ts"]
        self.assertNotEqual(ta, tb)
        self.assertIn(THREAD + tb, self.labels(b))

    def test_same_ts_on_two_cards_is_refused_and_goes_to_triage_card(self):
        other = self.make("먼저 붙은 카드", "-t", "task", "-l", THREAD + "1700000000.000001")
        c = self.make("나중 카드", "-t", "task")
        self.report(c, "시작", ws="/w/a")
        res = self.run_sender()
        self.assertEqual(res["sent"], 1, "보고는 올라감")
        self.assertFalse([x for x in self.labels(c) if x.startswith(THREAD)])
        self.assertEqual(self.topics()[f"card:{c}"]["attached"], "conflict")
        triage = self.bd("list", "--label", "feedback:triage", "--limit", "0")
        self.assertEqual(len(triage), 1)
        self.assertTrue(any(other in t and c in t for t in self.comments(triage[0]["id"])))
        self.run_sender()
        self.assertFalse([x for x in self.labels(c) if x.startswith(THREAD)], "거부는 재시도하지 않음")

    def test_attach_failure_keeps_report_and_retries_next_round(self):
        c = self.make("작업", "-t", "task")
        self.report(c, "시작", ws="/w/a")
        os.environ["BOARD_BD"] = "/nonexistent/bd"
        res = self.run_sender()
        self.assertEqual(res["sent"], 1)
        self.assertIs(self.topics()[f"card:{c}"]["attached"], False)
        del os.environ["BOARD_BD"]
        self.run_sender()
        self.assertIs(self.topics()[f"card:{c}"]["attached"], True)
        self.assertTrue([x for x in self.labels(c) if x.startswith(THREAD)])


class CapTest(Base):
    def setUp(self):
        super().setUp()
        self.conf.ws_thread_max = 2
        self.epic = self.make("기능", "-t", "epic", "-l", "feature:x")
        self.kids = [self.make(f"작업 {i}", "-t", "task", "--parent", self.epic) for i in range(3)]

    def open_first_two(self):
        for i, k in enumerate(self.kids[:2]):
            self.report(k, "시작", ws=f"/w/{i}")
        self.run_sender()

    def test_shared_thread_parent_is_not_rewritten_by_child_card(self):
        """상한 밖 카드 보고가 기능 스레드 부모를 자기 카드 양식으로 고쳐 쓰지 않음"""
        self.open_first_two()
        self.report(self.kids[2], "시작", ws="/w/2")
        self.run_sender()
        self.report(self.kids[2], "진행", ws="/w/2")
        self.run_sender()
        self.assertFalse([a for a in self.calls() if "--update" in a])

    def test_over_cap_reports_go_to_feature_thread_as_replies(self):
        self.open_first_two()
        k3 = self.kids[2]
        self.report(k3, "시작", ws="/w/2")
        self.run_sender()
        et = self.topics()[f"card:{self.epic}"]["ts"]
        t3 = self.topics()[f"card:{k3}"]
        self.assertEqual((t3["ts"], t3["shared"]), (et, self.epic))
        self.assertIn(THREAD + et, self.labels(self.epic), "기능 스레드는 epic 이 가짐")
        self.assertFalse([x for x in self.labels(k3) if x.startswith(THREAD)], "상한 밖 작업 공간 카드는 자기 스레드가 없음")
        last = self.calls()[-1]
        self.assertEqual(last[last.index("--thread") + 1], et)
        self.assertIn(k3, last[-1], "어느 카드 글인지 머리에 카드 id")

    def test_over_cap_decision_is_made_once(self):
        self.open_first_two()
        k3 = self.kids[2]
        self.report(k3, "시작", ws="/w/2")
        self.run_sender()
        self.bd("label", "add", self.kids[0], feedback_route.END)  # 상한에 여유가 생겨도
        self.report(k3, "진행", ws="/w/2")
        self.run_sender()
        self.assertEqual(self.topics()[f"card:{k3}"].get("shared"), self.epic)
        self.assertFalse([x for x in self.labels(k3) if x.startswith(THREAD)])

    def test_ended_thread_frees_a_slot(self):
        self.open_first_two()
        feedback_route.end_thread(self.kids[0])
        k3 = self.kids[2]
        self.report(k3, "시작", ws="/w/2")
        self.run_sender()
        self.assertNotIn("shared", self.topics()[f"card:{k3}"])
        self.assertTrue([x for x in self.labels(k3) if x.startswith(THREAD)])


if __name__ == "__main__":
    unittest.main()
