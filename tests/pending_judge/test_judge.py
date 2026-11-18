"""대기 목록 판정(pending_judge) 시험. 경우는 표 한 줄씩이라, 새 경우는 표에 줄을 더하면 됨. 파일·Slack 을 쓰지 않음(설정 표만 임시 폴더)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hubs", "scripts"))
import pending_judge as pj  # noqa: E402

NOW = 1_800_000_000
HOUR = 3600
CARD = {"ts": "1.1", "thread_ts": "", "at": NOW, "title": "매점 진열을 어디서 관리할까", "form_kind": "decide",
        "decision_id": "D-STORECTRL", "options": {"1": "시트", "2": "코드"}}


def rec(**over):
    """기본은 방금 보낸 번호 카드. 바꿀 칸만 넘김. 값이 None 이면 그 칸을 없앰"""
    out = {**CARD, **over}
    return {k: v for k, v in out.items() if v is not None}


RULES = pj.Rules(max_age_h=48.0, auto=pj.compile_auto([r"D-ST[0-9a-f]{10}", r"D-RS[0-9a-f]{10}"]))

# (설명, 보낸 기록, 결정인가)
DECISION_CASES = [
    ("번호 카드", rec(), True),
    ("결정 번호가 없는 선택지 카드", rec(decision_id=None), True),
    ("선택지 없는 옛 결정 글(반응으로만 닫히던 것)", rec(options=None, decision_id=None), False),
    ("제목에 '결정'이 든 진행 보고", rec(form_kind="progress", options=None, decision_id=None, title="Slack 결정 카드를 작업 스레드 안으로"), False),
    ("종류 표시 없는 옛 글, 제목에 '결정'", rec(form_kind=None, options=None, decision_id=None, title="결정: 첫째"), False),
    ("작업 스레드 안에 올라간 결정 카드(작업 하나 = 스레드 하나)", rec(thread_ts="0.9"), True),
    ("스레드 안의 일반 답글", rec(thread_ts="0.9", form_kind="progress", options=None, decision_id=None), False),
    ("메시지 번호 없는 기록", rec(ts=None), False),
]

# (설명, 결정 번호, 자동 알림인가)
AUTO_CASES = [
    ("멈춘 워커 감시(머리말 + 16진수 10자리)", "D-ST40a3edd6b4", True),
    ("재시작 감시", "D-RS0123456789", True),
    ("머리말만 같은 사람 결정", "D-STOREDAY", False),
    ("머리말만 같은 사람 결정 2", "D-STORECTRL", False),
    ("16진수가 9자리", "D-ST40a3edd6b", False),
    ("작업판 결정", "D-Blrm5ic", False),
    ("번호 없음", None, False),
]

# (설명, 보낸 시각, 오래됐나)
OLD_CASES = [
    ("방금", NOW, False),
    ("47시간 전", NOW - 47 * HOUR, False),
    ("49시간 전", NOW - 49 * HOUR, True),
    ("보낸 시각 없음(오래됐다고 단정하지 않음)", None, False),
    ("보낸 시각이 숫자가 아님", "어제", False),
]


THREAD_CARD = rec(ts="2.2", thread_ts="2.1", decision_id="D-TH1")


def reply(text, parent="2.1", kind="thread_reply"):
    return {"type": kind, "ts": "2.9", "parent_ts": parent, "text": text}


# 작업 스레드에 결정 번호로 시작하는 글 = 그 카드에 쓴 글 답. (설명, 카드, 받은 글, 글 답인가)
WRITTEN_CASES = [
    ("결정 번호로 시작하는 스레드 글", THREAD_CARD, reply("D-TH1 2번으로 갑니다"), True),
    ("'결정' 낱말을 앞에 붙인 글", THREAD_CARD, reply("결정 D-TH1: 가 먼저"), True),
    ("번호만 적은 글", THREAD_CARD, reply("D-TH1"), True),
    ("번호가 글 중간에 있는 글(다른 얘기 중 언급)", THREAD_CARD, reply("그건 D-TH1 이랑 상관없어"), False),
    ("머리만 같은 다른 번호", THREAD_CARD, reply("D-TH12 로 하자"), False),
    ("번호 없는 일반 피드백", THREAD_CARD, reply("색을 좀 더 진하게"), False),
    ("다른 스레드의 글", THREAD_CARD, reply("D-TH1 2번", parent="9.9"), False),
    ("스레드 글이 아닌 기록(반응)", THREAD_CARD, reply("D-TH1 2번", kind="reaction"), False),
    ("맨 위 카드는 해당 없음(자기 스레드의 글은 다른 길로 기록됨)", rec(ts="2.2", decision_id="D-TH1"), reply("D-TH1 2번", parent="2.2"), False),
]


class JudgeTest(unittest.TestCase):
    def test_which_thread_replies_are_written_answers(self):
        for name, card, got, want in WRITTEN_CASES:
            with self.subTest(name):
                self.assertEqual(pj.written_answer(card, got), want)

    def test_which_sent_records_are_decisions(self):
        for name, s, want in DECISION_CASES:
            with self.subTest(name):
                self.assertEqual(pj.is_decision(s), want)

    def test_which_decision_ids_are_automatic_alerts(self):
        for name, did, want in AUTO_CASES:
            with self.subTest(name):
                self.assertEqual(pj.is_auto(rec(decision_id=did), RULES), want)

    def test_which_decisions_are_too_old_to_remind(self):
        for name, at, want in OLD_CASES:
            with self.subTest(name):
                self.assertEqual(pj.is_old(rec(at=at), RULES, NOW), want)

    def test_no_age_limit_means_nothing_is_old(self):
        self.assertFalse(pj.is_old(rec(at=NOW - 999 * HOUR), pj.Rules(None, ()), NOW))


class RulesFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cc-pending-")
        self.addCleanup(self.tmp.cleanup)
        self.warned = []

    def load(self, text=None, newline="\n"):
        if text is not None:
            with open(os.path.join(self.tmp.name, pj.RULES_NAME), "w", encoding="utf-8", newline="") as f:
                f.write(text.replace("\n", newline))
        return pj.load_rules([os.path.join(self.tmp.name, "없는 폴더"), self.tmp.name], warn=self.warned.append)

    def test_reads_age_and_patterns_and_skips_comments(self):
        r = self.load("# 설명\n\nmax_age_hours\t48\nexclude_pattern\tD-ST[0-9a-f]{10}\t멈춘 워커\n")
        self.assertEqual((r.max_age_h, len(r.auto), self.warned), (48.0, 1, []))
        self.assertTrue(pj.is_auto(rec(decision_id="D-ST40a3edd6b4"), r))

    def test_windows_line_endings_do_not_break_patterns(self):
        r = self.load("max_age_hours\t48\nexclude_pattern\tD-ST[0-9a-f]{10}\t멈춘 워커\n", newline="\r\n")
        self.assertTrue(pj.is_auto(rec(decision_id="D-ST40a3edd6b4"), r))
        self.assertEqual(r.max_age_h, 48.0)

    def test_bad_values_warn_and_are_ignored_instead_of_crashing(self):
        r = self.load("max_age_hours\t이틀\nexclude_pattern\tD-ST[\nexclude_pattern\tD-RS[0-9a-f]{10}\n")
        self.assertEqual((r.max_age_h, len(r.auto), len(self.warned)), (None, 1, 2))

    def test_missing_table_means_no_limits_and_one_warning(self):
        r = self.load()
        self.assertEqual((r.max_age_h, r.auto, len(self.warned)), (None, (), 1))

    def test_repo_table_excludes_watchers_but_not_store_decisions(self):
        """저장소의 실제 설정 표: 감시기 카드는 빠지고, 머리말만 같은 사람 결정은 남음"""
        r = pj.load_rules([os.path.join(HERE, "..", "..", "hubs")], warn=self.warned.append)
        self.assertEqual(self.warned, [])
        self.assertEqual([pj.is_auto(rec(decision_id=d), r) for d in ("D-ST40a3edd6b4", "D-RS0123456789", "D-STOREDAY")], [True, True, False])
        self.assertEqual(r.max_age_h, 48.0)


if __name__ == "__main__":
    unittest.main()
