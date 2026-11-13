import json
import os
import subprocess
import sys
import tempfile
import unittest
import warnings

warnings.simplefilter("ignore", ResourceWarning)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hubs", "scripts"))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cc-answer-")
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

    def ans(self, did="D-0001", ts="1.1", source=None, extra=None, label="2. B 안"):
        return self.st.Answer(did, ts, "C1", "시험 결정", label, source or self.st.SLACK, extra or {"action": "pick", "pick": 2})

    def lines(self, name):
        with open(os.path.join(self.hub, name), encoding="utf-8") as f:
            return f.read().splitlines()

    def test_first_answer_records_inbox_and_done_in_slack_format(self):
        c = self.store.claim(self.ans(), now=100)
        self.assertEqual(c.status, "claimed")
        rec = json.loads(self.lines("slack-inbox.jsonl")[0])
        self.assertEqual(rec, {"type": "button", "parent_ts": "1.1", "channel": "C1", "title": "시험 결정", "decision_id": "D-0001",
                               "kind": "decide", "option_text": "2. B 안", "received_at": 100, "action": "pick", "pick": 2, "source": "slack"})
        self.assertEqual(self.lines("slack-done.txt"), ["1.1"])

    def test_old_readers_see_it_as_a_closed_card(self):
        """기존 리더(panel_collect.card_state·todo_facts.closed_cards)가 문서 사이트 답도 닫힘으로 읽음"""
        import panel_collect
        import todo_facts
        self.store.claim(self.ans(source=self.st.DOCSITE))
        inbox = [json.loads(x) for x in self.lines("slack-inbox.jsonl")]
        card = {"form_kind": "decide", "decision_id": "D-0001", "ts": "1.1", "options": {"1": "가", "2": "나"}}
        reactions = {"1.1": [d for d in inbox if d["parent_ts"] == "1.1"]}
        self.assertEqual(panel_collect.card_state(card, reactions["1.1"]), "closed")
        self.assertEqual(todo_facts.closed_cards([card], inbox, set(self.lines("slack-done.txt")), panel_collect.card_state), {"D-0001": True})

    def test_late_answer_is_rejected_and_writes_nothing(self):
        self.store.claim(self.ans(source=self.st.SLACK))
        before = (self.lines("slack-inbox.jsonl"), self.lines("slack-done.txt"))
        c = self.store.claim(self.ans(source=self.st.DOCSITE, extra={"action": "pick", "pick": 1}, label="1. A 안"))
        self.assertTrue(c.late)
        self.assertEqual(c.by, "slack")
        self.assertEqual(c.late_text(), "이미 Slack 에서 답함")
        self.assertEqual((self.lines("slack-inbox.jsonl"), self.lines("slack-done.txt")), before)
        c2 = self.store.claim(self.ans(source=self.st.SLACK))
        self.assertTrue(c2.late)

    def test_late_text_names_docsite(self):
        self.store.claim(self.ans(source=self.st.DOCSITE))
        c = self.store.claim(self.ans(source=self.st.SLACK))
        self.assertEqual((c.by, c.late_text()), ("docsite", "이미 문서 사이트에서 답함"))

    def test_late_answer_does_not_take_card_action(self):
        self.actions.register("D-0001", "resume", {"slugs": ["a"]})
        self.store.claim(self.ans(source=self.st.SLACK))  # 첫 답이 등록을 꺼냄
        self.actions.register("D-0001", "resume", {"slugs": ["a"]})
        c = self.store.claim(self.ans(source=self.st.DOCSITE))
        self.assertTrue(c.late)
        self.assertIn("D-0001", self.actions.load())  # 늦은 쪽은 등록을 건드리지 않음

    def test_first_answer_takes_card_action_entry(self):
        self.actions.register("D-0001", "resume", {"slugs": ["a"]})
        c = self.store.claim(self.ans())
        self.assertEqual(c.taken[0], "ok")
        self.assertEqual(c.taken[1]["kind"], "resume")
        self.assertEqual(self.actions.load(), {})

    def test_other_decisions_are_independent(self):
        self.assertEqual(self.store.claim(self.ans("D-0001", "1.1")).status, "claimed")
        self.assertEqual(self.store.claim(self.ans("D-0002", "2.2")).status, "claimed")

    def test_emoji_pick_counts_but_hold_does_not(self):
        with open(os.path.join(self.hub, "slack-inbox.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "reaction", "kind": "decide", "decision_id": "D-0001", "action": "hold", "parent_ts": "1.1"}) + "\n")
            f.write("깨진 줄\n")
        self.assertEqual(self.store.claim(self.ans()).status, "claimed")  # 보류·깨진 줄은 답이 아님
        with open(os.path.join(self.hub, "slack-inbox.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "reaction", "kind": "decide", "decision_id": "D-0003", "action": "pick", "pick": 1, "parent_ts": "3.3"}) + "\n")
        c = self.store.claim(self.ans("D-0003", "3.3", source=self.st.DOCSITE))
        self.assertEqual((c.status, c.by), ("late", "slack"))

    def test_card_closed_by_hand_in_done_is_late(self):
        with open(os.path.join(self.hub, "slack-done.txt"), "w", encoding="utf-8") as f:
            f.write("1.1\n")
        self.assertTrue(self.store.claim(self.ans(source=self.st.DOCSITE)).late)

    def test_find_card_reads_latest_numbered_card(self):
        rows = [{"ts": "1.1", "form_kind": "decide", "decision_id": "D-0001", "options": {"1": "옛"}},
                {"ts": "2.2", "form_kind": "decide", "decision_id": "D-0001", "options": {"1": "가", "2": "나"}, "tag": "vfx"},
                {"ts": "3.3", "form_kind": "decide", "decision_id": "D-0002", "options": {"1": "다"}},
                {"ts": "4.4", "decision_id": "D-0001"}]
        with open(os.path.join(self.hub, "slack-sent.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n깨진\n")
        self.assertEqual(self.store.find_card("D-0001")["ts"], "2.2")
        self.assertIsNone(self.store.find_card("D-9999"))
        self.assertIsNone(self.store.find_card("../x"))

    def test_two_processes_only_one_wins_each_decision(self):
        """프로세스 2개(Slack·문서 사이트)가 같은 결정 40건에 동시에 claim → 결정마다 정확히 1번만 claimed"""
        go = os.path.join(self.hub, "go")
        env = dict(os.environ)
        procs = [subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "worker.py"), src, go, "40"], stdout=subprocess.PIPE, text=True, env=env)
                 for src in ("slack", "docsite")]
        open(go, "w").close()
        outs = [p.communicate(timeout=60)[0].splitlines() for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs))
        won = {}
        for who, out in zip(("slack", "docsite"), outs):
            for line in out:
                i, status, _by = line.split()
                if status == "claimed":
                    won.setdefault(i, []).append(who)
        self.assertEqual(sorted(won), sorted(str(i) for i in range(40)))
        self.assertTrue(all(len(v) == 1 for v in won.values()), won)
        recs = [json.loads(x) for x in self.lines("slack-inbox.jsonl")]
        self.assertEqual(len(recs), 40)
        self.assertEqual(len({r["decision_id"] for r in recs}), 40)
        self.assertEqual(len(self.lines("slack-done.txt")), 40)
        for r in recs:  # 이긴 쪽 이름이 기록의 source 와 같음
            self.assertEqual([r["source"]], won[str(int(r["decision_id"][3:]))])


if __name__ == "__main__":
    unittest.main()
