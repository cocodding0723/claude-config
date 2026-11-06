import unittest
from unittest.mock import patch
from types import SimpleNamespace

import hud
import hud_view


class HudTest(unittest.TestCase):
    def test_invalid_envelopes_are_reportable_errors(self):
        for raw in ['[]', '{"ok":false,"error":null}', '{"ok":true,"result":null}',
                    '{"ok":true,"result":{"tasks":{}}}',
                    '{"ok":true,"result":{"tasks":[null]}}']:
            with self.subTest(raw=raw), patch.object(hud.subprocess, "run", return_value=
                    SimpleNamespace(stdout=raw, returncode=0)):
                with self.assertRaises(RuntimeError):
                    hud.query("orca", ["task-list", "--run", "run_test"])

    def test_git_metadata_does_not_refresh_index_or_inspect_working_files(self):
        with patch.object(hud.subprocess, "run", return_value=
                SimpleNamespace(stdout="", returncode=0)) as run:
            result = hud.git_metadata("/project")
        calls = [call.args[0] for call in run.call_args_list]
        self.assertTrue(all("--no-optional-locks" in command for command in calls))
        self.assertTrue(any("--cached" in command for command in calls))
        self.assertTrue(all("status" not in command for command in calls))
        self.assertIn("Stage 파일 변경: 0건", result)

    def test_implementation_completion_is_not_qa_acceptance(self):
        snapshot = {"tasks": {"tasks": [
            {"id": "t1", "task_title": "[impl] 편성", "status": "completed"},
            {"id": "t2", "task_title": "[qa] 경쟁모드", "status": "blocked"},
        ]}, "gates": {"gates": []}}
        result = hud.render(snapshot, [], "run_test", 0)
        self.assertIn("Task 종료", result)
        self.assertIn("동작·화면 QA: Task 0/1 종료", result)
        self.assertIn("커밋 확인: 단계 미등록", result)
        self.assertIn("[blocked]", result)

    def test_unavailable_data_does_not_become_zero_tasks_or_dead_worker(self):
        result = hud.render({}, ["runtime 조회 실패"], "run_test", 0)
        self.assertIn("Task 수·진척: 확인 불가", result)
        self.assertIn("결정 Gate: 확인 불가", result)
        self.assertNotIn("Task 종료 [", result)
        self.assertIn("종료·수용 판정에 사용하지 않음", result)

    def test_old_observation_preserves_unverifiable_verdict(self):
        worker = {"projection": {"provider": {"id": "claude", "model": "test"},
                                  "liveness": {"verdict": "unverifiable", "observedAt": 1000}}}
        self.assertIn("unverifiable · 99초 전", hud.worker_detail(worker, 100))

    def test_completed_task_keeps_dispatch_model_and_terminal_accounting(self):
        snapshot = {"tasks": {"tasks": [
            {"id": "t1", "task_title": "[research] 조사", "status": "completed", "dispatch_id": None},
        ]}, "workers": {"workers": [
            {"taskId": "t1", "dispatchId": "ctx_old", "dispatchStatus": "failed",
             "terminalState": "released", "projection": {"outcome": "failed"}},
            {"taskId": "t1", "dispatchId": "ctx_new", "dispatchStatus": "completed",
             "terminalState": "active", "projection": {"outcome": "succeeded",
                "provider": {"id": "codex", "model": "gpt-6.1-sol"}}},
        ]}}
        result = hud.render(snapshot, [], "run_test", 0)
        self.assertIn("ctx_old", result)
        self.assertIn("ctx_new", result)
        self.assertIn("codex/gpt-6.1-sol", result)
        self.assertIn("터미널 active", result)

    def test_untrusted_text_cannot_inject_terminal_controls(self):
        result = hud.safe("hello\x1b[2J\n\u202e bad")
        self.assertNotIn("\x1b", result)
        self.assertNotIn("\n", result)
        self.assertNotIn("\u202e", result)

    def test_short_terminal_keeps_header_and_gate_visible(self):
        snapshot = {"run": {"run": {"objective": "중앙 HUD"}},
                    "tasks": {"tasks": [
                        {"id": str(i), "task_title": "[impl] 작업", "status": "dispatched"}
                        for i in range(12)
                    ]}, "gates": {"gates": [{"status": "pending", "question": "기획 결정 필요"}]}}
        result = hud.render(snapshot, [], "run_test", 0, height=19)
        self.assertLess(len(result.splitlines()), 19)
        self.assertIn("ORCA COORDINATOR HUD", result)
        self.assertIn("기획 결정 필요", result)
        self.assertIn("아래 항목 생략", result)

    def test_worker_pagination_is_explicitly_scoped_and_complete(self):
        calls = []

        def query(executable, argv):
            calls.append(argv)
            if argv[0] == "worker-list":
                if "--cursor" in argv:
                    return {"workers": [{"dispatchId": "second"}], "page": {"hasMore": False}}
                return {"workers": [{"dispatchId": "first"}],
                        "page": {"hasMore": True, "nextCursor": "opaque"}}
            return {}

        with patch.object(hud, "query", side_effect=query):
            snapshot, errors = hud.collect("orca", "run_test")
        self.assertFalse(errors)
        self.assertEqual(len(snapshot["workers"]["workers"]), 2)
        self.assertTrue(all("run_test" in argv for argv in calls))
        self.assertTrue(all(argv[0] in {"run-show", "task-list", "worker-list", "gate-list"}
                            for argv in calls))


