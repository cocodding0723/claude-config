"""Slack 밖에서 받은 답 기록(record·decide-answer)과 글로 쓴 답 판정. 실제 작업 폴더·Slack 은 쓰지 않음(임시 폴더)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import warnings

warnings.simplefilter("ignore", ResourceWarning)
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
CLI = os.path.join(SCRIPTS, "decide-answer")


class TerminalAnswerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cc-terminal-")
        self.hub = self.tmp.name
        self.old = dict(os.environ)
        os.environ["LRM_HUB_DIR"] = self.hub
        for m in [m for m in sys.modules if m in ("answer_store", "card_actions")]:
            del sys.modules[m]
        import answer_store
        import card_actions
        self.st, self.actions = answer_store, card_actions
        self.store = answer_store.AnswerStore()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)
        self.tmp.cleanup()

    def write(self, name, rows):
        with open(os.path.join(self.hub, name), "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def lines(self, name):
        try:
            with open(os.path.join(self.hub, name), encoding="utf-8") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def card(self, did="D-0001", ts="1.1"):
        self.write("slack-sent.jsonl", [{"ts": ts, "channel": "C1", "title": "시험 결정", "form_kind": "decide", "decision_id": did,
                                          "options": {"1": "A 안", "2": "B 안"}}])

    def cli(self, *args):
        p = subprocess.run([sys.executable, "-I", CLI, *args], capture_output=True, text=True, env={**os.environ, "LRM_HUB_DIR": self.hub})
        return p.returncode, p.stdout.strip(), p.stderr.strip()

    def ans(self, source, did="D-0001", ts="1.1"):
        return self.st.Answer(did, ts, "C1", "시험 결정", "승인", source, {"action": "custom", "text": "승인"})

    def test_record_writes_same_shape_with_terminal_source_and_closes(self):
        self.assertEqual(self.store.record(self.ans(self.st.TERMINAL), now=100), "")
        rec = json.loads(self.lines("slack-inbox.jsonl")[0])
        self.assertEqual((rec["type"], rec["source"], rec["decision_id"], rec["parent_ts"], rec["text"]), ("button", "terminal", "D-0001", "1.1", "승인"))
        self.assertEqual(self.lines("slack-done.txt"), ["1.1"])
        self.assertEqual(self.store.first_answer("D-0001", "1.1"), "terminal")

    def test_record_leaves_delivery_log_and_watcher_registration_alone(self):
        """터미널 답은 전달할 곳이 없고, 감시기 카드의 동작은 버튼으로만 실행돼야 함"""
        self.actions.register("D-0001", "stall", {"hub": "ui"})
        self.store.record(self.ans(self.st.TERMINAL))
        self.assertEqual(self.lines(self.st.DELIVERY_NAME), [])
        self.assertIsNotNone(self.actions.take("D-0001"))

    def test_record_after_slack_answer_writes_nothing(self):
        self.store.claim(self.ans(self.st.SLACK), now=100)
        self.assertEqual(self.store.record(self.ans(self.st.TERMINAL), now=200), "slack")
        self.assertEqual(len(self.lines("slack-inbox.jsonl")), 1)

    def test_late_button_after_terminal_answer_is_late_with_reason(self):
        self.store.record(self.ans(self.st.TERMINAL), now=100)
        c = self.store.claim(self.ans(self.st.SLACK), now=200)
        self.assertTrue(c.late)
        self.assertEqual(c.late_text(), "이미 터미널에서 답함")

    def test_free_text_reply_counts_as_answer_but_question_does_not(self):
        reply = {"type": "thread_reply", "kind": "decide", "decision_id": "D-0001", "parent_ts": "1.1", "action": "free"}
        self.assertEqual(self.st.AnswerStore.judge([{**reply, "text": "이건 뭐야?"}], set(), "D-0001", "1.1")[0], "")
        self.assertEqual(self.st.AnswerStore.judge([{**reply, "text": "둘 다 말고 다음 주에 다시 보자"}], set(), "D-0001", "1.1")[0], "slack")

    def test_conflicting_picks_stay_open_even_with_free_text(self):
        """선택 표기가 서로 다르면 충돌. 글 답이 있어도 사람이 정리할 때까지 열어 둠"""
        base = {"type": "thread_reply", "kind": "decide", "decision_id": "D-0001", "parent_ts": "1.1"}
        inbox = [{**base, "action": "pick", "pick": 1}, {**base, "action": "pick", "pick": 2}, {**base, "action": "free", "text": "아무거나"}]
        self.assertEqual(self.st.AnswerStore.judge(inbox, set(), "D-0001", "1.1")[0], "")

    def test_cli_records_pick_with_card_option_text(self):
        self.card()
        rc, out, _ = self.cli("D-0001", "B 로 간다", "--pick", "2")
        self.assertEqual(rc, 0)
        self.assertIn("기록함", out)
        rec = json.loads(self.lines("slack-inbox.jsonl")[0])
        self.assertEqual((rec["action"], rec["pick"], rec["option_text"], rec["text"], rec["source"]), ("pick", 2, "B 안", "B 로 간다", "terminal"))
        self.assertEqual(self.store.closed_by_did(), {"D-0001": True})

    def test_cli_is_safe_to_repeat(self):
        self.card()
        self.cli("D-0001", "승인")
        rc, out, _ = self.cli("D-0001", "승인")
        self.assertEqual(rc, 0)
        self.assertIn("이미 답함(터미널)", out)
        self.assertEqual(len(self.lines("slack-inbox.jsonl")), 1)

    def test_cli_dry_run_writes_nothing(self):
        self.card()
        rc, out, _ = self.cli("D-0001", "승인", "--dry-run")
        self.assertEqual((rc, self.lines("slack-inbox.jsonl"), self.lines("slack-done.txt")), (0, [], []))
        self.assertIn("미리 보기", out)

    def test_cli_unknown_decision_exits_4_and_bad_input_exits_2(self):
        self.assertEqual(self.cli("D-NOPE", "승인")[0], 4)
        self.card()
        self.assertEqual(self.cli("D-0001", "  ")[0], 2)
        self.assertEqual(self.cli("X-1", "승인")[0], 2)
        self.assertEqual(self.cli("D-0001", "승인", "--pick", "9")[0], 2)
        self.assertEqual(self.lines("slack-inbox.jsonl"), [])

    def test_cli_ts_closes_old_card_without_decision_id(self):
        rc, out, _ = self.cli("--ts", "1794968665.024229", "대화로 정함")
        self.assertEqual((rc, self.lines("slack-done.txt")), (0, ["1794968665.024229"]))
        self.assertEqual(self.cli("--ts", "1794968665.024229", "대화로 정함")[1].count("이미 답함"), 1)
        self.assertEqual(self.cli("--ts", "abc", "x")[0], 2)

    def test_board_answer_closes_linked_slack_card(self):
        """작업판에서 닫은 결정(label slack:<결정 번호>)은 Slack 결정 기록에도 남음. Slack 으로 보낸 적 없는 카드는 건드리지 않음"""
        sys.modules.pop("board_cards", None)
        import board_cards
        self.card("D-B12ab", "2.2")
        self.assertEqual(board_cards.close_slack_cards({"labels": ["kind:decision", "slack:D-B12ab"]}, "B 로 간다"), 1)
        rec = json.loads(self.lines("slack-inbox.jsonl")[0])
        self.assertEqual((rec["decision_id"], rec["source"], rec["text"]), ("D-B12ab", "terminal", "B 로 간다"))
        self.assertEqual(board_cards.close_slack_cards({"labels": ["kind:decision", "slack:D-B12ab"]}, "B 로 간다"), 0)  # 이미 닫힘
        self.assertEqual(board_cards.close_slack_cards({"labels": ["kind:decision"]}, "x"), 0)
        self.assertEqual(len(self.lines("slack-inbox.jsonl")), 1)

    def test_cli_json_output(self):
        self.card()
        rc, out, _ = self.cli("D-0001", "승인", "--json")
        self.assertEqual(json.loads(out), {"decision": "D-0001", "cards": [{"ts": "1.1", "recorded": True, "already": "", "dryRun": False}]})


if __name__ == "__main__":
    unittest.main()
