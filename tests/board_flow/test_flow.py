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


def answer(did=PLAN, source="slack", parent="1.1", **over):
    """받은 답 한 줄. 기본은 첫 계획 카드(메시지 1.1)에 Slack 버튼으로 1번. source=None 이면 그 칸이 없는 기록(폴러가 쓴 반응·스레드 글)"""
    out = {"type": "button", "decision_id": did, "kind": "decide", "action": "pick", "pick": 1, "source": source, "parent_ts": parent, **over}
    return {k: v for k, v in out.items() if not (k == "source" and v is None)}


SENT_OK = {"decision_id": PLAN, "ts": "1.1", "form_kind": "decide", "options": {"1": "승인", "2": "수정 요청"}}
SENT_AGAIN = {**SENT_OK, "ts": "2.2"}  # 계획을 고쳐 같은 번호로 다시 올린 카드

# (설명, 보낸 기록들, 받은 기록들, 승인으로 보나)
APPROVAL_CASES = [
    ("Slack 버튼으로 1번(승인)", [SENT_OK], [answer()], True),
    ("추천대로(1번이 추천)", [SENT_OK], [answer(action="approve")], True),
    ("추천대로인데 추천이 2번인 카드", [SENT_OK], [answer(action="approve", pick=2)], False),
    ("2번(수정 요청)", [SENT_OK], [answer(pick=2)], False),
    ("답 없음", [SENT_OK], [], False),
    ("다른 결정의 답", [SENT_OK], [answer(did="D-OTHER")], False),
    ("터미널에서 남긴 답은 승인이 아님(코디네이터가 스스로 승인하지 못하게)", [SENT_OK], [answer(source="terminal")], False),
    ("직접 입력 글은 승인이 아님", [SENT_OK], [answer(action="custom", pick=None, text="좋아요")], False),
    ("1번이 '승인'이 아닌 카드는 계획 승인 카드가 아님", [{**SENT_OK, "options": {"1": "보류", "2": "승인"}}], [answer()], False),
    ("보낸 기록이 없으면 승인 아님", [], [answer()], False),
    ("승인한 뒤 수정 요청으로 바꿈(같은 카드의 최근 답이 기준)", [SENT_OK], [answer(), answer(pick=2)], False),
    ("계획을 고쳐 다시 올리면 옛 카드의 승인은 새 계획의 승인이 아님", [SENT_OK, SENT_AGAIN], [answer()], False),
    ("다시 올린 카드에 승인", [SENT_OK, SENT_AGAIN], [answer(), answer(parent="2.2")], True),
    ("다시 올린 카드에 수정 요청(옛 카드의 승인은 남아 있어도)", [SENT_OK, SENT_AGAIN], [answer(), answer(parent="2.2", pick=2)], False),
    ("카드에 단 이모지 반응 ✅(폴러 기록, 출처 칸 없음)", [SENT_OK], [answer(source=None, type="reaction", action="approve")], True),
    ("카드 스레드에 쓴 '1'(폴러 기록, 출처 칸 없음)", [SENT_OK], [answer(source=None, type="thread_reply")], True),
    ("출처를 모르는 버튼 기록은 세지 않음", [SENT_OK], [answer(source=None)], False),
]


INTAKE = "- 목표: 마지막 날 시작해도 결말이 열린다\n- 확인 방법: 마지막 날 세이브로 시작해 그 장면이 열리는 캡처\n- 범위 밖: 다른 결말"


def feature(*labels, drop=(), desc=INTAKE):
    return {**epic(*labels, drop=drop), "description": desc}


# (설명, 기능 카드, 옮기려는 단계, 계획 승인됐나, 막혀야 하나)
STAGE_CASES = [
    ("접수로 되돌리기는 언제나 됨", feature(desc=""), "intake", False, False),
    ("목표·확인 방법 없이 이해로 넘어가기", feature(desc="- 범위 밖: 없음"), "understand", False, True),
    ("확인 방법만 빠짐", feature(desc="- 목표: 결말이 열린다"), "understand", False, True),
    ("칸 이름만 있고 내용이 빔", feature(desc="- 목표:\n- 확인 방법: "), "understand", False, True),
    ("접수를 채우면 이해로", feature(), "understand", False, False),
    ("굵게 쓴 칸 이름·전각 쌍점·붙여 쓴 '확인방법'도 채운 것으로 봄", feature(desc="**목표**: 결말이 열린다\n확인방법：세이브로 시작해 캡처"), "understand", False, False),
    ("크기를 안 정하고 넘어가기", feature(drop=("size:normal",)), "understand", False, True),
    ("설계까지는 승인 없이", feature(), "design", False, False),
    ("보통 크기, 승인 전 구현으로", feature(), "build", False, True),
    ("보통 크기, 승인 뒤 구현으로", feature(), "build", True, False),
    ("승인 전 검증으로 건너뛰기", feature(), "verify", False, True),
    ("작은 일은 접수에서 바로 구현으로", feature("size:small", drop=("size:normal",)), "build", False, False),
    ("검수 단계는 완료 검사가 옮김(손으로 못 옮김)", feature(), "review", True, True),
    ("반영 단계도 손으로 못 옮김", feature(), "ship", True, True),
    ("모르는 단계", feature(), "coding", True, True),
    ("옛 방식 기능도 단계 표시는 할 수 있고 막지 않음", feature(drop=("flow:v1", "size:normal"), desc=""), "build", False, False),
]