class CompactHudTest(unittest.TestCase):
    def snapshot(self, count=1):
        return {"run": {"run": {"objective": "중앙 화면"}},
                "tasks": {"tasks": [
                    {"id": f"task_{i}", "dispatch_id": f"ctx_{i}",
                     "task_title": "[impl] 작업", "status": "dispatched"}
                    for i in range(count)]},
                "workers": {"workers": []}, "gates": {"gates": []}}

    def render(self, snapshot, errors=None, **options):
        return hud_view.render_compact(snapshot, errors or [], 1000, **options)

    def test_small_terminal_prioritizes_error_gate_and_current_over_completed(self):
        snapshot = self.snapshot(12)
        snapshot["tasks"]["tasks"].insert(0, {
            "id": "old", "task_title": "옛 작업", "status": "completed"})
        snapshot["gates"]["gates"] = [{"status": "pending", "question": "결정 필요"}]
        snapshot["tasks"]["tasks"][-1]["status"] = "blocked"
        for width in (65, 80):
            result = self.render(snapshot, ["조회 실패"], width=width, height=19, color=True)
            plain = hud_view._SGR.sub("", result)
            self.assertLess(len(result.splitlines()), 19)
            self.assertTrue(all(hud_view.cell_width(line) < width for line in result.splitlines()))
            self.assertLess(plain.index("조회 오류"), plain.index("[차단]"))
            self.assertLess(plain.index("질문 결정 필요"), plain.index("[차단]"))
            self.assertLess(plain.index("[차단]"), plain.index("종료 1건"))
            self.assertIn("더보기", plain)
            self.assertIn("ORCA HUD", plain)

    def test_impl_completion_never_claims_feature_or_qa_acceptance(self):
        snapshot = self.snapshot()
        snapshot["tasks"]["tasks"][0]["status"] = "completed"
        result = self.render(snapshot)
        self.assertIn("Task 종료 [##########] 1/1", result)
        self.assertIn("기능 수용·QA 통과 별도 확인", result)
        self.assertIn("구현 1/1", result)
        self.assertIn("미등록 4:", result)
        self.assertIn("독립 리뷰", result)
        snapshot["tasks"]["tasks"].append({"task_title": "[qa] 화면", "status": "blocked"})
        self.assertIn("동작·화면 QA 0/1", self.render(snapshot))
        self.assertIn("[차단]", self.render(snapshot))

    def test_missing_replies_do_not_become_zero_success_or_unregistered(self):
        result = self.render({})
        for text in ("작업 수 확인 불가", "Task 종료: 확인 불가", "결정 Gate 확인 불가",
                     "워커 조회 불가", "단계 등록 여부: 확인 불가"):
            self.assertIn(text, result)
        self.assertNotIn("0/0", result)
        self.assertNotIn("미등록", result)
        self.assertNotIn("현재 작업 없음", result)

    def test_only_current_dispatch_model_with_separate_observation_age(self):
        snapshot = self.snapshot()
        snapshot["workers"]["workers"] = [
            {"taskId": "task_0", "dispatchId": "ctx_old", "projection": {
                "provider": {"model": "old-retry"}}},
            {"taskId": "task_0", "dispatchId": "ctx_0", "projection": {
                "provider": {"model": "current-model"},
                "liveness": {"verdict": "live", "observedAt": 900000,
                             "reason": "raw reason"}}}]
        result = self.render(snapshot)
        self.assertIn("current-model · 관찰 오래됨 · 100초 전", result)
        self.assertIn("조회 ", result)
        for text in ("old-retry", "ctx_", "task_", "raw reason", "dead"):
            self.assertNotIn(text, result)
        snapshot["tasks"]["tasks"][0]["dispatch_id"] = None
        result = self.render(snapshot)
        self.assertNotIn("current-model", result)
        self.assertIn("현재 워커 관찰 없음", result)

    def test_unassigned_waiting_tasks_do_not_warn_but_dispatched_tasks_do(self):
        snapshot = self.snapshot()
        task = snapshot["tasks"]["tasks"][0]
        task["dispatch_id"] = None
        for status in ("pending", "ready"):
            task["status"] = status
            self.assertEqual(hud_view.observation(task, [], False, 1000), ("배정 대기", False))
            result = self.render(snapshot, color=True)
            self.assertIn("\x1b[37m배정 대기\x1b[0m", result)
            self.assertNotIn("현재 워커 관찰 없음", result)
        for status, dispatch in (("dispatched", None), ("dispatched", "ctx_0"), ("ready", "ctx_0")):
            task.update(status=status, dispatch_id=dispatch)
            self.assertTrue(hud_view.observation(task, [], True, 1000)[1])
            self.assertIn("\x1b[1;93m모델 미확인 · 현재 워커 관찰 없음\x1b[0m",
                          self.render(snapshot, color=True))

    def test_current_unknown_future_and_missing_observations_are_explicit(self):
        snapshot = self.snapshot()
        worker = {"taskId": "task_0", "dispatchId": "ctx_0", "projection": {}}
        snapshot["workers"]["workers"] = [worker]
        for observation in ({}, {"verdict": "unverifiable", "observedAt": 999000},
                            {"verdict": "live", "observedAt": 1001000}):
            worker["projection"]["liveness"] = observation
            self.assertIn("관찰 확인 불가", self.render(snapshot))
        worker["projection"]["liveness"] = {"verdict": "live", "observedAt": 999000}
        self.assertIn("관찰 정상 · 1초 전", self.render(snapshot))

    def test_completed_released_attempt_does_not_warn_or_dump_history(self):
        snapshot = self.snapshot()
        snapshot["tasks"]["tasks"][0]["status"] = "completed"
        snapshot["workers"]["workers"] = [{"taskId": "task_0", "terminalState": "released",
            "projection": {"attention": {"requiresAction": True},
                           "nextAction": {"kind": "release"}}}]
        result = self.render(snapshot)
        self.assertIn("워커 현재 관찰: 확인 불가 0건", result)
        self.assertNotIn("조치 필요", result)
        self.assertNotIn("자원 회수 대기", result)
        snapshot["workers"]["workers"][0]["terminalState"] = "reclaimable"
        self.assertIn("자원 회수 대기 1건", self.render(snapshot))

    def test_untrusted_controls_removed_before_ansi_and_cjk_cropping(self):
        attack = "\x1b[2J\x1b]52;c;bad\x07\x9b31m\n\r\t\u202e"
        snapshot = self.snapshot()
        snapshot["run"]["run"]["objective"] = attack + "한글e\u0301" * 60
        snapshot["gates"]["gates"] = [{"question": attack + "질문" * 80}]
        result = self.render(snapshot, [attack + "오류"], width=65, height=19, color=True)
        plain = hud_view._SGR.sub("", result)
        for c in ("\x1b", "\x9b", "\u202e", "\r", "\t"):
            self.assertNotIn(c, plain)
        self.assertIn("\x1b[1;97;44m", result)
        self.assertIn("더보기", plain)
        self.assertEqual(hud_view.cell_width("한e\u0301"), 3)
        self.assertEqual(hud_view.fit("한e\u0301", 3), "한e\u0301")
        self.assertTrue(all(hud_view.cell_width(line) < 65 for line in result.splitlines()))

    def test_color_policy_and_main_compact_default_details_opt_in(self):
        for mode, tty, no_color, expected in [
            ("auto", True, "", True), ("auto", True, "1", False),
            ("auto", False, "", False), ("always", False, "1", True),
            ("never", True, "", False)]:
            self.assertEqual(hud_view.use_color(mode, tty, no_color), expected)
        for flags, renderer in [([], "render_compact"), (["--details"], "render")]:
            with patch.object(hud.sys, "argv", ["hud.py", "--run", "run_test", *flags]), \
                 patch.object(hud, "collect", return_value=(self.snapshot(), [])), \
                 patch.object(hud, renderer, return_value="view") as draw, \
                 patch("builtins.print"), patch.object(hud, "git_metadata") as git:
                self.assertEqual(hud.main(), 0)
                draw.assert_called_once()
                git.assert_not_called()


if __name__ == "__main__":
    unittest.main()
