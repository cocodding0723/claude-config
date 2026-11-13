"""decide_card 규칙 단위 시험 + 자동 결정 카드 통과 시험. 작업 폴더는 hubs/scripts."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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


class ReuseTest(unittest.TestCase):
    """결정 번호 재사용 차단: 접수(hub_report.make_report)와 직접 호출 검사(check-decide)가 같은 함수를 씀"""

    def setUp(self):
        import hub_report
        self.hr = hub_report
        self.dir = tempfile.mkdtemp(prefix="cc-reuse.")
        self.old = (hub_report.HUB_DIR, os.environ.get("LRM_REPORT_OUTBOX"))
        hub_report.HUB_DIR = self.dir
        os.environ.pop("LRM_REPORT_OUTBOX", None)
        self.addCleanup(self.restore)

    def restore(self):
        self.hr.HUB_DIR = self.old[0]
        if self.old[1] is not None:
            os.environ["LRM_REPORT_OUTBOX"] = self.old[1]

    def put(self, name, rows):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def add(self, title, did="D-SPINE"):
        return self.hr.make_report("data-design", "없음", "decide", f"{title}\n{BG}\n{TERMS}", [], OPTS, "1", did, "보통", None, True)

    def test_other_question_rejected_with_old_title_and_new_number_hint(self):
        self.put("slack-sent.jsonl", [{"decision_id": "D-SPINE", "kind": "decide", "title": ":비서-선택해주세요: 공주영 스파인 앞머리 분절 방식"}])
        with self.assertRaises(self.hr.ReportError) as cm:
            self.add("스파인 설정 원본을 빌드 밖으로 옮길까?")
        self.assertIn("공주영 스파인 앞머리 분절 방식", str(cm.exception))
        self.assertIn("새 번호 쓰기(예: D-SPINE2)", str(cm.exception))

    def test_same_question_passes_even_with_prefix_and_spacing(self):
        self.put("slack-sent.jsonl", [{"decision_id": "D-SPINE", "kind": "decide", "title": ":비서-선택해주세요: 스파인 설정 원본을  빌드 밖으로 옮길까?"}])
        self.assertEqual(self.add("스파인 설정 원본을 빌드 밖으로 옮길까?")["decision_id"], "D-SPINE")  # 재전송·충돌 재질문

    def test_outbox_record_also_counts(self):
        self.put("hub-reports.jsonl", [{"decision_id": "D-SPINE", "kind": "decide", "text": "첫 질문\n배경: x"}])
        with self.assertRaises(self.hr.ReportError):
            self.add("다른 질문")
        self.assertEqual(self.add("첫 질문")["decision_id"], "D-SPINE")

    def test_new_number_and_no_record_pass(self):
        self.put("slack-sent.jsonl", [{"decision_id": "D-SPINE", "kind": "decide", "title": "옛 질문"}])
        self.assertEqual(self.add("다른 질문", "D-SPINE2")["decision_id"], "D-SPINE2")

    def test_unreadable_record_warns_and_passes(self):
        os.makedirs(os.path.join(self.dir, "slack-sent.jsonl"))  # 파일 대신 폴더 → 읽기 실패
        why, warn = dc.reuse_problem("D-SPINE", "질문", [(os.path.join(self.dir, "slack-sent.jsonl"), lambda r: r.get("title"))])
        self.assertEqual(why, "")
        self.assertIn("재사용 검사 못 함", warn)
        self.assertEqual(self.add("질문")["decision_id"], "D-SPINE")

    def test_check_decide_cli_uses_same_rule(self):
        import subprocess
        self.put("slack-sent.jsonl", [{"decision_id": "D-SPINE", "kind": "decide", "title": "옛 질문"}])
        run = lambda title: subprocess.run([sys.executable, "-I", "hub_report.py", "check-decide", "--options", OPTS, "--decision-id", "D-SPINE"],  # noqa: E731
                                           input=f"{title}\n{BG}\n{TERMS}", capture_output=True, text=True, env={**os.environ, "LRM_HUB_DIR": self.dir})
        bad, ok = run("새 질문"), run("옛 질문")
        self.assertEqual((bad.returncode, ok.returncode), (2, 0))
        self.assertIn("이미 쓰임(옛 질문)", bad.stderr + bad.stdout)


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


class LocalQaTest(unittest.TestCase):
    """D-RDY<PR번호> 카드만 '로컬 QA:' 줄 필수(사용자 11-13 D-LOCALQA)"""
    HEAD = f"PR 열기\n{BG}\n{TERMS}"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cc-localqa.")
        self.old = os.environ.get("LRM_LOCAL_GATE_DIR")
        os.environ["LRM_LOCAL_GATE_DIR"] = os.path.join(self.tmp, "store")
        os.makedirs(os.environ["LRM_LOCAL_GATE_DIR"])
        self.video = os.path.join(self.tmp, "shop.mp4")
        open(self.video, "w").close()
        self.sha = "1a2b3c4d5e6f"
        open(os.path.join(os.environ["LRM_LOCAL_GATE_DIR"], self.sha + "0" * 28 + ".json"), "w").close()

        self.head = self.sha + "0" * 28  # PR head 로 가정할 전체 해시(기록 파일과 같은 커밋)
        self.gh = mock.patch.object(dc, "pr_head", side_effect=lambda n: self.head)
        self.gh.start()

    def tearDown(self):
        self.gh.stop()
        if self.old is None:
            os.environ.pop("LRM_LOCAL_GATE_DIR", None)
        else:
            os.environ["LRM_LOCAL_GATE_DIR"] = self.old

    def check(self, qa, did="D-RDY746", attached=()):
        body = self.HEAD + (f"\n로컬 QA: {qa}" if qa is not None else "")
        return dc.problem(body, OPTS, did, attached)

    def test_missing_line_rejected_with_example(self):
        why = self.check(None)
        for word in ("로컬 QA", "예:", "조치"):
            self.assertIn(word, why)

    def test_recording_path_passes(self):
        self.assertEqual(self.check(f"EditMode 12 통과 — 장면 녹화 {self.video}"), "")

    def test_missing_recording_file_rejected(self):
        self.assertIn("녹화·캡처", self.check(f"EditMode 12 통과 — 녹화 {self.tmp}/none.mp4"))

    def test_command_result_form_required(self):
        self.assertIn("명령 — 결과", self.check(f"녹화 {self.video}"))

    def test_attached_recording_replaces_path(self):
        self.assertEqual(self.check("EditMode 12 통과 — 첨부 녹화 참고", attached=["/x/rec.mp4"]), "")
        self.assertIn("녹화·캡처", self.check("EditMode 12 통과 — 첨부 없음", attached=["/x/doc.html"]))

    def test_not_applicable_with_gate_record_passes(self):
        self.assertEqual(self.check(f"해당 없음(도구·CI·문서) — 게이트 통과 커밋 {self.sha[:8]}"), "")

    def test_not_applicable_without_gate_record_rejected(self):
        self.head = "deadbeef" + "0" * 32  # head 와는 일치하지만 게이트 기록이 없는 경우
        self.assertIn("기록 없음", self.check("해당 없음(도구·CI·문서) — 게이트 통과 커밋 deadbeef"))
        self.assertIn("해시", self.check("해당 없음(도구·CI·문서)"))

    def test_hash_must_match_pr_head(self):
        other = "deadbeef" + "0" * 32
        open(os.path.join(os.environ["LRM_LOCAL_GATE_DIR"], other + ".json"), "w").close()  # 기록은 있으나 PR head 아님
        why = self.check("해당 없음(도구·CI·문서) — 게이트 통과 커밋 deadbeef")
        self.assertIn("head", why)
        self.assertIn(self.sha[:8], why)
        self.assertEqual(self.check(f"해당 없음(도구·CI·문서) — 게이트 통과 커밋 {self.sha[:8]}"), "")

    def test_gh_failure_warns_and_falls_back_to_prefix(self):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with mock.patch.object(dc, "pr_head", return_value=None), redirect_stderr(buf):
            self.assertEqual(self.check(f"해당 없음(도구·CI·문서) — 게이트 통과 커밋 {self.sha[:8]}"), "")
            self.assertIn("기록 없음", self.check("해당 없음(도구·CI·문서) — 게이트 통과 커밋 cafebabe"))
        self.assertIn("경고", buf.getvalue())

    def test_other_cards_unaffected(self):
        self.assertEqual(self.check(None, did="D-0001"), "")
        self.assertEqual(self.check(None, did=None), "")
        self.assertEqual(self.check(None, did="D-RDYX"), "")

    def test_make_report_and_cli_apply_rule(self):
        import hub_report
        with self.assertRaises(hub_report.ReportError):
            hub_report.make_report("hub-x", "없음", "decide", self.HEAD, [], OPTS, "1", "D-RDY746", "보통", None, True)
        hub_report.make_report("hub-x", "없음", "decide", self.HEAD + f"\n로컬 QA: 시험 통과 — 녹화 {self.video}", [self.video], OPTS, "1", "D-RDY746", "보통", None, True)
        import subprocess
        base = [sys.executable, "-I", "hub_report.py", "check-decide", "--options", OPTS, "--decision-id", "D-RDY747"]
        bad = subprocess.run(base, input=self.HEAD, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2, bad.stderr)
        good = subprocess.run(base + ["--attached", "/x/rec.mp4"], input=self.HEAD + "\n로컬 QA: 시험 통과 — 첨부", capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
