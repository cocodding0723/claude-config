"""건너뛰기 판정 시험(board_flow): 창에 친 글을 건너뛰기로 읽는 경우, 건너뛴 기능의 구현 착수, 지난 항목 판정.
경우를 더할 때는 각 CASES 에 한 줄. 도구가 카드에 적는 것은 tests/board_skip/run.sh"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "board", "lib"))
import board_flow as bf  # noqa: E402

BOTH = ("understand", "plan")


def epic(*labels, eid="lrm-x"):
    return {"id": eid, "issue_type": "epic", "labels": ["flow:v1", *labels], "description": "- 목표: 가\n- 확인 방법: 나"}


class TypedSkipTest(unittest.TestCase):
    """코디네이터 창에 친 글 → (건너뛰는 항목, 계획 승인으로 세는가)"""
    CASES = [
        ("급함 한 마디는 준비 둘 다", "급함", BOTH, True),
        ("느낌표·마침표가 붙어도 같음", "급함!", BOTH, True),
        ("급함으로 진행", "급함으로 진행", BOTH, True),
        ("급함 뒤에 다른 말이 붙으면 건너뛰기가 아님", "급함 아님, 천천히 해", (), False),
        ("급하다는 말이 문장 속에 있으면 건너뛰기가 아님", "이거 좀 급함", (), False),
        ("항목 없는 스킵은 지금처럼 계획 승인 대기만 넘김", "스킵", (), True),
        ("스킵하고 진행도 계획 승인", "스킵하고 진행", (), True),
        ("스킵 이해는 이해만 건너뛰고 계획 승인은 아님", "스킵 이해", ("understand",), False),
        ("스킵 계획은 계획을 건너뜀", "스킵 계획", ("plan",), True),
        ("항목 둘", "스킵 이해 계획", BOTH, True),
        ("쉼표로 이어도 됨", "스킵 이해, 계획", BOTH, True),
        ("영문 이름도 받음", "skip plan", ("plan",), True),
        ("묶음 이름을 스킵 뒤에 써도 됨", "스킵 급함", BOTH, True),
        ("건너뛸 수 없는 것을 건너뛰라는 글은 건너뛰기도 계획 승인도 아님", "스킵 다른모델검수", (), False),
        ("건너뛸 수 없는 것이 섞이면 전부 받지 않음", "스킵 계획 녹화", (), False),
        ("승인은 계획 승인", "승인", (), True),
        ("진행해는 아무것도 아님", "진행해", (), False),
    ]

    def test_cases(self):
        for name, text, items, plan_ok in self.CASES:
            with self.subTest(name):
                self.assertEqual(bf.typed_skips(text), items)
                self.assertEqual(bf.typed_plan_ok(text), plan_ok)

    def test_refused_names_are_reported(self):
        self.assertEqual(bf.refused_skips("스킵 다른모델검수 녹화"), ("다른모델검수", "녹화"))
        self.assertEqual(bf.refused_skips("스킵 계획"), ())

    def test_skip_texts_are_not_written_to_the_instruction_ledger(self):
        for text, want in (("급함", True), ("스킵 이해", True), ("승인", True), ("급함 아님, 천천히 해", False), ("높이 낮춰", False)):
            with self.subTest(text):
                self.assertEqual(bf.is_control(text), want)


class SkipKeysTest(unittest.TestCase):
    CASES = [
        ("한글 이름", ["이해"], (("understand",), ())),
        ("묶음", ["급함"], (BOTH, ())),
        ("겹치면 한 번, 순서는 단계 순", ["계획", "급함", "이해"], (BOTH, ())),
        ("모르는 낱말은 따로", ["계획", "검수"], (("plan",), ("검수",))),
        ("빈 목록", [], ((), ())),
    ]

    def test_cases(self):
        for name, words, want in self.CASES:
            with self.subTest(name):
                self.assertEqual(bf.skip_keys(words), want)


class GateTest(unittest.TestCase):
    """건너뛴 기능의 구현 착수. 승인 조회는 늘 '승인 없음'"""
    TASK = {"id": "lrm-x.1", "issue_type": "task", "status": "open"}

    def never(self, _did):
        return False

    def test_plan_skip_lets_a_normal_feature_claim_without_approval(self):
        self.assertEqual(bf.claim_block(self.TASK, epic("size:normal", "skip:plan"), self.never), "")

    def test_without_plan_skip_a_normal_feature_still_needs_approval(self):
        self.assertIn("계획 승인이 없음", bf.claim_block(self.TASK, epic("size:normal"), self.never))

    def test_understand_skip_alone_does_not_skip_the_plan(self):
        self.assertIn("계획 승인이 없음", bf.claim_block(self.TASK, epic("size:normal", "skip:understand"), self.never))

    def test_block_message_tells_the_urgent_way(self):
        self.assertIn("급함", bf.claim_block(self.TASK, epic("size:large"), self.never))

    def test_plan_skip_lets_the_stage_move_to_build_from_intake(self):
        self.assertEqual(bf.stage_block(epic("size:normal", "stage:intake", "skip:understand", "skip:plan"), "build", self.never), "")

    def test_skip_does_not_waive_intake_fields_or_size(self):
        no_goal = dict(epic("size:normal", "skip:plan"), description="")
        self.assertIn("접수 칸", bf.stage_block(no_goal, "build", self.never))
        self.assertIn("크기", bf.stage_block(epic("skip:plan"), "build", self.never))

    def test_review_and_ship_cannot_be_reached_by_skipping(self):
        self.assertIn("손으로 옮기지 않음", bf.stage_block(epic("size:normal", "skip:plan"), "review", self.never))


class PastTest(unittest.TestCase):
    """이미 지난 단계의 항목인가"""
    CASES = [
        ("접수 중이면 둘 다 아직", "stage:intake", "understand", False),
        ("이해 중이면 이해는 아직", "stage:understand", "understand", False),
        ("설계에 들어섰으면 이해는 지남", "stage:design", "understand", True),
        ("설계 중이면 계획은 아직", "stage:design", "plan", False),
        ("구현에 들어섰으면 계획은 지남", "stage:build", "plan", True),
        ("검증 중이면 둘 다 지남", "stage:verify", "understand", True),
    ]

    def test_cases(self):
        for name, label, item, want in self.CASES:
            with self.subTest(name):
                self.assertEqual(bf.skip_past(epic("size:normal", label), item), want)

    def test_no_stage_label_means_nothing_is_past(self):
        self.assertFalse(bf.skip_past(epic("size:normal"), "plan"))

    def test_skips_are_read_from_labels_in_stage_order(self):
        self.assertEqual(bf.skips(epic("skip:plan", "skip:understand", "skip:unknown")), BOTH)


if __name__ == "__main__":
    unittest.main()
