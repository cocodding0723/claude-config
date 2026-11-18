"""작업 흐름 규칙(board_flow) 시험. 경우는 표 한 줄씩. 작업판·Slack 을 쓰지 않음(받은 기록은 임시 파일).
규칙: 새 흐름을 쓰는 기능(label flow:v1)은 크기를 정해야 하고, 보통·큼이면 계획 승인(작업 스레드 버튼 답) 뒤에만 구현 카드를 집음."""
import hashlib
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "board", "lib"))
import board_flow as bf  # noqa: E402

EPIC = {"id": "lrm-spn", "issue_type": "epic", "labels": ["feature:mail", "flow:v1", "size:normal"]}
PLAN = "D-PLANLRMSPN"


def epic(*labels, drop=()):
    return {**EPIC, "labels": [x for x in EPIC["labels"] if x not in drop] + list(labels)}


def card(**over):
    return {"id": "lrm-spn.1", "issue_type": "task", "status": "open", "parent": "lrm-spn", **over}


# (설명, 집으려는 카드, 그 기능 카드, 계획 승인됐나, 막혀야 하나)
GATE_CASES = [
    ("보통 크기, 계획 승인 전", card(), epic(), False, True),
    ("보통 크기, 계획 승인됨", card(), epic(), True, False),
    ("큰 일, 계획 승인 전", card(), epic("size:large", drop=("size:normal",)), False, True),
    ("작은 일은 계획 승인 없이 구현", card(), epic("size:small", drop=("size:normal",)), False, False),
    ("크기를 안 정했으면 접수부터", card(), epic(drop=("size:normal",)), True, True),
    ("옛 방식 기능(flow:v1 없음)은 막지 않음", card(), epic(drop=("flow:v1",)), False, False),
    ("기능에 속하지 않은 카드는 막지 않음", card(parent=None), None, False, False),
    ("기능 카드 자신을 집는 것은 막지 않음(코디네이터가 맡음)", {**EPIC, "status": "open"}, epic(), False, False),
    ("이미 집은 카드를 다시 집는 것은 막지 않음", card(status="in_progress"), epic(), False, False),
]

# (설명, 기능 카드 id, 계획 승인 결정 번호)
PLAN_ID_CASES = [
    ("작업판 카드 id 의 글자·숫자를 대문자로", "lrm-spn", "D-PLANLRMSPN"),
    ("점이 든 하위 번호", "lrm-q5o.21", "D-PLANLRMQ5O21"),
    ("긴 id 는 앞 6자 + 지문 6자(잘라서 다른 기능과 번호가 겹치지 않게)", "tutorial-coaching-off-1-2",
     "D-PLANTUTORI" + hashlib.sha1(b"tutorial-coaching-off-1-2").hexdigest()[:6].upper()),
]


def answer(did=PLAN, source="slack", **over):
    return {"type": "button", "decision_id": did, "kind": "decide", "action": "pick", "pick": 1, "source": source, **over}


SENT_OK = {"decision_id": PLAN, "form_kind": "decide", "options": {"1": "승인", "2": "수정 요청"}}

# (설명, 보낸 기록들, 받은 기록들, 승인으로 보나)
APPROVAL_CASES = [
    ("Slack 버튼으로 1번(승인)", [SENT_OK], [answer()], True),
    ("추천대로(1번이 추천)", [SENT_OK], [answer(action="approve")], True),
    ("2번(수정 요청)", [SENT_OK], [answer(pick=2)], False),
    ("답 없음", [SENT_OK], [], False),
    ("다른 결정의 답", [SENT_OK], [answer(did="D-OTHER")], False),
    ("터미널에서 남긴 답은 승인이 아님(코디네이터가 스스로 승인하지 못하게)", [SENT_OK], [answer(source="terminal")], False),
    ("직접 입력 글은 승인이 아님", [SENT_OK], [answer(action="custom", pick=None, text="좋아요")], False),
    ("1번이 '승인'이 아닌 카드는 계획 승인 카드가 아님", [{**SENT_OK, "options": {"1": "보류", "2": "승인"}}], [answer()], False),
    ("보낸 기록이 없으면 승인 아님", [], [answer()], False),
    ("나중에 고친 계획으로 다시 올린 카드의 답이 기준", [SENT_OK, SENT_OK], [answer(), answer(pick=2)], False),
]


class FlowTest(unittest.TestCase):
    def test_which_cards_are_blocked_until_the_plan_is_approved(self):
        for name, c, e, approved, want in GATE_CASES:
            with self.subTest(name):
                self.assertEqual(bool(bf.claim_block(c, e, lambda did: approved)), want)

    def test_block_message_says_what_to_do_next(self):
        self.assertIn(PLAN, bf.claim_block(card(), epic(), lambda did: False))
        self.assertIn("size:", bf.claim_block(card(), epic(drop=("size:normal",)), lambda did: True))

    def test_plan_decision_id_is_derived_from_the_feature_card(self):
        for name, cid, want in PLAN_ID_CASES:
            with self.subTest(name):
                self.assertEqual(bf.plan_id(cid), want)
                self.assertLessEqual(len(want) - 2, 16)

    def test_what_counts_as_plan_approval(self):
        for name, sent, inbox, want in APPROVAL_CASES:
            with self.subTest(name), tempfile.TemporaryDirectory(prefix="cc-flow-") as d:
                for fname, rows in (("slack-sent.jsonl", sent), ("slack-inbox.jsonl", inbox)):
                    with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
                        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows) + "깨진 줄\n")
                self.assertEqual(bf.plan_approved(d, PLAN), want)

    def test_missing_records_mean_not_approved(self):
        with tempfile.TemporaryDirectory(prefix="cc-flow-") as d:
            self.assertFalse(bf.plan_approved(d, PLAN))

    def test_stage_names_are_in_order_and_review_keeps_its_old_label(self):
        self.assertEqual([s.key for s in bf.STAGES], ["intake", "understand", "design", "build", "verify", "review", "ship"])
        self.assertEqual(bf.stage("review").label, "stage:review")  # 검수 대기 상한이 이 이름을 셈
        self.assertIsNone(bf.stage("없는 단계"))


if __name__ == "__main__":
    unittest.main()
