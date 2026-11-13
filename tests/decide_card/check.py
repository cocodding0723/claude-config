"""decide_card 규칙 단위 시험 + 자동 결정 카드 통과 시험. 작업 폴더는 hubs/scripts."""
import json
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
