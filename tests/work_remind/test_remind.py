"""남은 작업 리마인드 시험: 무엇을 '내 차례'·'진행 중'·'멈춤'으로 세는지, 언제 보내는지. 작업판·Slack 은 쓰지 않음(순수 함수 + 가짜 전송 스크립트)."""
import json
import os
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import board_cards  # noqa: E402
import pending_judge  # noqa: E402
import work_remind as wr  # noqa: E402

NOW = 1795053600.0  # 2026-11-19 11:00 KST
HOUR = 3600
RULES = pending_judge.Rules(max_age_h=48, auto=pending_judge.compile_auto([r"D-ST[0-9a-f]{10}"]))


def sent(did, ts, by="", at=NOW - HOUR, title="계획 승인: 불꽃 연출"):
    return {"decision_id": did, "ts": ts, "at": at, "by": by, "form_kind": "decide", "options": {"1": "승인"}, "title": title}


def card(cid, status="open", labels=(), title="", kind="task", updated=NOW - 600, assignee=""):
    iso = __import__("datetime").datetime.fromtimestamp(updated, __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": cid, "status": status, "labels": list(labels), "title": title or cid, "issue_type": kind, "updated_at": iso, "assignee": assignee}


class WaitingTest(unittest.TestCase):
    """답을 기다리는 결정. 경우를 더할 때는 CASES 에 한 줄"""
    CASES = [
        ("답이 없는 결정 카드는 기다리는 중", [sent("D-A", "1.1")], ["D-A"]),
        ("답한 카드는 뺌", [sent("D-A", "1.1", by="slack")], []),
        ("같은 번호로 다시 올렸으면 가장 최근 카드만 봄", [sent("D-A", "1.1"), sent("D-A", "2.2", by="slack")], []),
        ("다시 올린 새 카드에 답이 없으면 기다리는 중", [sent("D-A", "1.1", by="slack"), sent("D-A", "2.2")], ["D-A"]),
        ("감시기 자동 알림 카드는 사람이 정할 것이 아님", [sent("D-ST0123456789", "1.1")], []),
        ("기한(48시간)을 넘긴 결정은 다시 알리지 않음", [sent("D-A", "1.1", at=NOW - 49 * HOUR)], []),
        ("선택지 없는 옛 글은 결정이 아님", [dict(sent("D-A", "1.1"), options=None, form_kind="progress")], []),
    ]

    def test_cases(self):
        for name, cards, want in self.CASES:
            with self.subTest(name):
                self.assertEqual([c["decision_id"] for c in wr.waiting(cards, RULES, NOW)], want)


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.cards = [
            card("t-e1", labels=["feature:flame", "flow:v1", "stage:build", "size:normal"], title="[LRM-684] 텐션업 불꽃 상승 연출", kind="epic"),
            card("t-e1.1", "closed"), card("t-e1.2", "in_progress", assignee="w"), card("t-e1.3"),
            card("t-e2", "closed", labels=["feature:banner", "stage:review"], title="[LRM-677] 승부 배너 앵커 자동 배치", kind="epic", updated=NOW - 20 * HOUR),
            card("t-e3", "closed", labels=["feature:old"], title="끝난 기능", kind="epic"),
            card("t-e4", labels=[], title="[EPIC] 버그 팀 11-17~", kind="epic"),
            card("t-e4.1", "in_progress", title="[LRM-645] 초상화 뒤바뀜 — 원인 조사", updated=NOW - 50 * HOUR, assignee="bug"),
        ]

    def test_review_waiting_features_are_my_turn(self):
        self.assertEqual([r["name"] for r in wr.reviews(self.cards)], ["승부 배너 앵커 자동 배치"])

    def test_open_features_show_stage_and_card_progress(self):
        got = wr.features(self.cards, wr.flow_lib())
        self.assertEqual([(f["name"], f["stage"], f["done"], f["total"]) for f in got],
                         [("텐션업 불꽃 상승 연출", "3 구현", 1, 3)])

    def test_feature_without_feature_label_is_not_listed_as_feature(self):
        self.assertNotIn("버그 팀 11-17~", [f["name"] for f in wr.features(self.cards)])

    def test_in_progress_cards_without_update_are_stalled(self):
        got = wr.stalled(self.cards, NOW, hours=24)
        self.assertEqual([(s["name"], s["days"]) for s in got], [("초상화 뒤바뀜 — 원인 조사", 2)])


class BuildTest(unittest.TestCase):
    def build(self, **over):
        parts = dict(decisions=[sent("D-A", "1.1")], reviews=[{"name": "승부 배너 앵커 자동 배치", "since": NOW - 20 * HOUR}],
                     features=[{"name": "텐션업 불꽃 상승 연출", "stage": "3 구현", "done": 1, "total": 3}],
                     stalled=[{"name": "초상화 뒤바뀜 — 원인 조사", "days": 2}])
        parts.update(over)
        return wr.build(NOW, **parts)

    def test_my_turn_comes_first_with_count_in_title(self):
        lines = self.build().splitlines()
        self.assertEqual(lines[0], "📋 남은 작업 11:00 — 내 차례 2건")
        self.assertLess(lines.index("🙋 내 차례"), lines.index("🔄 진행 중"))
        self.assertIn("- 결정: 계획 승인: 불꽃 연출", lines)
        self.assertIn("- 검수: 승부 배너 앵커 자동 배치", lines)
        self.assertIn("- 텐션업 불꽃 상승 연출: 3 구현, 카드 1/3", lines)
        self.assertIn("- 초상화 뒤바뀜 — 원인 조사: 2일째 갱신 없음", lines)

    def test_nothing_left_means_no_message(self):
        self.assertEqual(self.build(decisions=[], reviews=[], features=[], stalled=[]), "")

    def test_no_my_turn_section_when_nothing_waits_on_me(self):
        text = self.build(decisions=[], reviews=[])
        self.assertTrue(text.startswith("📋 남은 작업 11:00 — 내 차례 없음"))
        self.assertNotIn("🙋 내 차례", text)

    def test_message_passes_the_slack_format_check(self):
        """티켓 번호·카드 번호를 본문에 넣지 않고, 한 줄에 사실 하나"""
        text = self.build()
        self.assertEqual([g for g in board_cards.text_gaps(text, skip_first=True) if g[2] == "reject"], [])
        self.assertNotRegex(text, r"LRM-\d+")
        self.assertLessEqual(len(text.splitlines()[0].replace("📋", "").strip()), 40)

    def test_long_lists_are_cut_with_a_count(self):
        many = [{"name": f"기능 {i}", "stage": "3 구현", "done": 0, "total": 1} for i in range(12)]
        lines = self.build(features=many).splitlines()
        self.assertIn("- 그 밖에 4건", lines)


class SendRuleTest(unittest.TestCase):
    """언제 보내는가: 내 차례가 있으면 매번, 없으면 내용이 바뀐 때만"""
    CASES = [
        ("내 차례가 있으면 같은 내용이어도 다시 알림", "본문", 2, {"body": "본문"}, True),
        ("내 차례가 없고 내용이 같으면 보내지 않음", "본문", 0, {"body": "본문"}, False),
        ("내 차례가 없어도 내용이 바뀌면 보냄", "새 본문", 0, {"body": "본문"}, True),
        ("남은 것이 없으면 보내지 않음", "", 0, {"body": "본문"}, False),
        ("첫 실행은 보냄", "본문", 0, {}, True),
    ]

    def test_cases(self):
        for name, text, mine, state, want in self.CASES:
            with self.subTest(name):
                self.assertEqual(wr.should_send(text, mine, state), want)

    def test_time_in_title_does_not_count_as_a_change(self):
        a = wr.build(NOW, decisions=[], reviews=[], features=[{"name": "기능", "stage": "3 구현", "done": 0, "total": 1}], stalled=[])
        b = wr.build(NOW + 1800, decisions=[], reviews=[], features=[{"name": "기능", "stage": "3 구현", "done": 0, "total": 1}], stalled=[])
        self.assertNotEqual(a, b)
        self.assertFalse(wr.should_send(b, 0, {"body": wr.body_key(a)}))


class RunTest(unittest.TestCase):
    """보내기: 새 글을 올리고, 앞 리마인드는 한 줄로 줄여 DM 에 옛 목록이 쌓이지 않게 함"""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name
        self.fake = os.path.join(self.dir, "fake_notify.sh")
        with open(self.fake, "w") as f:
            f.write('#!/bin/bash\nprintf \'%s\\n\' "$(printf \'%s\\t\' "$@")" >> "$FAKE_LOG"\n[ -n "${FAKE_FAIL:-}" ] && { echo "거부: 시험" >&2; exit 2; }\necho "ok 1795053700.000100"\n')
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IXUSR)
        self.log = os.path.join(self.dir, "calls.log")
        os.environ["FAKE_LOG"] = self.log
        self.addCleanup(os.environ.pop, "FAKE_LOG", None)
        self.state = os.path.join(self.dir, "state.json")

    def calls(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                return [ln.rstrip("\n").split("\t")[:-1] for ln in f]
        except FileNotFoundError:
            return []

    def test_first_send_posts_and_remembers_the_message(self):
        out = wr.send("📋 남은 작업 11:00 — 내 차례 1건\n\n🙋 내 차례\n- 결정: x", 1, self.fake, self.state, NOW)
        self.assertEqual(out, "sent")
        self.assertEqual(self.calls()[0][:4], ["--tag", "remind", "--kind", "progress"])
        with open(self.state) as f:
            self.assertEqual(json.load(f)["ts"], "1795053700.000100")

    def test_next_send_shrinks_the_previous_message(self):
        wr.send("📋 남은 작업 11:00 — 내 차례 1건\n\n🙋 내 차례\n- 결정: x", 1, self.fake, self.state, NOW)
        wr.send("📋 남은 작업 11:30 — 내 차례 1건\n\n🙋 내 차례\n- 결정: x", 1, self.fake, self.state, NOW + 1800)
        upd = [c for c in self.calls() if "--update" in c]
        self.assertEqual(len(upd), 1)
        self.assertEqual(upd[0][upd[0].index("--update") + 1], "1795053700.000100")
        self.assertIn("11:00", upd[0][-1])

    def test_skipped_round_sends_nothing(self):
        wr.send("본문", 0, self.fake, self.state, NOW)
        n = len(self.calls())
        self.assertEqual(wr.send("본문", 0, self.fake, self.state, NOW + 1800), "skip")
        self.assertEqual(len(self.calls()), n)

    def test_failed_send_keeps_the_old_state_so_next_round_retries(self):
        os.environ["FAKE_FAIL"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_FAIL", None)
        self.assertTrue(wr.send("본문", 1, self.fake, self.state, NOW).startswith("fail"))
        self.assertFalse(os.path.exists(self.state))


if __name__ == "__main__":
    unittest.main()
