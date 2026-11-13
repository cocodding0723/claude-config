"""decide_card 규칙 단위 시험 + 자동 결정 카드 통과 시험. 작업 폴더는 hubs/scripts."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.getcwd())
os.environ.setdefault("LRM_HUB_DIR", tempfile.mkdtemp(prefix="cc-decidecard."))  # 모듈 import 가 실제 작업 폴더를 건드리지 않게
import decide_card as dc  # noqa: E402

BG, TERMS = "배경: 알림 개편 중이라 시각을 하나로 맞추려 함", "용어: 푸시 — 앱 밖 알림"
OPTS = "1=9시 => 일찍 도착,2=10시 => 늦게 도착"


class RuleTest(unittest.TestCase):
    def test_complete_card_passes(self):
        self.assertEqual(dc.problem(f"제목\n{BG}\n{TERMS}", OPTS), "")
        self.assertEqual(dc.problem(f"제목\n- {BG}\n• {TERMS}", OPTS), "")  # 불릿 머리표 허용

    def test_missing_parts_named_with_fix_and_example(self):
        why = dc.problem("제목", OPTS)
        for word in ("배경", "용어", "조치", "예:"):
            self.assertIn(word, why)

    def test_legacy_options_without_result_rejected(self):
        self.assertIn("선택지 결과(1번, 2번)", dc.problem(f"제목\n{BG}\n{TERMS}", "1=9시,2=10시"))

    def test_choices_card_skips_result_check(self):
        self.assertEqual(dc.problem(f"제목\n{BG}\n{TERMS}", None), "")

    def test_no_terms_is_written_as_none(self):
        self.assertEqual(dc.problem(f"제목\n{BG}\n용어: 없음", OPTS), "")

    def test_options_split_keeps_comma_inside_text(self):
        self.assertEqual(dc.options("1=가, 나 => 결과, 둘,2=다 => 라"), [("1", "가, 나", "결과, 둘"), ("2", "다", "라")])

    def test_take_lines_moves_terms_and_reason(self):
        rest, terms, why = dc.take_lines(f"제목\n{BG}\n- {TERMS}\n이유: 열람률")
        self.assertEqual((terms, why), (f"- {TERMS}", "열람률"))
        self.assertNotIn("용어", rest)
        self.assertIn("배경", rest)


class AutoCardTest(unittest.TestCase):
    """저장소 안 자동 카드가 필수 항목을 채워 거부되지 않음"""

    def test_restart_watch_card(self):
        import restart_watch as rw
        text = rw.card_text({"startedAt": "10:00"}, [{"slug": "ui", "name": "UI", "tasks": ["작업"]}])
        self.assertEqual(dc.problem(text, rw.RESUME_OPTIONS), "")

    def test_stall_watch_card(self):
        import stall_watch as sw
        item = {"slug": "ui", "dispatch": "d1", "ws": "w1", "title": "t", "kind": "stalled", "last": None}
        text, _ = sw.build([item], {"ui": "UI"}, {})
        self.assertEqual(dc.problem(text, sw.STALL_OPTIONS), "")

    def test_hub_split_card(self):
        import hub_split as hs
        import hub_report
        for kind in ("split", "merge"):
            pl = hs.Plan(kind, "parent", "child", "자식", "머리")
            text = hs.card_text(pl, ["근거"])
            self.assertEqual(dc.problem(text, hs.APPROVE_OPTIONS), "", kind)
            hub_report.make_report("hub-x", "없음", "decide", text, [], hs.APPROVE_OPTIONS, "1", "D-0001", "보통", None, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
