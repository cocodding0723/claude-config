"""이해 확인(board_flow.understand_block·ask_understand) 시험. 큰 기능만 설계 이상에서 막고, 최근 카드의 1번 Slack 답만 인정, 대기 중이면 재발행 안 함."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "board", "lib"))
import board_flow as bf  # noqa: E402

DESC = "- 목표: x\n- 확인 방법: y\n- 기능 코디네이터 허브: /w/coord-feat-mail-hub"
DID = bf.understand_id("lrm-big")


def epic(size="large", *extra):
    return {"id": "lrm-big", "issue_type": "epic", "title": "우편함", "description": DESC,
            "labels": [bf.FLOW_LABEL, bf.SIZE_HEAD + size, "feature:mail", *extra]}


def write(path, *rows):
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class Understand(unittest.TestCase):
    def setUp(self):
        self.hub = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.hub, True)
        open(os.path.join(self.hub, bf.REPORT_NAME), "w").close()
        self._env = {k: os.environ.pop(k, None) for k in (bf.REPORT_ENV, "LRM_REPORT_OUTBOX")}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) for k, v in self._env.items() if v is not None])

    def card(self, ts, first=f"{bf.CONFIRM_WORD} => 설계"):
        write(os.path.join(self.hub, bf.SENT_NAME),
              {"decision_id": DID, "form_kind": "decide", "title": "t", "ts": ts, "at": "5", "options": {"1": first, "2": "다시 설명"}})

    def answer(self, ts, pick, source="slack"):
        write(os.path.join(self.hub, bf.INBOX_NAME), {"decision_id": DID, "parent_ts": ts, "action": "pick", "pick": pick, "source": source})

    def test_large_blocked_from_design_up(self):
        for target in ("design", "build", "verify"):
            self.assertIn(DID, bf.understand_block(epic(), target, self.hub))
        self.assertEqual(bf.understand_block(epic(), "understand", self.hub), "")

    def test_normal_small_pass(self):
        for size in ("normal", "small"):
            self.assertEqual(bf.understand_block(epic(size), "design", self.hub), "")

    def test_old_flow_pass(self):
        e = epic()
        e["labels"].remove(bf.FLOW_LABEL)
        self.assertEqual(bf.understand_block(e, "design", self.hub), "")

    def test_skip_understand_pass(self):
        self.assertEqual(bf.understand_block(epic("large", "skip:understand"), "design", self.hub), "")

    def test_first_answer_passes(self):
        self.card("1.1")
        self.answer("1.1", 1)
        self.assertEqual(bf.understand_block(epic(), "design", self.hub), "")

    def test_second_answer_blocks(self):
        self.card("1.1")
        self.answer("1.1", 2)
        self.assertTrue(bf.understand_block(epic(), "design", self.hub))

    def test_terminal_answer_blocks(self):
        self.card("1.1")
        self.answer("1.1", 1, source="terminal")
        self.assertTrue(bf.understand_block(epic(), "design", self.hub))

    def test_old_card_answer_blocks(self):
        self.card("1.1")
        self.answer("1.1", 1)
        self.card("2.2")
        self.assertTrue(bf.understand_block(epic(), "design", self.hub))

    def test_ask_posts_card(self):
        calls = []
        r = bf.ask_understand(epic(), self.hub, run=lambda c, **k: calls.append(c) or subprocess.CompletedProcess(c, 0, "", ""))
        self.assertTrue(r["posted"])
        a = {calls[0][i]: calls[0][i + 1] for i in range(len(calls[0]) - 1) if calls[0][i].startswith("--")}
        self.assertEqual((a["--decision-id"], a["--kind"], a["--recommend"]), (DID, "decide", "1"))
        self.assertTrue(a["--options"].startswith(f"1={bf.CONFIRM_WORD} =>"))
        self.assertEqual(a["--text"].split("\n")[0], "우편함" + bf.UNDERSTAND_TAIL)

    def test_pending_not_reposted(self):
        calls = []
        run = lambda c, **k: calls.append(c) or subprocess.CompletedProcess(c, 0, "", "")  # noqa: E731
        self.card("1.1")
        self.assertTrue(bf.ask_understand(epic(), self.hub, run=run)["skipped"])
        os.remove(os.path.join(self.hub, bf.SENT_NAME))
        write(os.path.join(self.hub, bf.OUTBOX_NAME), {"decision_id": DID, "kind": "decide", "text": "우편함 이해 확인", "at": 9})
        self.assertTrue(bf.ask_understand(epic(), self.hub, run=run)["skipped"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
