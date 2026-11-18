"""discord_answers: 디스코드 결정 답을 결정을 올린 허브로 한 번만 전달(버튼과 같은 경로). 실제 허브·터미널 호출 없음"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts"))
import answer_route  # noqa: E402
import discord_answers  # noqa: E402

TS = "1572630791677022289"


class FakeRouter:
    def __init__(self, store):
        self.store, self.calls = store, []

    def route(self, did, ts, source, body, on_done=None):
        self.calls.append((did, ts, source, body))
        self.store.record_delivery(did, ts, "hub", True)
        return answer_route.Outcome(True, "hub", "feat-opening")


def react(emoji, action, pick):
    return {"type": "reaction", "emoji": emoji, "action": action, "kind": "decide", "decision_id": "D-OPEN1", "pick": pick,
            "option_text": "그림 포함 그대로", "parent_ts": TS, "tag": "feat-opening", "source": "discord"}


class AnswersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, items):
        with open(os.path.join(self.hub, "slack-inbox.jsonl"), "a") as f:
            f.writelines(json.dumps(i, ensure_ascii=False) + "\n" for i in items)
        store = discord_answers.answer_store.AnswerStore(self.hub)
        r = FakeRouter(store)
        return discord_answers.route_new(self.hub, items, router=r), r

    def test_single_reaction_routes_once_as_discord(self):
        done, r = self.run_with([react("one", "pick", 1)])
        self.assertEqual(done, [("D-OPEN1", "hub", True)])
        self.assertEqual(r.calls[0][2], "discord")
        self.assertIn("D-OPEN1", r.calls[0][3])
        again, r2 = self.run_with([])  # 다음 회차: 전달 기록이 있어 다시 안 보냄
        self.assertEqual((again, r2.calls), ([], []))
        self.assertEqual(discord_answers.route_new(self.hub, [react("one", "pick", 1)], router=r2), [])

    def test_conflicting_answers_not_routed(self):
        done, r = self.run_with([react("one", "pick", 1), react("two", "pick", 2)])
        self.assertEqual((done, r.calls), ([], []))

    def test_hold_reaction_not_routed(self):
        done, _ = self.run_with([react("x", "hold", None)])
        self.assertEqual(done, [])


if __name__ == "__main__":
    unittest.main()