REVIEW = "D-REVWLRMSPN"
REVIEW_SENT = {"decision_id": REVIEW, "ts": "1.1", "form_kind": "decide", "options": {"1": "통과", "2": "고칠 점 있음"}}
REVIEW_AGAIN = {**REVIEW_SENT, "ts": "2.2"}

# (설명, 보낸 기록들, 받은 기록들, 통과로 보나)
REVIEW_CASES = [
    ("Slack 버튼으로 1번(통과)", [REVIEW_SENT], [answer(REVIEW)], True),
    ("답 없음", [REVIEW_SENT], [], False),
    ("2번(고칠 점 있음)", [REVIEW_SENT], [answer(REVIEW, pick=2)], False),
    ("통과 뒤 고칠 점으로 바꿈(최근 답이 기준)", [REVIEW_SENT], [answer(REVIEW), answer(REVIEW, pick=2)], False),
    ("다시 올린 카드에는 옛 카드의 통과를 쓰지 않음", [REVIEW_SENT, REVIEW_AGAIN], [answer(REVIEW)], False),
    ("다시 올린 카드에 통과", [REVIEW_SENT, REVIEW_AGAIN], [answer(REVIEW), answer(REVIEW, parent="2.2")], True),
    ("사용자가 아닌 답(터미널)", [REVIEW_SENT], [answer(REVIEW, source="terminal")], False),
    ("1번이 '통과'가 아닌 카드는 검수 카드가 아님", [{**REVIEW_SENT, "options": {"1": "승인", "2": "통과"}}], [answer(REVIEW)], False),
    ("계획 승인 번호의 답은 검수 통과가 아님", [REVIEW_SENT, {**SENT_OK}], [answer(PLAN)], False),
    ("보낸 기록 없음", [], [answer(REVIEW)], False),
]

# 하위 카드에 복사된 기능 표시는 기능 판정에서 무시함. (설명, 카드, 단계 key 또는 None, 크기, 잘못 붙은 표시)
COPIED = ["stage:review", "size:normal", "flow:v1", "review:v1", "kind:fix"]
FEATURE_MARK_CASES = [
    ("기능 카드의 표시는 그대로", epic("stage:review"), "review", "normal", []),
    ("하위 카드의 복사된 표시는 무시", card(labels=COPIED), None, "", COPIED[:4]),
    ("상위 없는 일반 카드도 기능이 아님", card(parent=None, labels=["stage:build", "size:small"]), None, "", ["stage:build", "size:small"]),
    ("표시 없는 하위 카드는 경고 없음", card(labels=["kind:fix"]), None, "", []),
]


class FlowTest(unittest.TestCase):
    def test_child_cards_with_copied_marks_are_not_features(self):
        for name, c, st, size, stray in FEATURE_MARK_CASES:
            with self.subTest(name):
                self.assertEqual(getattr(bf.current(c), "key", None), st)
                self.assertEqual(bf.size_of(c), size)
                self.assertEqual(bf.stray_marks(c), stray)

    def test_copied_flow_mark_on_parent_like_child_does_not_block_claims(self):
        self.assertEqual(bf.claim_block(card(), card(labels=COPIED), lambda did: False), "")

    def test_which_stage_moves_are_blocked(self):
        for name, e, target, approved, want in STAGE_CASES:
            with self.subTest(name):
                self.assertEqual(bool(bf.stage_block(e, target, lambda did: approved)), want)

    def test_stage_can_be_named_by_number_or_korean(self):
        self.assertEqual([bf.stage(x).key for x in ("3", "구현", "build")], ["build"] * 3)

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

    def test_review_decision_id_is_derived_and_does_not_collide_with_plan(self):
        self.assertEqual(bf.review_id("lrm-spn"), REVIEW)
        self.assertNotEqual(bf.review_id("lrm-spn"), bf.plan_id("lrm-spn"))
        long_id = "tutorial-coaching-off-1-2"
        self.assertLessEqual(len(bf.review_id(long_id)) - 2, 16)
        self.assertNotEqual(bf.review_id(long_id), bf.review_id(long_id + "x"))

    def test_what_counts_as_review_pass(self):
        for name, sent, inbox, want in REVIEW_CASES:
            with self.subTest(name), tempfile.TemporaryDirectory(prefix="cc-flow-") as d:
                for fname, rows in (("slack-sent.jsonl", sent), ("slack-inbox.jsonl", inbox)):
                    with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
                        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
                self.assertEqual(bf.review_passed(d, REVIEW), want)

    def test_only_marked_features_need_the_review_card(self):
        with tempfile.TemporaryDirectory(prefix="cc-flow-") as d:
            why = bf.review_block(epic(bf.REVIEW_LABEL), d)
            self.assertIn(REVIEW, why)
            self.assertIn("--user-ok-typed", why)
            self.assertEqual(bf.review_block(epic(), d), "")  # 표시 없는 옛 기능은 막지 않음

    def test_stage_names_are_in_order_and_review_keeps_its_old_label(self):
        self.assertEqual([s.key for s in bf.STAGES], ["intake", "understand", "design", "build", "verify", "review", "ship"])
        self.assertEqual(bf.stage("review").label, "stage:review")  # 검수 대기 상한이 이 이름을 셈
        self.assertIsNone(bf.stage("없는 단계"))


if __name__ == "__main__":
    unittest.main()
