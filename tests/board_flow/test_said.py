"""사용자가 코디네이터 창에 직접 친 글 읽기(board_said)와 그 글로 하는 승인·건너뛰기(board_flow.typed_approval) 시험.
세션 기록은 임시 폴더에 만든 가짜 파일만 씀."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "board", "lib"))
import board_flow as bf  # noqa: E402
import board_said as said  # noqa: E402

WS = "/w/coord-feat-x-hub"
T0 = 1795050000.0


def iso(at):
    import datetime
    return datetime.datetime.fromtimestamp(at, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(text, at, **over):
    return dict({"type": "user", "timestamp": iso(at), "cwd": WS, "message": {"role": "user", "content": text}}, **over)


class TypedTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name
        self.proj = os.path.join(self.dir, "projects", "-w-coord-feat-x-hub")
        os.makedirs(self.proj)

    def write(self, *recs, name="s1.jsonl"):
        with open(os.path.join(self.proj, name), "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def typed(self, since=0.0):
        return [t for _, t in said.typed(WS, since, dirs=[self.dir])]

    def test_only_what_the_user_typed_counts(self):
        """경우를 더할 때는 아래 목록에 한 줄"""
        cases = [
            ("직접 친 글", user("높이 조금만 더 낮추자", T0 + 1), True),
            ("작업 도중에 친 글(대기열)", {"type": "attachment", "timestamp": iso(T0 + 2), "attachment": {"type": "queued_command", "prompt": "스킵하고 진행"}}, True),
            ("도구 결과", user([{"type": "tool_result", "tool_use_id": "t", "content": "ok"}], T0 + 3), False),
            ("시스템 알림", user("<system-reminder>알림</system-reminder>", T0 + 4), False),
            ("세션을 띄울 때 붙여 넣은 역할 글", user("<pasted_content id=\"1\">역할: 기능 코디네이터</pasted_content>", T0 + 5), False),
            ("작업자 메시지 도착 알림", user("You have 1 orchestration message. Run `orca orchestration check --run r`.", T0 + 6), False),
            ("답 전달기의 입력창 알림", user("Slack 답 도착: bd show lrm-94k", T0 + 7), False),
            ("메타 줄", user("내부 기록", T0 + 8, isMeta=True), False),
            ("하위 에이전트 줄", user("하위 지시", T0 + 9, isSidechain=True), False),
            ("코디네이터가 쓴 글", {"type": "assistant", "timestamp": iso(T0 + 10), "message": {"role": "assistant", "content": [{"type": "text", "text": "승인"}]}}, False),
        ]
        self.write(*[rec for _, rec, _ in cases])
        want = [(rec["attachment"]["prompt"] if rec["type"] == "attachment" else rec["message"]["content"]) for _, rec, keep in cases if keep]
        self.assertEqual(self.typed(), want)

    def test_since_and_order_across_session_files(self):
        self.write(user("둘째", T0 + 20), name="b.jsonl")
        self.write(user("첫째", T0 + 10), user("셋째", T0 + 30), name="a.jsonl")
        self.assertEqual(self.typed(), ["첫째", "둘째", "셋째"])
        self.assertEqual(self.typed(since=T0 + 15), ["둘째", "셋째"])

    def test_same_text_recorded_twice_is_one(self):
        self.write({"type": "attachment", "timestamp": iso(T0 + 1), "attachment": {"type": "queued_command", "prompt": "승인"}}, user("승인", T0 + 20))
        self.assertEqual(self.typed(), ["승인"])

    def test_no_transcript_means_nothing_typed(self):
        self.assertEqual(said.typed("/w/none", 0.0, dirs=[self.dir]), [])


class TypedApprovalTest(unittest.TestCase):
    """코디네이터 창에 직접 친 승인·건너뛰기. 경우를 더할 때는 CASES 에 한 줄: (이름, 친 글들 [(카드 올린 뒤 몇 초, 글)], 기대)"""
    DID = "D-PLANTE"
    CARD_AT = T0 + 100
    CASES = [
        ("'승인'이라고 치면 승인", [(10, "승인")], True),
        ("'스킵하고 진행'도 승인", [(10, "스킵하고 진행")], True),
        ("'승인 건너뛰고 진행해'도 승인", [(10, "승인 건너뛰고 진행해")], True),
        ("'계획 승인한다'도 승인", [(10, "계획 승인한다")], True),
        ("영문 skip 도 승인", [(10, "Skip")], True),
        ("'진행해'·'ㄱㄱ'만으로는 승인이 아님(다른 얘기 끝에 붙는 말)", [(10, "작업 빠르게 진행 ㄱㄱ"), (20, "진행해")], False),
        ("글 중간의 승인은 세지 않음", [(10, "이거 승인 전에 하나만 고치자")], False),
        ("계획 카드를 올리기 전에 친 승인은 그 카드의 승인이 아님", [(-10, "승인")], False),
        ("아무 글도 없으면 승인 아님", [], False),
    ]

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.hub = d.name
        self.epic = {"id": "t-e", "created_at": iso(T0), "description": f"- 목표: x\n- 기능 코디네이터 허브: {WS}\n"}

    def post(self, at=None):
        with open(os.path.join(self.hub, bf.SENT_NAME), "a", encoding="utf-8") as f:
            f.write(json.dumps({"decision_id": self.DID, "ts": f"{at or self.CARD_AT:.6f}", "at": at or self.CARD_AT, "form_kind": "decide",
                                "options": {"1": "승인", "2": "수정 요청"}}, ensure_ascii=False) + "\n")

    def ok(self, texts, epic=None):
        typed = lambda ws, since: [(self.CARD_AT + dt, t) for dt, t in texts if self.CARD_AT + dt > since and ws == WS]  # noqa: E731
        return bf.typed_approval(self.hub, self.DID, epic or self.epic, typed=typed)

    def test_cases(self):
        self.post()
        for name, texts, want in self.CASES:
            with self.subTest(name):
                self.assertEqual(self.ok(texts), want)

    def test_reposted_plan_needs_a_new_typed_approval(self):
        self.post()
        self.assertTrue(self.ok([(10, "승인")]))
        self.post(at=self.CARD_AT + 60)
        self.assertFalse(self.ok([(10, "승인")]), "다시 올린 카드보다 먼저 친 승인")
        self.assertTrue(self.ok([(10, "승인"), (70, "승인")]))

    def test_without_any_plan_card_a_typed_skip_after_opening_counts(self):
        self.assertTrue(self.ok([(10, "스킵하고 바로 구현해")]))
        self.assertFalse(self.ok([(-200, "스킵")]), "기능을 열기 전에 친 글")

    def test_later_change_request_on_slack_overrides_an_earlier_typed_approval(self):
        self.post()
        with open(os.path.join(self.hub, bf.INBOX_NAME), "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "button", "decision_id": self.DID, "parent_ts": f"{self.CARD_AT:.6f}", "action": "pick", "pick": 2,
                                "source": "slack", "received_at": self.CARD_AT + 50}) + "\n")
        self.assertFalse(self.ok([(10, "승인")]))
        self.assertTrue(self.ok([(10, "승인"), (60, "승인")]))

    def test_feature_without_coordinator_workspace_line_cannot_be_approved_this_way(self):
        self.post()
        self.assertFalse(self.ok([(10, "승인")], epic={"id": "t-e", "created_at": iso(T0), "description": "- 목표: x"}))

    def test_approval_lookup_accepts_either_way(self):
        self.post()
        self.assertFalse(bf.approval(self.hub, self.epic)(self.DID))


if __name__ == "__main__":
    unittest.main()
