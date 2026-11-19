"""스레드 답 받기 시험: 작업 공간 카드 스레드의 Slack 답글 → 카드 코멘트 + 세션 받은 편지함 + 입력창 한 줄 알림.
임시 작업판(bd)·가짜 slack-inbox·가짜 orca 만 씀. feedback_route.py 는 폴러처럼 별도 프로세스로 돌림."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SCRIPT = os.path.join(ROOT, "hubs", "scripts", "feedback_route.py")
FAKE_ORCA = os.path.join(ROOT, "tests", "stall_watch", "fake_orca.py")
FAKE_ORCA_SHOW = os.path.join(HERE, "fake_orca_show.py")  # terminal show(화면 끝) 지원판
TERM = "term_00000000-0000-4000-8000-0000000000aa"
WS = "/w/feat-a"
END = "slack-thread-end"
PARENT_TS = "1794383274.111111"
EPIC_TS = "1794383274.222222"


class ReplyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cc-wsr-")
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.board, self.hub, self.fake, self.bin = (os.path.join(t, n) for n in ("board", "hub", "fake", "bin"))
        for d in (self.board, self.hub, self.fake, self.bin):
            os.makedirs(d)
        subprocess.run("git init -q && bd init --prefix t -q", shell=True, cwd=self.board, capture_output=True, check=True)
        shutil.copy(FAKE_ORCA, os.path.join(self.bin, "orca"))
        os.chmod(os.path.join(self.bin, "orca"), 0o755)
        self.env = dict(os.environ, BOARD_DIR=self.board, LRM_HUB_DIR=self.hub, FAKE_DIR=self.fake, LRM_ORCA=os.path.join(self.bin, "orca"),
                        HUB_PROC_TABLE=os.path.join(t, "procs"))
        self.env.pop("BOARD_BD", None)
        self.write(os.path.join(t, "procs"), f"1 {TERM}\n")
        self.set_terminals([{"handle": TERM, "worktreePath": WS, "agentIdentity": "claude", "writable": True, "connected": True, "orphaned": False}])
        self.epic = self.make("기능", "-t", "epic", "-l", f"feature:x,slack-thread:{EPIC_TS}")
        self.ws_card = self.make("작업", "-t", "task", "--parent", self.epic, "-l", f"ws:{WS},slack-thread:{PARENT_TS}")
        self.inbox = os.path.join(self.hub, "slack-inbox.jsonl")
        self.write(self.inbox, "")
        self.route()  # 첫 실행: 파일 끝에서 시작

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def set_terminals(self, terms):
        self.write(os.path.join(self.fake, "terminals.json"), json.dumps(terms))

    def bd(self, *args):
        out = subprocess.run(["bd", *args, "--json"], cwd=self.board, capture_output=True, text=True).stdout
        return json.loads(out or "null")

    def make(self, title, *extra):
        return self.bd("create", title, *extra)["id"]

    def comments(self, cid):
        return [c["text"] for c in self.bd("comments", cid) or []]

    def reply(self, ts, parent, text, files=None):
        rec = {"type": "thread_reply", "ts": ts, "parent_ts": parent, "text": text, "channel": "D1", "received_at": 1794383300}
        if files:
            rec["files"] = files
        with open(self.inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def route(self, *extra, env=None):
        p = subprocess.run([sys.executable, "-I", SCRIPT, "--once", "--json", *extra], capture_output=True, text=True, env=dict(self.env, **(env or {})))
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def box(self, name):
        try:
            with open(os.path.join(self.hub, "hub-inbox", name + ".jsonl"), encoding="utf-8") as f:
                return [json.loads(ln) for ln in f if ln.strip()]
        except FileNotFoundError:
            return []

    def sends(self):
        try:
            with open(os.path.join(self.fake, "sends.log"), encoding="utf-8") as f:
                return [ln.rstrip("\n").split("\t", 1) for ln in f if ln.strip()]
        except FileNotFoundError:
            return []

    def wake_on(self):
        self.write(os.path.join(self.hub, ".thread-wake.on"), "")

    def label(self, cid, lb):
        self.bd("label", "add", cid, lb)


    def use_show_orca(self):
        shutil.copy(FAKE_ORCA_SHOW, os.path.join(self.bin, "orca"))
        os.chmod(os.path.join(self.bin, "orca"), 0o755)
        self.env["FAKE_ORCA_BASE"] = FAKE_ORCA

    def assert_handed_off(self, why_part):
        """세션에 닿지 못한 답: 부모 기능 코디네이터 상자에 한 줄 + 작업 공간 카드 코멘트에 넘겼다는 한 줄. 기능 epic 코멘트는 건드리지 않음"""
        rows = self.box("feat-x")
        self.assertEqual(len(rows), 1)
        self.assertIn(f"작업 공간 카드 {self.ws_card} 에 Slack 답 도착, 세션에 알리지 못함", rows[0]["text"])
        self.assertIn(why_part, rows[0]["text"])
        notes = [t for t in self.comments(self.ws_card) if "세션에 알리지 못해" in t]
        self.assertEqual(len(notes), 1)
        self.assertIn("기능 코디네이터 상자 feat-x", notes[0])
        self.assertEqual(self.comments(self.epic), [])




class ButtonAnswerTest(ReplyBase):
    """결정 버튼 답을 그 결정을 올린 세션에 전달(스레드 글 답과 같은 길: 상자 한 줄 + 카드 코멘트 + 입력창 알림)"""

    def call(self, card, ws, text="사용자 원문: 결정 D-TH1 = 선택 2(나) (버튼) (출처: Slack)", kind="answer"):
        code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import feedback_route as f; "
                "print(json.dumps(f.answer_to_ws(sys.argv[2], sys.argv[3], '1794383275.5', sys.argv[4], sys.argv[5])))")
        p = subprocess.run([sys.executable, "-I", "-c", code, os.path.dirname(SCRIPT), card, ws, text, kind], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_answer_lands_in_the_work_card_session_box_and_card_comment(self):
        box = self.call(self.ws_card, WS)
        row = self.box(box)[-1]
        self.assertEqual((row["kind"], row["text"]), ("answer", "사용자 원문: 결정 D-TH1 = 선택 2(나) (버튼) (출처: Slack)"))
        self.assertTrue(any("선택 2(나)" in c for c in self.comments(self.ws_card)))

    def test_feature_coordinator_gets_it_in_the_feature_box(self):
        """기능 코디네이터가 기능 카드(epic)로 올린 결정: 코디네이터가 읽는 기능 상자로"""
        self.assertEqual(self.call(self.epic, "/w/hub"), "feat-x")
        self.assertIn("선택 2(나)", self.box("feat-x")[-1]["text"])

    def test_session_is_told_right_away(self):
        self.write(os.path.join(self.hub, ".thread-wake.on"), "")
        self.call(self.ws_card, WS)
        with open(os.path.join(self.fake, "sends.log"), encoding="utf-8") as f:
            sent = f.read()
        self.assertIn(TERM, sent)
        self.assertIn(self.ws_card, sent)

    def test_question_is_kept_as_question(self):
        box = self.call(self.ws_card, WS, "사용자 원문: 결정 D-TH1 질문: 왜? (출처: Slack)", "question")
        self.assertEqual(self.box(box)[-1]["kind"], "question")



class FeatureSessionTest(ReplyBase):
    """기능 코디네이터가 작업 카드 없이 맨 위 글로 올린 결정: 버튼 답과 그 카드 스레드의 글을 그 코디네이터에게 전달"""

    def setUp(self):
        super().setUp()
        self.hubws = "/w/coord-feat-z-hub"
        self.fe = self.make("기능 z", "-t", "epic", "-l", "feature:z", "-d", f"- 기능 코디네이터 허브: {self.hubws}\n- 통합 브랜치: feature/z")
        card = {"at": 1, "channel": "D1", "ts": "1794383290.000100", "thread_ts": "", "form_kind": "decide", "tag": "feat-z",
                "decision_id": "D-ZPLAN", "options": {"1": "승인", "2": "수정 요청"}, "title": "z 계획 컨펌"}
        self.write(os.path.join(self.hub, "slack-sent.jsonl"), json.dumps(card, ensure_ascii=False) + "\n")

    def session(self, hub):
        code = "import sys, json; sys.path.insert(0, sys.argv[1]); import feedback_route as f; print(json.dumps(f.feature_session(sys.argv[2])))"
        p = subprocess.run([sys.executable, "-I", "-c", code, os.path.dirname(SCRIPT), hub], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_feature_hub_name_resolves_to_its_card_and_workspace(self):
        self.assertEqual(self.session("feat-z"), [self.fe, self.hubws])
        self.assertEqual(self.session("feat-none"), ["", ""])
        self.assertEqual(self.session("ui-layer"), ["", ""])

    def test_reply_under_a_top_level_decision_card_reaches_the_feature_coordinator(self):
        self.reply("1794383291.000001", "1794383290.000100", "지금 샘플로 화면 참조로 쓰는거 구조가 저게 맞아?")
        got = self.route()["routed"][0]
        self.assertEqual((got["to"], got["epic"], got.get("decision_id")), ("feature", self.fe, "D-ZPLAN"))
        line = self.box("feat-z")[-1]["text"]
        self.assertEqual(line, "[결정 D-ZPLAN 질문] 지금 샘플로 화면 참조로 쓰는거 구조가 저게 맞아?")

    def test_coordinator_session_is_told_about_the_reply(self):
        """결정에 달린 글은 사용자가 답을 기다리는 것이라, 코디네이터 창에 바로 한 줄 알림"""
        self.write(os.path.join(self.hub, ".thread-wake.on"), "")
        self.set_terminals([{"handle": TERM, "worktreePath": self.hubws, "agentIdentity": "claude", "writable": True, "connected": True, "orphaned": False}])
        self.reply("1794383291.000009", "1794383290.000100", "이건 왜 필요한가요?")
        self.route()
        with open(os.path.join(self.fake, "sends.log"), encoding="utf-8") as f:
            sent = f.read()
        self.assertIn(self.fe, sent)

    def test_reply_in_the_feature_thread_wakes_the_coordinator(self):
        """기능 스레드에 쓴 글도 코디네이터 창에 알림: 쉬는 코디네이터는 상자를 스스로 읽지 않아 글이 묻힘"""
        self.label(self.fe, "slack-thread:1794383200.000700")
        self.wake_on()
        self.set_terminals([{"handle": TERM, "worktreePath": self.hubws, "agentIdentity": "claude", "writable": True, "connected": True, "orphaned": False}])
        self.reply("1794383291.000011", "1794383200.000700", "여기 색을 더 진하게")
        out = self.route()
        self.assertEqual((out["routed"][0]["to"], out["routed"][0]["epic"]), ("feature", self.fe))
        self.assertEqual([w["wake"] for w in out["wake"]], ["sent"])
        self.assertIn(self.fe, self.sends()[-1][1])

    def test_feature_thread_reply_without_a_known_coordinator_workspace_is_still_recorded(self):
        """기능 카드 설명에 코디네이터 폴더 줄이 없는 옛 기능: 알림만 생략하고 카드·상자 기록은 그대로"""
        self.wake_on()
        self.reply("1794383291.000012", EPIC_TS, "옛 기능 스레드 글")
        out = self.route()
        self.assertEqual((out["routed"][0]["to"], out["routed"][0]["epic"]), ("feature", self.epic))
        self.assertEqual(out.get("wake"), None)
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.box("feat-x")[-1]["text"], "옛 기능 스레드 글")

    def test_plain_reply_under_such_a_card_is_marked_as_answer(self):
        self.reply("1794383291.000002", "1794383290.000100", "슬라이더가 다 이동하면 캐릭터를 끄고 싶다")
        self.route()
        self.assertEqual(self.box("feat-z")[-1]["text"], "[결정 D-ZPLAN 답] 슬라이더가 다 이동하면 캐릭터를 끄고 싶다")

    def test_reply_under_an_unrelated_top_level_message_is_left_alone(self):
        self.reply("1794383291.000003", "1794383999.000999", "아무 글")
        self.assertEqual(self.route()["routed"], [])


class DecisionReplyTest(ReplyBase):
    """작업 스레드에 결정 번호로 시작하는 글은 그 결정의 답으로 전달(번호 없는 글은 지금처럼 피드백)"""

    def setUp(self):
        super().setUp()
        card = {"at": 1, "channel": "D1", "ts": "1794383274.300000", "thread_ts": PARENT_TS, "form_kind": "decide",
                "decision_id": "D-TH1", "options": {"1": "가", "2": "나"}, "title": "배포 순서"}
        self.write(os.path.join(self.hub, "slack-sent.jsonl"), json.dumps(card, ensure_ascii=False) + "\n")

    def test_reply_starting_with_decision_id_is_delivered_as_that_decisions_answer(self):
        self.reply("1794383275.000001", PARENT_TS, "D-TH1 나 먼저로 갑니다")
        got = self.route()["routed"][0]
        self.assertEqual((got["to"], got.get("decision_id")), ("ws", "D-TH1"))
        line = self.box(self.box_name())[-1]["text"]
        self.assertEqual(line, "[결정 D-TH1 답] D-TH1 나 먼저로 갑니다")  # 머리는 짧게(입력창 알림 요약에 원문이 남게), 원문은 그대로
        note = next(c for c in self.comments(self.ws_card) if "나 먼저로 갑니다" in c)
        self.assertIn("decide-answer D-TH1", note)  # 닫는 법은 카드 코멘트에
        self.assertTrue(note.endswith(": D-TH1 나 먼저로 갑니다"), note)  # 코멘트의 원문 자리에는 원문만

    def test_question_about_a_decision_is_marked_as_question_not_answer(self):
        self.reply("1794383275.000004", PARENT_TS, "D-TH1 은 왜 가 가 추천이야?")
        got = self.route()["routed"][0]
        self.assertEqual(got.get("decision_id"), "D-TH1")
        line = self.box(self.box_name())[-1]["text"]
        self.assertTrue(line.startswith("[결정 D-TH1 질문] "), line)
        note = next(c for c in self.comments(self.ws_card) if "추천이야" in c)
        self.assertNotIn("decide-answer", note)  # 질문에 답으로 닫으라고 안내하지 않음

    def test_reply_without_decision_id_stays_plain_feedback(self):
        self.reply("1794383275.000002", PARENT_TS, "색을 좀 더 진하게")
        got = self.route()["routed"][0]
        self.assertNotIn("decision_id", got)
        self.assertEqual(self.box(self.box_name())[-1]["text"], "색을 좀 더 진하게")

    def test_unknown_decision_id_stays_plain_feedback(self):
        self.reply("1794383275.000003", PARENT_TS, "D-ZZZ9 이건 뭐지")
        self.assertNotIn("decision_id", self.route()["routed"][0])

    def box_name(self):
        import re
        return ("ws-" + re.sub(r"[^a-z0-9]+", "-", self.ws_card.lower()).strip("-"))


class ReplyToSessionTest(ReplyBase):
    RAW = "버튼 색이 *진해요* — `#FFB` 로\n둘째 줄도 그대로"

    def test_reply_goes_to_card_comment_and_session_inbox_verbatim(self):
        self.wake_on()  # 스위치 꺼짐은 부모 상자로 넘기므로(아래 폴백 시험) 여기서는 켜 둠
        self.reply("1794383300.000001", PARENT_TS, self.RAW, ["/hub/slack-files/a.png"])
        out = self.route()
        self.assertEqual([(r["to"], r["card"]) for r in out["routed"]], [("ws", self.ws_card)])
        want = self.RAW + "\n첨부: /hub/slack-files/a.png"
        self.assertEqual(self.comments(self.ws_card), ["Slack 피드백(원문): " + want])
        rows = self.box("ws-" + self.ws_card.replace(".", "-"))
        self.assertEqual([(r["kind"], r["text"]) for r in rows], [("feedback", want)])
        self.assertEqual(self.comments(self.epic), [], "기능 코디네이터 쪽에는 가지 않음")
        self.assertEqual(self.box("main"), [])

    def test_switch_off_means_no_input_line_and_hands_off_to_feature_box(self):
        self.reply("1794383300.000001", PARENT_TS, "답")
        self.route()
        self.assertEqual(self.sends(), [])
        self.assertEqual(json.load(open(os.path.join(self.hub, "thread-wake.json")))["items"], [])
        self.assertEqual(len(self.box("ws-" + self.ws_card.replace(".", "-"))), 1, "편지함·코멘트는 늘 넣음")
        self.assert_handed_off("스위치 꺼짐")

    def test_switch_on_sends_one_line_to_session_terminal(self):
        self.wake_on()
        self.reply("1794383300.000001", PARENT_TS, "답")
        out = self.route()
        self.assertEqual(self.sends(), [[TERM, f"Slack 답 도착: bd show {self.ws_card}"]])
        self.assertEqual([w["wake"] for w in out["wake"]], ["sent"])
        self.reply("1794383300.000002", PARENT_TS, "또 답")
        self.route()
        self.assertEqual(len(self.sends()), 2)
        self.route()
        self.assertEqual(len(self.sends()), 2, "같은 답으로 다시 알리지 않음")

    def test_blocked_prompt_is_retried_in_later_round(self):
        self.wake_on()
        self.reply("1794383300.000001", PARENT_TS, "답")
        out = self.route(env={"FAKE_SEND_BLOCK": f"{TERM}:1"})
        self.assertEqual([w["wake"] for w in out["wake"]], ["blocked_retry"])
        self.assertEqual(self.sends(), [])
        state = os.path.join(self.hub, "thread-wake.json")
        self.assertEqual(self.route(env={"FAKE_SEND_BLOCK": f"{TERM}:1"}).get("wake"), None, "재시도 간격 전에는 다시 시도하지 않음")
        d = json.load(open(state))
        d["items"][0]["next_at"] = 0
        self.write(state, json.dumps(d))
        out = self.route(env={"FAKE_SEND_BLOCK": f"{TERM}:1"})
        self.assertEqual([w["wake"] for w in out["wake"]], ["sent"])
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(json.load(open(state))["items"], [])

    def test_blocked_gives_up_after_limit_but_answer_stays_in_card(self):
        self.wake_on()
        self.reply("1794383300.000001", PARENT_TS, "답")
        env = {"FAKE_SEND_BLOCK": f"{TERM}:99"}
        state = os.path.join(self.hub, "thread-wake.json")
        seen = []
        for _ in range(6):
            seen += [w["wake"] for w in self.route(env=env).get("wake", [])]
            if os.path.exists(state) and json.load(open(state))["items"]:
                d = json.load(open(state))
                d["items"][0]["next_at"] = 0
                self.write(state, json.dumps(d))
        self.assertEqual(seen, ["blocked_retry"] * 4 + ["handed_off"])
        self.assertEqual(json.load(open(state))["items"], [])
        self.assertEqual(self.sends(), [])
        self.assert_handed_off("입력 막힘 5회")

    def test_no_live_terminal_retries_then_hands_off_to_feature_box(self):
        self.wake_on()
        self.set_terminals([])
        self.reply("1794383300.000001", PARENT_TS, "답")
        state = os.path.join(self.hub, "thread-wake.json")
        seen = []
        for _ in range(5):
            seen += [w["wake"] for w in self.route().get("wake", [])]
            if os.path.exists(state) and json.load(open(state))["items"]:
                d = json.load(open(state))
                d["items"][0]["next_at"] = 0
                self.write(state, json.dumps(d))
        self.assertEqual(seen, ["failed_retry"] * 4 + ["handed_off"])
        self.assertEqual(len(self.box("ws-" + self.ws_card.replace(".", "-"))), 1)
        self.assert_handed_off("터미널 전송 실패 5회")

    def test_draft_in_input_box_defers_wake_and_counts_toward_limit(self):
        """입력 칸에 사용자가 쓰던 글이 있으면 보내지 않고 미룸(막힘과 같은 5회 상한). 칸이 비면 다음 회차에 보냄"""
        self.use_show_orca()
        self.write(os.path.join(self.fake, f"preview_{TERM}.txt"), "작업 중\n> 쓰던 글 입니다\n")
        self.wake_on()
        self.reply("1794383300.000001", PARENT_TS, "답")
        out = self.route()
        self.assertEqual([w["wake"] for w in out["wake"]], ["blocked_retry"])
        self.assertIn("screen_draft", out["wake"][0]["why"])
        self.assertEqual(self.sends(), [])
        state = os.path.join(self.hub, "thread-wake.json")
        self.assertEqual(json.load(open(state))["items"][0]["tries"], 1)
        self.write(os.path.join(self.fake, f"preview_{TERM}.txt"), "작업 중\n> \n")  # 사용자가 글을 지움
        d = json.load(open(state))
        d["items"][0]["next_at"] = 0
        self.write(state, json.dumps(d))
        self.assertEqual([w["wake"] for w in self.route()["wake"]], ["sent"])
        self.assertEqual(len(self.sends()), 1)

    def test_draft_forever_hands_off_after_limit(self):
        self.use_show_orca()
        self.write(os.path.join(self.fake, f"preview_{TERM}.txt"), "> 쓰던 글\n")
        self.wake_on()
        self.reply("1794383300.000001", PARENT_TS, "답")
        state = os.path.join(self.hub, "thread-wake.json")
        seen = []
        for _ in range(5):
            seen += [w["wake"] for w in self.route().get("wake", [])]
            if os.path.exists(state) and json.load(open(state))["items"]:
                d = json.load(open(state))
                d["items"][0]["next_at"] = 0
                self.write(state, json.dumps(d))
        self.assertEqual(seen, ["blocked_retry"] * 4 + ["handed_off"])
        self.assertEqual(self.sends(), [])

    def test_stale_queue_item_is_handed_off_instead_of_sent(self):
        """스위치를 오래 꺼 뒀다 켠 경우: 나이 상한을 넘은 대기 항목은 입력창에 보내지 않고 부모 상자로"""
        self.wake_on()
        self.reply("1794383300.000001", PARENT_TS, "답")
        self.route(env={"FAKE_SEND_BLOCK": f"{TERM}:1"})  # 막힌 채로 대기열에 남김
        state = os.path.join(self.hub, "thread-wake.json")
        d = json.load(open(state))
        d["items"][0]["at"] -= 7200
        d["items"][0]["next_at"] = 0
        self.write(state, json.dumps(d))
        out = self.route()
        self.assertEqual([w["wake"] for w in out["wake"]], ["handed_off"])
        self.assertEqual(self.sends(), [])
        self.assert_handed_off("분 넘게 대기")

    def test_no_feature_parent_hands_off_to_confirm_card(self):
        orphan = self.make("부모 없는 작업", "-t", "task", "-l", "ws:/w/orphan,slack-thread:1794383274.333333")
        self.reply("1794383300.000001", "1794383274.333333", "답")
        out = self.route()
        self.assertEqual(out["wake"][0]["wake"], "handed_off")
        self.assertIn("확인 카드", out["wake"][0]["to"])
        triage = [r for r in self.bd("list", "--label", "feedback:triage", "--limit", "0") if "작업 공간 답 전달 확인 필요" in r["title"]]
        self.assertEqual(len(triage), 1)
        self.assertIn(f"작업 공간 카드 {orphan} 에 Slack 답 도착", self.bd("show", triage[0]["id"])[0]["description"])
        self.assertTrue(any("확인 카드" in t for t in self.comments(orphan)))

    def test_dry_run_changes_nothing(self):
        self.wake_on()
        self.reply("1794383300.000001", PARENT_TS, "답")
        out = self.route("--dry-run")
        self.assertEqual(len(out["routed"]), 1)
        self.assertEqual((self.comments(self.ws_card), self.sends()), ([], []))
        self.assertFalse(os.path.exists(os.path.join(self.hub, "feedback-routed.jsonl")))


class HandoffLinesTest(ReplyBase):
    """부모 상자로 넘기는 줄은 답마다 달라야 받은 편지함 중복 판정에 걸리지 않음"""

    def test_three_replies_in_separate_rounds_give_three_parent_box_lines(self):
        for i in range(3):  # 스위치 꺼짐: 회차마다 그 회차의 답이 바로 부모로 넘어감
            self.reply(f"1794383300.00000{i + 1}", PARENT_TS, f"{i + 1}번째 답\n둘째 줄은 요약에 안 들어감")
            self.assertEqual([w["wake"] for w in self.route()["wake"]], ["handed_off"])
        rows = self.box("feat-x")
        self.assertEqual(len(rows), 3)
        for i, r in enumerate(rows):
            self.assertIn(f"답 첫 줄: {i + 1}번째 답.", r["text"])
            self.assertNotIn("둘째 줄", r["text"])
            self.assertTrue(r["ref"].endswith(f"slack:1794383300.00000{i + 1}"))
        self.assertEqual(len([t for t in self.comments(self.ws_card) if "세션에 알리지 못해" in t]), 3)

    def test_three_replies_in_one_round_give_three_lines(self):
        for i in range(3):
            self.reply(f"1794383300.00000{i + 1}", PARENT_TS, f"한꺼번에 {i + 1}")
        self.route()
        self.assertEqual([r["text"].split("답 첫 줄: ")[1].split(".")[0] for r in self.box("feat-x")], ["한꺼번에 1", "한꺼번에 2", "한꺼번에 3"])

    def test_same_reason_again_after_gap_is_not_deduplicated(self):
        self.use_show_orca()
        self.write(os.path.join(self.fake, f"preview_{TERM}.txt"), "> 쓰던 글\n")
        self.wake_on()
        for i in range(2):  # 막힘 5회로 넘어가는 일을 두 번(사유 문구 같음)
            self.reply(f"1794383300.00000{i + 1}", PARENT_TS, f"답 {i + 1}")
            state = os.path.join(self.hub, "thread-wake.json")
            for _ in range(5):
                self.route()
                if os.path.exists(state) and json.load(open(state))["items"]:
                    d = json.load(open(state))
                    d["items"][0]["next_at"] = 0
                    self.write(state, json.dumps(d))
        self.assertEqual(len(self.box("feat-x")), 2)

    def test_handoff_that_keeps_failing_is_dropped_after_limit(self):
        """작업 카드가 지워지는 등으로 넘기기가 계속 실패하면 끝없이 반복하지 않고 3회째에 포기"""
        ghost = "t-ghost"
        self.write(os.path.join(self.hub, "thread-wake.json"), json.dumps({"items": [{"card": ghost, "ws": WS, "tries": 0, "next_at": 0, "at": 9999999999}]}))
        seen = [self.route()["wake"][0]["wake"] for _ in range(3)]
        self.assertEqual(seen, ["handoff_failed", "handoff_failed", "handoff_dropped"])
        self.assertEqual(json.load(open(os.path.join(self.hub, "thread-wake.json")))["items"], [])


class ReplyFailureTest(ReplyBase):
    def test_empty_body_reply_is_routed_with_placeholder_and_does_not_block(self):
        self.reply("1794383300.000001", PARENT_TS, "")
        self.reply("1794383300.000002", EPIC_TS, "뒤 답글")
        out = self.route()
        self.assertEqual([r["to"] for r in out["routed"]], ["ws", "feature"])
        self.assertEqual(self.comments(self.ws_card)[0], "Slack 피드백(원문): (본문 없음)")
        texts = [r["text"] for r in self.box("feat-x")]
        self.assertEqual(texts[0], "뒤 답글")  # 이어서 스위치 꺼짐 넘김 줄(빈 답은 '(본문 없음)' 요약)
        self.assertIn("답 첫 줄: (본문 없음).", texts[1])

    def test_inbox_write_failure_is_counted_and_does_not_duplicate_comments(self):
        """코멘트는 상자 쓰기가 된 뒤에만 달고, 상자 쓰기 실패도 3회 제한에 잡힘"""
        os.makedirs(os.path.join(self.hub, "hub-inbox"), exist_ok=True)
        os.makedirs(os.path.join(self.hub, "hub-inbox", "ws-" + self.ws_card.replace(".", "-") + ".jsonl"))  # 파일 자리에 폴더 → 쓰기 실패
        self.reply("1794383300.000001", PARENT_TS, "상자에 못 쓰는 답")
        self.reply("1794383300.000002", EPIC_TS, "뒤 답글")
        for _ in range(2):
            p = subprocess.run([sys.executable, "-I", SCRIPT, "--once", "--json"], capture_output=True, text=True, env=self.env)
            self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertEqual(self.comments(self.ws_card), [], "상자 쓰기가 실패한 회차에는 코멘트를 달지 않음")
        out = self.route()
        self.assertEqual([r["to"] for r in out["routed"]], ["triage", "feature"])
        self.assertEqual([r["text"] for r in self.box("feat-x")], ["뒤 답글"])
        self.assertEqual(self.comments(self.ws_card), [])


    def test_reply_failing_repeatedly_goes_to_confirm_card_and_next_reply_proceeds(self):
        """한 답글에서 bd 쓰기가 계속 실패해도 뒤 답글이 막히지 않음: 3회째에 확인 카드로 넘기고 진행"""
        wrapper = os.path.join(self.bin, "bd-fail")
        self.write(wrapper, f'#!/bin/bash\n[ "$1" = comments ] && [ "$2" = add ] && [ "$3" = "{self.ws_card}" ] && exit 1\nexec bd "$@"\n')
        os.chmod(wrapper, 0o755)
        env = dict(self.env, BOARD_BD=wrapper)
        self.reply("1794383300.000001", PARENT_TS, "계속 실패하는 답")
        self.reply("1794383300.000002", EPIC_TS, "뒤 답글")
        run = lambda: subprocess.run([sys.executable, "-I", SCRIPT, "--once", "--json"], capture_output=True, text=True, env=env)  # noqa: E731
        for _ in range(2):
            p = run()
            self.assertEqual(p.returncode, 3, p.stderr)
            self.assertEqual(self.box("feat-x"), [], "실패 중에는 뒤 답글도 대기")
        p = run()
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual([r["to"] for r in out["routed"]], ["triage", "feature"])
        self.assertIn("전달 3회 실패", out["routed"][0]["why"])
        self.assertEqual([r["text"] for r in self.box("feat-x")], ["뒤 답글"])
        self.assertTrue(any("계속 실패하는 답" in t for t in self.comments(out["routed"][0]["card"])))
        self.assertEqual(json.loads(run().stdout)["routed"], [], "다시 처리하지 않음")


class ReplyFallbackTest(ReplyBase):
    def feat_box(self):
        return self.box("feat-x")

    def test_ended_workspace_card_reply_goes_to_feature_epic(self):
        self.label(self.ws_card, END)
        self.reply("1794383300.000001", PARENT_TS, "끝난 뒤 답")
        out = self.route()
        self.assertEqual([(r["to"], r["epic"]) for r in out["routed"]], [("feature", self.epic)])
        self.assertIn(f"끝난 작업 공간 카드 {self.ws_card}", self.comments(self.epic)[0])
        self.assertTrue(self.comments(self.epic)[0].endswith("끝난 뒤 답"))
        self.assertEqual([r["text"] for r in self.feat_box()], ["끝난 뒤 답"])
        self.assertEqual(self.comments(self.ws_card), [])

    def test_closed_workspace_card_also_goes_to_feature(self):
        self.bd("close", self.ws_card)
        self.reply("1794383300.000001", PARENT_TS, "닫힌 뒤 답")
        self.assertEqual(self.route()["routed"][0]["to"], "feature")

    def test_feature_also_finished_goes_to_confirm_card(self):
        self.label(self.ws_card, END)
        self.bd("close", self.ws_card)
        self.bd("close", self.epic)
        self.reply("1794383300.000001", PARENT_TS, "기능도 끝난 뒤 답")
        r = self.route()["routed"][0]
        self.assertEqual(r["to"], "triage")
        self.assertIn("기능도 끝남", r["why"])
        self.assertTrue(any("기능도 끝난 뒤 답" in t for t in self.comments(r["card"])))
        self.assertEqual(self.feat_box(), [])

    def test_same_thread_on_two_cards_is_ambiguous(self):
        self.make("겹친 카드", "-t", "task", "-l", f"ws:/w/b,slack-thread:{PARENT_TS}")
        self.reply("1794383300.000001", PARENT_TS, "누구 것?")
        r = self.route()["routed"][0]
        self.assertEqual((r["to"], r["why"]), ("triage", "카드 여럿"))
        self.assertEqual(self.box("ws-" + self.ws_card.replace(".", "-")), [])

    def test_feature_thread_reply_still_goes_to_feature(self):
        self.reply("1794383300.000001", EPIC_TS, "기능 스레드 답")
        r = self.route()["routed"][0]
        self.assertEqual((r["to"], r["box"]), ("feature", "feat-x"))

    def test_routed_reply_is_skipped_by_main_forwarding(self):
        sys.path.insert(0, os.path.join(ROOT, "hubs", "scripts"))
        import feedback_route
        self.reply("1794383300.000001", PARENT_TS, "답")
        self.route()
        self.assertIn("1794383300.000001", feedback_route.routed_ts(os.path.join(self.hub, "feedback-routed.jsonl")))


class AttachCliTest(ReplyBase):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True, text=True, env=self.env)

    def test_attach_conflict_exits_5_and_is_repeat_safe(self):
        other = self.make("다른 카드", "-t", "task")
        p = self.cli("attach", "--card", other, "--ts", PARENT_TS, "--ws", "/w/o")
        self.assertEqual(p.returncode, 5, p.stderr)
        self.assertIn("확인 카드", p.stderr)
        again = self.cli("attach", "--card", self.ws_card, "--ts", PARENT_TS, "--json")
        self.assertEqual((again.returncode, json.loads(again.stdout)["added"]), (0, False))

    def test_end_thread_marks_once(self):
        self.assertTrue(json.loads(self.cli("end-thread", "--card", self.ws_card, "--json").stdout)["added"])
        self.assertFalse(json.loads(self.cli("end-thread", "--card", self.ws_card, "--json").stdout)["added"])
        self.assertEqual(self.cli("end-thread", "--card", "t-nope").returncode, 4)
        self.assertEqual(self.cli("end-thread", "--card", "bad card").returncode, 2)


if __name__ == "__main__":
    unittest.main()
