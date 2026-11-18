"""줄 가독성 검사 시험: ' / ' 이은 줄 거부·오탐 방지(URL·경로·코드·날짜·분수·따옴표)·가운뎃점 경고·설정 한 곳"""
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO, "hubs", "scripts", "hub_report.py")
sys.path.insert(0, os.path.dirname(SCRIPT))
import board_cards  # noqa: E402


class TextGapsTest(unittest.TestCase):
    def rejects(self, text, skip_first=False):
        return [g for g in board_cards.text_gaps(text, skip_first) if g[2] == "reject"]

    def test_slash_join_rejected(self):
        self.assertEqual(self.rejects("- 무엇: 가 / 나"), [(1, board_cards.WHY_SLASH, "reject")])

    def test_no_false_positive_inside_url_path_code_date(self):
        for ok in ("주소 https://x.test/a/b 와 ~/Documents/a/b 경로", "경로 /tmp/a/b.html 확인", "11/17 마감, 5/10 진행",
                   "명령 `a / b` 실행", "```\nx / y\n```", "진행 2 / 3 완료", '버튼 "이어하기 / 새로 시작" 표시', "A/B 시험"):
            self.assertEqual(self.rejects(ok), [], ok)

    def test_fixed_forms_skipped(self):
        for ok in ("| 가 / 나 | 다 |", "👉 답: 1 / 2", ":one: 가 / 나", "참고: lrm-q5o / 허브"):
            self.assertEqual(self.rejects("제목\n" + ok), [], ok)

    def test_title_skipped_only_when_asked(self):
        self.assertEqual(self.rejects("가 / 나\n- 본문", True), [])
        self.assertTrue(self.rejects("가 / 나\n- 본문", False))

    def test_dot_is_warn_not_reject(self):
        gaps = board_cards.text_gaps("- 차단 5종(AI 흔적·Debug.Log·Addressables 데이터·서브모듈·로컬 JSON) 점검")
        self.assertEqual([g[2] for g in gaps], ["warn"])

    def test_message_has_cause_action_and_example(self):
        text = "- 무엇: 시각 변경 / 왜: 놓침"
        msg = board_cards.gap_message(self.rejects(text), text)
        self.assertIn("1번째 줄", msg)
        self.assertIn("줄을 나눠", msg)
        self.assertIn("'- 무엇: 시각 변경' 줄과 '- 왜: 놓침' 줄로", msg)

    def test_level_is_one_setting(self):
        old = dict(board_cards.LINE_LEVEL)
        self.addCleanup(board_cards.LINE_LEVEL.update, old)
        board_cards.LINE_LEVEL[board_cards.WHY_SLASH] = "warn"
        self.assertEqual(self.rejects("- 가 / 나"), [])


class ReportAddTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.box = os.path.join(self.dir.name, "box.jsonl")

    def add(self, text, kind="info"):
        env = {**os.environ, "LRM_REPORT_OUTBOX": self.box}
        return subprocess.run([sys.executable, "-I", SCRIPT, "add", "--hub", "ui-layer", "--task", "C0012", "--kind", kind, "--text", text],
                              env=env, capture_output=True, text=True)

    def test_slash_join_rejected_with_exit_2_and_nothing_written(self):
        r = self.add("- 무엇: 가 / 나")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("한 줄에 사실 여러 개", r.stderr)
        self.assertFalse(os.path.exists(self.box))

    def test_clean_report_accepted(self):
        r = self.add("- 무엇: 가\n- 왜: 나\n참고: https://x.test/a/b")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dot_list_warns_but_accepted(self):
        r = self.add("- 항목: 가나다라마바사 · 아자차카타파하 · 거너더러머버서 · 어저처커터퍼허")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("경고:", r.stderr)

    def test_repeat_is_safe(self):
        self.assertEqual(self.add("- 무엇: 가 / 나").returncode, 2)
        self.assertEqual(self.add("- 무엇: 가 / 나").returncode, 2)


if __name__ == "__main__":
    unittest.main()
