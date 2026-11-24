"""쉬운 말 검사 시험: 바꾸기·수동 보고 거부 문구·백틱 예외·자동 보고 줄 빼기·결정 카드 양식 유지. 임시 폴더만 씀"""
import os
import json
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO, "hubs", "scripts", "hub_report.py")
sys.path.insert(0, os.path.dirname(SCRIPT))
import hub_report  # noqa: E402


def make(text, kind="info", **kw):
    return hub_report.make_report("ui-layer", "C0012", kind, text, [], **kw)


class PlainWordsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.box = os.path.join(self.dir.name, "box.jsonl")

    def add(self, text, *extra):
        env = {**os.environ, "LRM_REPORT_OUTBOX": self.box}
        return subprocess.run([sys.executable, "-I", SCRIPT, "add", *extra, "--hub", "ui-layer", "--task", "C0012", "--kind", "info", "--text", text],
                              env=env, capture_output=True, text=True)

    def test_rewrite_applied_for_every_kind(self):
        self.assertEqual(make("지시 처리 #12: 우편함 — 정렬 수정\n워커가 EditMode 시험 통과")["text"],
                         "요청 12 우편함: 정렬 수정\n작업자가 에디터 시험 통과")
        self.assertEqual(make("지시 #3 받음", kind="done")["text"], "요청 3 받음")
        self.assertEqual(make("허브·코디네이터 확인")["text"], "허브·코디네이터 확인")

    def test_cli_rejects_with_word_and_example(self):
        r = self.add("- local-gate 통과(--no-unity)")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("'local-gate' → '로컬 검사'", r.stderr)
        self.assertIn("'--no-unity'", r.stderr)
        self.assertIn(" — ", r.stderr)
        self.assertFalse(os.path.exists(self.box))

    def test_cli_auto_drops_lines_instead_of_rejecting(self):
        r = self.add("주간 집계 완료\n- local-gate 통과(--no-unity)", "--auto")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(open(self.box).read().splitlines()[-1])
        self.assertEqual(rec["text"], "주간 집계 완료")
        self.assertEqual(rec.get("plain_dropped"), 1)

    def test_every_default_reject_pattern(self):
        for bad in ("--force 로 해제", "board-done 통과", "bd close x", "evidence:compile", "ctx_abc 확인", "term_1 확인",
                    "unclaim 함", "heartbeat 끊김", "PR 생성 (head )", "브랜치 = . 확인"):
            with self.assertRaises(hub_report.ReportError, msg=bad):
                make(bad)

    def test_backtick_command_allowed_and_not_rewritten(self):
        r = self.add("- 다시 돌리려면 `board-run --once` 실행")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(make("`bd update 워커` 실행")["text"], "`bd update 워커` 실행")
        self.assertEqual(make("```\nbd close x\n```")["text"], "```\nbd close x\n```")

    def test_reference_line_keeps_internal_ids(self):
        self.assertIn("참고: ctx_1 · term_2", make("정렬 수정\n참고: ctx_1 · term_2")["text"])

    def test_auto_path_drops_lines_only(self):
        rep = make("정렬 수정 완료\nheartbeat 끊김 복구\n워커 2명", strict=False)
        self.assertEqual(rep["text"], "정렬 수정 완료\n작업자 2명")
        self.assertEqual(rep["plain_dropped"], 1)
        self.assertNotIn("plain_dropped", make("정렬 수정 완료", strict=False))
        with self.assertRaises(hub_report.ReportError):
            make("heartbeat 끊김", strict=False)

    def test_decide_format_kept(self):
        rep = hub_report.make_report("feat-x", "없음", "decide",
                                     "워커 계획 승인\n배경: 워커가 EditMode 시험 계획서를 올리고 승인을 기다림\n용어: 없음", [],
                                     options="1=승인 => 구현 시작,2=수정 요청 => 계획을 고침", recommend="1",
                                     decision_id="D-PLAINW", no_doc=True)
        self.assertEqual(rep["text"], "작업자 계획 승인\n배경: 작업자가 에디터 시험 계획서를 올리고 승인을 기다림\n용어: 없음")
        self.assertEqual(rep["options"], "1=승인 => 구현 시작,2=수정 요청 => 계획을 고침")


if __name__ == "__main__":
    unittest.main()
