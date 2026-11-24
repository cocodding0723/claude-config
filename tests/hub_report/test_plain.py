"""쉬운 말 검사 시험: 바꾸기·감싸기(줄 안 뺌)·수동 보고 경고·깨진 표·URL 보존·결정 카드 양식 유지. 임시 폴더만 씀"""
import os
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

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

    def test_cli_wraps_and_warns_instead_of_rejecting(self):
        r = self.add("- local-gate 통과(--no-unity)")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("'local-gate' → '로컬 검사'", r.stderr)
        self.assertIn(" — ", r.stderr)
        rec = json.loads(open(self.box).read().splitlines()[-1])
        self.assertEqual(rec["text"], "- `local-gate` 통과(`--no-unity`)")
        self.assertNotIn("plain_dropped", rec)

    def test_cli_auto_wraps_silently(self):
        r = self.add("주간 집계 완료\n- local-gate 통과", "--auto")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        rec = json.loads(open(self.box).read().splitlines()[-1])
        self.assertEqual(rec["text"], "주간 집계 완료\n- `local-gate` 통과")

    def test_every_default_reject_pattern_wrapped(self):
        for bad, want in (("--force 로 해제", "`--force` 로 해제"), ("board-done 통과", "`board-done` 통과"),
                          ("bd close x", "`bd close` x"), ("evidence:compile", "`evidence:`compile"),
                          ("ctx_abc 확인", "`ctx_`abc 확인"), ("term_1 확인", "`term_`1 확인"), ("unclaim 함", "`unclaim` 함"),
                          ("heartbeat 끊김", "`heartbeat` 끊김"), ("PR 생성 (head )", "PR 생성 `(head )`"), ("브랜치 = . 확인", "브랜치 `= .` 확인")):
            self.assertEqual(make(bad, strict=False)["text"], want, bad)

    def test_one_line_warn_sent(self):
        for kind in ("warn", "block", "info"):
            self.assertEqual(make("heartbeat 끊김", kind=kind, strict=False)["text"], "`heartbeat` 끊김", kind)

    def test_feature_open_text_with_tool_named_branch(self):
        out = subprocess.run(["bash", "-c", 'NAME="board-done 정리"; BRANCH="feat/board-done-fix"; TICKET=""; '
                              'echo "기능 시작: \\`${NAME//\\`/}\\` (${TICKET:+$TICKET, }통합 브랜치 \\`$BRANCH\\`)"'],
                             capture_output=True, text=True).stdout.strip()
        r = self.add(out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        rec = json.loads(open(self.box).read().splitlines()[-1])
        self.assertIn("`feat/board-done-fix`", rec["text"])

    def test_broken_table_row_skipped(self):
        table = os.path.join(self.dir.name, "plain.tsv")
        with open(table, "w", encoding="utf-8") as f:
            f.write("reject\t(unclosed\t예시\nrewrite\t워커\t작업자\n")
        env = {**os.environ, "LRM_PLAIN_WORDS": table, "LRM_REPORT_OUTBOX": self.box}
        r = subprocess.run([sys.executable, "-I", SCRIPT, "add", "--hub", "ui-layer", "--task", "C0012", "--kind", "info", "--text", "워커 확인"],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1행 정규식 오류", r.stderr)
        self.assertEqual(json.loads(open(self.box).read().splitlines()[-1])["text"], "작업자 확인")

    def test_missing_table_sends_unchecked(self):
        env = {**os.environ, "LRM_PLAIN_WORDS": os.path.join(self.dir.name, "none.tsv"), "LRM_REPORT_OUTBOX": self.box}
        with unittest.mock.patch.dict(os.environ, env), unittest.mock.patch.object(hub_report, "plain_words_path",
                                                                                   side_effect=hub_report.ReportError("없음")):
            self.assertEqual(make("워커 확인")["text"], "워커 확인")

    def test_url_and_path_not_rewritten(self):
        self.assertEqual(make("https://x.io/Tests/EditMode 와 Assets/Tests/EditMode 확인, EditMode 통과")["text"],
                         "https://x.io/Tests/EditMode 와 Assets/Tests/EditMode 확인, 에디터 시험 통과")

    def test_backtick_command_allowed_and_not_rewritten(self):
        r = self.add("- 다시 돌리려면 `board-run --once` 실행")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(make("`bd update 워커` 실행")["text"], "`bd update 워커` 실행")
        self.assertEqual(make("```\nbd close x\n```")["text"], "```\nbd close x\n```")

    def test_reference_line_keeps_internal_ids(self):
        self.assertIn("참고: ctx_1 · term_2", make("정렬 수정\n참고: ctx_1 · term_2")["text"])

    def test_decide_warning_line_kept_and_wrapped(self):
        rep = hub_report.make_report("feat-x", "없음", "decide",
                                     "강제 반영 승인\n배경: 원격과 갈라져 반영이 막힘\n용어: 없음\n주의: --force 로 원격 커밋 3개가 사라짐", [],
                                     options="1=승인 => 강제 반영,2=보류 => 그대로 둠", recommend="2",
                                     decision_id="D-PLAINF", no_doc=True, strict=False)
        self.assertEqual(rep["text"].splitlines()[3], "주의: `--force` 로 원격 커밋 3개가 사라짐")
        self.assertEqual(len(rep["text"].splitlines()), 4)

    def test_decide_format_kept(self):
        rep = hub_report.make_report("feat-x", "없음", "decide",
                                     "워커 계획 승인\n배경: 워커가 EditMode 시험 계획서를 올리고 승인을 기다림\n용어: 없음", [],
                                     options="1=승인 => 구현 시작,2=수정 요청 => 계획을 고침", recommend="1",
                                     decision_id="D-PLAINW", no_doc=True)
        self.assertEqual(rep["text"], "작업자 계획 승인\n배경: 작업자가 에디터 시험 계획서를 올리고 승인을 기다림\n용어: 없음")
        self.assertEqual(rep["options"], "1=승인 => 구현 시작,2=수정 요청 => 계획을 고침")


if __name__ == "__main__":
    unittest.main()
