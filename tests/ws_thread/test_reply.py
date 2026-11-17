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
