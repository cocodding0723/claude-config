"""check-text 결정 번호 언급 판정 시험: 열린 카드 링크 요청·닫힌 결정 '대기' 경고·카드 없음 무변경·decide 카드 제외. 임시 폴더만 씀."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import hub_report  # noqa: E402

CLI = os.path.join(SCRIPTS, "hub_report.py")
OPEN, CLOSED, DONE = "1700000001.000001", "1700000002.000001", "1700000003.000001"


class MentionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        d = pathlib.Path(self.dir.name)
        cards = [(OPEN, "D-0A01"), (CLOSED, "D-0A02"), (DONE, "D-0A03"), ("1700000004.000001", "D-0A02")]  # D-0A02 는 다시 올린 카드(열림)가 가장 최근
        (d / "slack-sent.jsonl").write_text("".join(json.dumps({"ts": ts, "channel": "D1", "form_kind": "decide", "decision_id": did, "options": {"1": "A"}}) + "\n" for ts, did in cards))
        inbox = [{"type": "button", "parent_ts": CLOSED, "decision_id": "D-0A02", "kind": "decide", "option_text": "직접 입력: 둘 다", "received_at": 1700000000, "source": "slack"},
                 {"type": "button", "parent_ts": DONE, "decision_id": "D-0A03", "kind": "decide", "option_text": "1. A 안", "received_at": 1700000000, "source": "slack"}]
        (d / "slack-inbox.jsonl").write_text("".join(json.dumps(r) + "\n" for r in inbox))

    def check(self, text, *extra):
        return subprocess.run([sys.executable, "-I", CLI, "check-text", "--hub-dir", self.dir.name, *extra], input=text, capture_output=True, text=True)

    def test_open_card_link(self):
        links, warns = hub_report.mention_check("결정 D-0A01 선택 대기", self.dir.name)
        self.assertEqual((links, warns), ([(OPEN, "D-0A01")], []))

    def test_multiple_one_each_deduped(self):
        links, _ = hub_report.mention_check("D-0A01 대기\nD-0A01 또\n참고: D-0A02 눌러", self.dir.name)
        self.assertEqual([d for _, d in links], ["D-0A01", "D-0A02"])  # 가장 최근 카드가 열린 D-0A02 도 링크

    def test_closed_with_wait_warns(self):
        links, warns = hub_report.mention_check("- D-0A03 선택 대기 중", self.dir.name)
        self.assertEqual(links, [])
        self.assertEqual(len(warns), 1)
        self.assertRegex(warns[0], r"^D-0A03 는 \d\d-\d\d 에 답함\(1\. A 안\) — 대기로 쓰지 말 것$")

    def test_closed_without_wait_silent(self):
        self.assertEqual(hub_report.mention_check("D-0A03 은 A 안으로 확정됨", self.dir.name), ([], []))
        self.assertEqual(hub_report.mention_check("D-0A03 선택됨", self.dir.name), ([], []))

    def test_wait_on_other_line_not_flagged(self):
        self.assertEqual(hub_report.mention_check("D-0A03 확정\n다른 일 대기", self.dir.name), ([], []))

    def test_no_card_unchanged(self):
        self.assertEqual(hub_report.mention_check("D-0Z99 선택 대기", self.dir.name), ([], []))
        self.assertEqual(hub_report.mention_check("번호 없는 글 대기", self.dir.name), ([], []))
        self.assertEqual(hub_report.mention_check("XD-0A01 와 D-0A01x", self.dir.name), ([], []))

    def test_missing_records_never_block(self):
        self.assertEqual(hub_report.mention_check("D-0A01 대기", os.path.join(self.dir.name, "none")), ([], []))

    def test_cli_output_lines(self):
        r = self.check("D-0A01 선택 대기\nD-0A03 눌러 주세요")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], f"link\t{OPEN}\tD-0A01")
        self.assertTrue(lines[1].startswith("warn\tD-0A03 는 "))

    def test_cli_decide_kind_skipped(self):
        r = self.check("D-0A01 선택 대기\nD-0A03 눌러", "--kind", "decide")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_cli_path_problem_still_exits_2(self):
        r = self.check("보고서 /tmp/x/a.pdf 참고 D-0A01")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
