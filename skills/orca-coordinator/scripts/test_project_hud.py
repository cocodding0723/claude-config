import copy
import io
import subprocess
import unittest
from types import SimpleNamespace
from threading import Lock
from unittest.mock import patch

import hud_view
import project_hud as hud
from project_view import render


REPO = "86e8d506-a3a1-4b05-8219-6229d180f9fb"
PROJECT = "github:basakansoft/loverevolutionmaker"


def worker(task="task", dispatch="ctx", repo=REPO):
    return {"taskId": task, "dispatchId": dispatch, "resource": {"worktreeId": repo + "::/deleted"},
            "projection": {"host": {"id": "local"}, "workspace": {"id": repo + "::/deleted"},
                           "provider": {"model": "observed-model"},
                           "liveness": {"verdict": "live", "observedAt": 100000}}}


class ProjectHudTest(unittest.TestCase):
    def setUp(self):
        self.clock = 0
        self.runs = [{"id": "run_legacy_local", "coordinator_handle": "central", "objective": "active"},
                     {"id": "run_done", "coordinator_handle": "central", "objective": "old"},
                     {"id": "run_other", "coordinator_handle": "other", "objective": PROJECT},
                     {"id": "run_unknown", "objective": PROJECT}]
        self.tasks = {"run_legacy_local": [{"id": "task", "dispatch_id": "ctx", "status": "dispatched",
                                            "task_title": "[impl] 한글 작업"}],
                      "run_done": [{"id": "done", "status": "completed"}]}
        self.workers = {"run_legacy_local": [worker()], "run_other": [worker(repo="other-repo")]}
        self.gates, self.fail, self.calls = {}, set(), []
        self.collector = hud.ProjectHud("orca", "id:" + REPO, PROJECT)
        self.query_patch = patch.object(hud, "query", side_effect=self.query)
        self.time_patch = patch.object(hud.time, "monotonic", side_effect=lambda: self.clock)
        self.query_patch.start()
        self.time_patch.start()
        self.addCleanup(self.query_patch.stop)
        self.addCleanup(self.time_patch.stop)

    def query(self, executable, argv, field):
        self.calls.append(argv)
        command = argv[1]
        if command in self.fail:
            raise RuntimeError("unavailable")
        run = argv[argv.index("--run") + 1] if "--run" in argv else None
        if command == "run-list":
            return {"runs": copy.deepcopy(self.runs), "nextCursor": None}
        if command == "worker-list":
            return {"workers": copy.deepcopy(self.workers.get(run, [])), "page": {"hasMore": False}}
        if command == "task-list":
            return {"tasks": copy.deepcopy(self.tasks.get(run, []))}
        if command == "gate-list":
            return {"gates": copy.deepcopy(self.gates.get(run, []))}
        if command == "run-show":
            return {"run": {"id": argv[-1]}}
        if argv[0] == "project":
            return {"projects": [{"id": PROJECT, "sourceRepoIds": [REPO]},
                                 {"id": "other-project", "sourceRepoIds": ["other-repo"]}]}
        if argv[0] == "worktree":
            return {"worktrees": [{"id": REPO + "::/current", "projectId": PROJECT, "repoId": REPO, "hostId": "local"}]}
        return {"terminals": [{"handle": "central", "worktreeId": REPO + "::/current", "executionHostId": "local"},
                              {"handle": "other", "worktreeId": "other-repo::/current", "executionHostId": "local"}],
                "hostScope": {"omittedHostIds": ["remote-missing"]}}

    def test_multiple_runs_exact_project_and_read_only_commands(self):
        snapshot = self.collector.refresh()
        entries = {e["id"]: e for e in snapshot["entries"]}
        self.assertTrue(entries["run_legacy_local"]["scope"]["included"])
        self.assertFalse(entries["run_other"]["scope"]["included"])
        self.assertEqual(entries["run_unknown"]["scope"]["kind"], "unknown")
        self.assertIn("remote-missing", " ".join(snapshot["warnings"]))
        self.assertTrue(all(c[1] in {"list", "run-list", "worker-list", "run-show", "task-list", "gate-list"} for c in self.calls))
        self.assertTrue(all("--run" in c and "--include-remote" in c for c in self.calls if c[1] == "worker-list"))
        self.assertFalse(any(c[1] == "task-list" and "run_other" in c for c in self.calls))

    def test_mixed_run_counts_only_current_project_dispatch(self):
        self.workers["run_legacy_local"] += [worker("other", "otherctx", "other-repo"), worker("retry", "old")]
        self.tasks["run_legacy_local"] += [
            {"id": "other", "dispatch_id": "otherctx", "status": "blocked"},
            {"id": "pending", "status": "pending"},
            {"id": "retry", "dispatch_id": "new", "status": "failed"}]
        entry = self.collector.refresh()["entries"][0]
        self.assertEqual(entry["scope"]["kind"], "mixed")
        self.assertEqual([t["id"] for t in entry["tasks"]], ["task"])
        self.assertEqual(entry["excluded"], 3)

    def test_discovery_finds_new_run_after_completion_based_ttl(self):
        self.collector.refresh()
        self.runs.append({"id": "run_new", "coordinator_handle": "central"})
        self.clock = 29
        self.assertNotIn("run_new", [e["id"] for e in self.collector.refresh()["entries"]])
        self.clock = 30
        self.assertIn("run_new", [e["id"] for e in self.collector.refresh()["entries"]])
        self.assertEqual(self.collector.discovery, 60)

    def test_partial_failures_preserve_cache_timestamp_and_runs(self):
        first = self.collector.refresh()
        stamp = self.collector.cache["tasks:run_legacy_local"]["at"]
        self.fail.update(("task-list", "run-list", "worker-list"))
        self.clock = 31
        second = self.collector.refresh()
        self.assertEqual(len(first["entries"]), len(second["entries"]))
        entry = second["entries"][0]
        self.assertEqual(entry["tasks"], first["entries"][0]["tasks"])
        self.assertEqual(entry["records"]["tasks"]["at"], stamp)
        self.assertIn("stale/cache", render(second, stamp + 90, details=True, height=None))

    def test_slow_discovery_ttl_starts_after_scan_and_worker_cache_is_reused(self):
        original = self.query

        def slow(executable, argv, field):
            self.clock += 1
            return original(executable, argv, field)

        with patch.object(hud, "query", side_effect=slow):
            self.collector.refresh()
        self.assertGreater(self.collector.discovery, 30)
        discovered = self.collector.discovered_at
        run_scans = sum(c[1] == "run-list" for c in self.calls)
        self.collector.refresh()
        self.assertEqual(sum(c[1] == "run-list" for c in self.calls), run_scans)
        self.assertEqual(self.collector.discovered_at, discovered)

    def test_partial_first_read_is_unknown_not_empty_success(self):
        self.fail.add("task-list")
        text = render(self.collector.refresh(), 100, details=True, height=None)
        self.assertIn("Task 수 확인 불가", text)
        self.assertNotIn("0/0", text)

    def test_exact_repo_mapping_removed_workspace_and_conflict(self):
        repos, spaces = hud.mappings([{"id": PROJECT, "sourceRepoIds": [REPO]}],
            [{"id": REPO + "::/current", "repoId": REPO, "projectId": "different", "hostId": "local"}])
        self.assertEqual(hud.workspace(REPO + "::/deleted", repos, spaces, "local"), {PROJECT})
        self.assertEqual(hud.workspace("prefix" + REPO + "::/deleted", repos, spaces, "local"), set())
        run = {"coordinator_handle": "central"}
        scope = hud.membership(run, [], {("local", "central"): {"worktreeId": REPO + "::/current", "executionHostId": "local"}}, repos, spaces, PROJECT)
        self.assertEqual(scope["kind"], "conflict")
        self.assertTrue(scope["restricted"])

    def test_missing_coordinator_alone_does_not_drop_proven_project_history(self):
        self.runs[0].pop("coordinator_handle")
        self.tasks["run_legacy_local"][0].update(status="completed", dispatch_id=None)
        entry = self.collector.refresh()["entries"][0]
        self.assertTrue(entry["scope"]["uncertain"])
        self.assertEqual(len(entry["tasks"]), 1)

    def test_render_priority_model_semantics_ansi_and_terminal_size(self):
        snapshot = self.collector.refresh()
        snapshot["entries"][0]["run"]["objective"] = "\x1b[2J\x1b]52;c;x\a한글" * 50
        text = render(snapshot, 200, width=220, height=None, details=True)
        self.assertIn("출처Run run_legacy_local", text)
        self.assertLess(text.index("Run run_legacy_local"), text.index("Run run_done"))
        self.assertIn("observed-model · 관찰 오래됨", text)
        self.assertIn("종료 ≠ 기능 수용", text)
        snapshot["entries"][0]["records"]["workers"]["data"]["workers"][0]["projection"]["provider"] = {}
        self.assertIn("모델 미확인", render(snapshot, 100, details=True, height=None))
        for width, height in ((80, 19), (40, 10), (10, 3), (1, 1)):
            with self.subTest(width=width):
                output = render(snapshot, 100, width=width, height=height, color=True)
                self.assertLessEqual(len(output.splitlines()), height)
                self.assertTrue(all(hud_view.cell_width(line) < width for line in output.splitlines()))
                self.assertNotIn("\x1b[2J", output)
                self.assertNotIn("52;c", output)

    def test_24_line_screen_shares_current_tasks_across_runs_before_old_failure(self):
        self.runs = [{"id": name, "coordinator_handle": "central"}
                     for name in ("run_old_failed", "run_many", "run_next", "run_third")]
        self.tasks = {r["id"]: [{"id": f"{r['id']}_{i}", "status": "failed" if r["id"] == "run_old_failed" else "dispatched",
                                "task_title": f"{r['id']}-task-{i}", "result": "old failure"}
                               for i in range(20)] for r in self.runs}
        text = render(self.collector.refresh(), 100, width=120, height=24)
        self.assertLess(len(text.splitlines()), 24)
        for name in ("run_many", "run_next", "run_third"):
            self.assertIn(f"출처Run {name}", text)
            self.assertLess(text.index(f"Run {name}"), text.index("Run run_old_failed"))
        self.assertIn("Task 더보기 19건", text)
        self.assertNotIn("run_many-task-1", text)

    def test_failed_and_blocked_result_is_one_sanitized_cause_line(self):
        self.tasks["run_legacy_local"] = [
            {"id": "failed", "status": "failed", "result": "quota exceeded\nretry tomorrow\x1b[2J"},
            {"id": "blocked", "status": "blocked", "result": "approval required\nask coordinator"}]
        text = render(self.collector.refresh(), 100, width=200, height=None, details=True)
        causes = [line for line in text.splitlines() if line.startswith("원인 ")]
        self.assertEqual(len(causes), 2)
        self.assertIn("원인 quota exceeded retry tomorrow", causes)
        self.assertIn("원인 approval required ask coordinator", causes)
        self.assertNotIn("\x1b", text)

    def test_completed_tasks_keep_pending_gate_visible_on_24_line_screen(self):
        self.gates["run_done"] = [
            {"id": "approval", "status": "pending", "question": "배포 승인 필요\n중앙 확인\x1b[2J"},
            {"id": "resolved", "status": "resolved", "question": "해결된 질문"},
            {"id": "cancelled", "status": "cancelled", "question": "취소된 질문"}]
        self.gates["run_other"] = [{"status": "pending", "question": "외부 질문"}]
        snapshot = self.collector.refresh()
        text = render(snapshot, 100, width=80, height=24)
        self.assertLess(len(text.splitlines()), 24)
        self.assertIn("결정 대기 Gate 1건", text)
        self.assertIn("출처Run run_done · 질문 배포 승인 필요 중앙 확인", text)
        self.assertIn("Run run_done [프로젝트] Gate 1", text)
        self.assertIn("과거 요약 Run 0", text)
        self.assertLess(text.index("질문 배포 승인 필요"), text.index("Run run_legacy_local ["))
        for hidden in ("해결된 질문", "취소된 질문", "외부 질문", "\x1b"):
            self.assertNotIn(hidden, text)

    def test_resolved_and_cancelled_gates_allow_historical_summary(self):
        self.gates["run_done"] = [
            {"status": "resolved", "question": "해결된 질문"},
            {"status": "cancelled", "question": "취소된 질문"}]
        text = render(self.collector.refresh(), 100, width=80, height=24)
        self.assertIn("결정 대기 Gate 0건", text)
        self.assertIn("과거 요약 Run 1", text)
        self.assertNotIn("Run run_done", text)
        self.assertNotIn("질문", text)

    def test_gate_summary_precedes_busy_runs_and_preserves_two_current_runs(self):
        self.runs = [{"id": name, "coordinator_handle": "central"} for name in
                     ("run_done", *(f"run_blocked_{i}" for i in range(6)), "run_current_a", "run_current_b")]
        self.tasks = {r["id"]: [{"id": f"{r['id']}_{i}", "task_title": f"{r['id']}-task-{i}",
                                "status": "completed" if r["id"] == "run_done" else
                                "blocked" if "blocked" in r["id"] else "dispatched"}
                               for i in range(20)] for r in self.runs}
        self.gates["run_done"] = [{"status": "pending", "question": "배포 승인 필요"}]
        snapshot = self.collector.refresh()
        for width in (80, 110):
            with self.subTest(width=width):
                text = render(snapshot, 100, width=width, height=24)
                self.assertLess(len(text.splitlines()), 24)
                self.assertIn("결정 대기 Gate 1건", text)
                self.assertIn("출처Run run_done · 질문 배포 승인 필요", text)
                for run_id in ("run_current_a", "run_current_b"):
                    self.assertIn(f"출처Run {run_id} · [dispatched]", text)
                    self.assertLess(text.index("질문 배포 승인 필요"), text.index(f"출처Run {run_id}"))
                self.assertIn("더보기", text)

    def test_failed_gate_reads_warn_without_confirming_zero_or_hiding_cached_question(self):
        self.fail.add("gate-list")
        text = render(self.collector.refresh(), 100, width=120, height=24)
        self.assertIn("결정 Gate 확인 불가 2 Run", text)
        self.assertNotIn("결정 대기 Gate 0건", text)
        self.assertNotIn("[프로젝트] Gate 0", text)
        self.assertIn("Run run_done [프로젝트] Gate 확인 불가", text)
        self.assertIn("조회불가·범위 경고", text)
        self.fail.clear()
        self.gates["run_done"] = [{"status": "pending", "question": "캐시 승인 질문"}]
        self.clock = 31
        self.collector.refresh()
        self.fail.add("gate-list")
        self.clock = 62
        text = render(self.collector.refresh(), 100, width=120, height=24)
        self.assertIn("결정 Gate 확인 불가 2 Run · 대기 1건(캐시 포함)", text)
        self.assertIn("출처Run run_done · 질문 캐시 승인 질문", text)
        self.assertIn("Run run_done [프로젝트] Gate 확인 불가", text)
        self.assertIn("stale/cache", text)

    def test_failed_discovery_keeps_success_time_and_recovery_updates_it(self):
        with patch.object(hud.time, "time", return_value=1000):
            self.collector.refresh()
        self.clock = 31
        self.fail.update(("worker-list", "run-list"))
        with patch.object(hud.time, "time", return_value=1100):
            failed = self.collector.refresh()
        self.assertEqual(failed["discovered_at"], 1000)
        self.assertEqual(failed["discovery_attempted_at"], 1100)
        self.assertTrue(failed["discovery_failed"])
        self.assertIn("최근 스캔 실패", render(failed, 1101, width=200))
        self.clock = 62
        self.fail.clear()
        with patch.object(hud.time, "time", return_value=1200):
            recovered = self.collector.refresh()
        self.assertEqual(recovered["discovered_at"], 1200)
        self.assertFalse(recovered["discovery_failed"])

    def test_first_discovery_failure_has_no_success_timestamp(self):
        self.fail.add("run-list")
        snapshot = self.collector.refresh()
        self.assertEqual(snapshot["discovered_at"], 0)
        self.assertGreater(snapshot["discovery_attempted_at"], 0)
        self.assertIn("소속 마지막 성공 없음", render(snapshot, 100, width=200))

    def test_refresh_deadline_preserves_success_cache_and_skips_queued_calls(self):
        before = self.collector.refresh()
        stamp = self.collector.cache["tasks:run_legacy_local"]["at"]
        self.clock = 31
        deadline, starts = self.clock + 45, []

        def slow(executable, argv, field):
            starts.append(self.clock)
            self.clock += 10
            return self.query(executable, argv, field)

        with patch.object(hud, "query", side_effect=slow):
            snapshot = self.collector.refresh()
        self.assertTrue(all(start < deadline for start in starts))
        self.assertLessEqual(self.clock, deadline + 10)
        self.assertEqual(snapshot["entries"][0]["tasks"], before["entries"][0]["tasks"])
        self.assertEqual(self.collector.cache["tasks:run_legacy_local"]["at"], stamp)
        self.assertTrue(snapshot["discovery_failed"])
        self.assertIn("조회 기한 초과", " ".join(snapshot["entries"][0]["errors"]))

    def test_same_workspace_id_on_remote_cannot_borrow_local_membership(self):
        self.runs[0].pop("coordinator_handle")
        remote = worker()
        remote["resource"]["worktreeId"] = REPO + "::/current"
        remote["projection"].update(host={"id": "remote"}, workspace={"id": REPO + "::/current"})
        remote["agentTerminalHandle"] = "central"
        self.workers["run_legacy_local"] = [remote]
        entry = self.collector.refresh()["entries"][0]
        self.assertEqual(entry["scope"]["kind"], "unknown")
        self.assertFalse(entry["scope"]["included"])
        repos, spaces = hud.mappings([{"id": PROJECT, "sourceRepoIds": [REPO]}], [
            {"id": REPO + "::/current", "repoId": REPO, "projectId": PROJECT, "hostId": "local"},
            {"id": REPO + "::/current", "repoId": REPO, "projectId": "other-project", "hostId": "remote"}])
        scope = hud.membership({}, [remote], {}, repos, spaces, PROJECT)
        self.assertEqual(scope["kind"], "conflict")
        self.assertTrue(scope["restricted"])
        self.assertEqual(hud.workspace(REPO + "::/deleted", repos, spaces, "remote"), set())
        self.assertEqual(hud.workspace(REPO + "::/current", repos, spaces), set())
        remote["resource"]["hostId"] = "local"
        self.assertEqual(hud.affiliation(remote, {}, repos, spaces), (set(), True))


class PagingTest(unittest.TestCase):
    def test_unique_cursor_stops_at_refresh_deadline_with_ten_second_overrun(self):
        clock, starts = [0], []

        def endless(executable, argv, field):
            starts.append(clock[0])
            clock[0] += 10
            return {"runs": [{"id": str(clock[0])}], "nextCursor": str(clock[0])}

        with patch.object(hud.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(hud, "query", side_effect=endless):
            result = hud.pages("orca", ["orchestration", "run-list"], "runs", deadline=45)
        self.assertEqual(starts, [0, 10, 20, 30, 40])
        self.assertLessEqual(clock[0], 55)
        self.assertEqual(len(result["runs"]), 5)
        self.assertIn("기한 초과", result["_errors"][0])

    def test_real_query_boundary_caps_subprocesses_and_deadline_skips_large_queue(self):
        clock, starts, mutex = [0], [], Lock()

        def process(argv, **kwargs):
            with mutex:
                starts.append(clock[0])
                clock[0] += 10
            self.assertEqual(kwargs["timeout"], 10)
            command = argv[2]
            if command == "run-list":
                result = {"runs": [{"id": f"run_{i}", "coordinator_handle": "central"} for i in range(100)]}
            elif command == "worker-list":
                result = {"workers": [], "page": {"hasMore": False}}
            elif argv[1] == "project":
                result = {"projects": [{"id": PROJECT, "sourceRepoIds": [REPO]}]}
            elif argv[1] == "worktree":
                result = {"worktrees": [{"id": REPO + "::/current", "projectId": PROJECT, "hostId": "local"}]}
            else:
                result = {"terminals": [{"handle": "central", "worktreeId": REPO + "::/current", "executionHostId": "local"}]}
            return SimpleNamespace(stdout=hud.json.dumps({"ok": True, "result": result}), returncode=0)

        with patch.object(hud.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(hud.subprocess, "run", side_effect=process):
            snapshot = hud.ProjectHud("orca", "id:" + REPO, PROJECT).refresh()
        self.assertTrue(all(start < 45 for start in starts))
        self.assertLessEqual(clock[0], 55)
        self.assertLessEqual(len(starts), 5)
        self.assertEqual(len(snapshot["entries"]), 100)
        self.assertTrue(snapshot["discovery_failed"])

    def test_run_and_worker_pagination_deduplication_and_repeated_cursor(self):
        for field in ("runs", "workers"):
            key = "id" if field == "runs" else "dispatchId"
            replies = [{field: [{key: "first"}], **({"nextCursor": "opaque"} if field == "runs" else
                        {"page": {"hasMore": True, "nextCursor": "opaque"}})},
                       {field: [{key: "first"}, {key: "second"}], **({"nextCursor": "opaque"} if field == "runs" else
                        {"page": {"hasMore": True, "nextCursor": "opaque"}})}]
            with patch.object(hud, "query", side_effect=replies) as query:
                result = hud.pages("orca", ["orchestration", "run-list" if field == "runs" else "worker-list"], field)
            self.assertEqual(len(result[field]), 2)
            self.assertTrue(result["_errors"])
            self.assertIn("--cursor", query.call_args.args[1])

    def test_unique_infinite_cursor_is_bounded_and_partial_failure_retained(self):
        def reply(executable, argv, field):
            cursor = int(argv[-1]) + 1 if "--cursor" in argv else 1
            return {"runs": [{"id": str(cursor)}], "nextCursor": str(cursor)}
        with patch.object(hud, "query", side_effect=reply) as query:
            result = hud.pages("orca", ["orchestration", "run-list"], "runs")
        self.assertEqual(query.call_count, 256)
        self.assertIn("상한", result["_errors"][0])
        with patch.object(hud, "query", side_effect=[{"workers": [{"dispatchId": "one"}],
              "page": {"hasMore": True, "nextCursor": "next"}}, subprocess.TimeoutExpired("orca", 10)]):
            result = hud.pages("orca", ["orchestration", "worker-list"], "workers")
        self.assertEqual(result["workers"], [{"dispatchId": "one"}])
        self.assertTrue(result["_errors"])

    def test_page_one_missing_hosts_and_remote_warnings_survive_later_pages(self):
        replies = [{"workers": [{"dispatchId": "one"}], "warnings": ["remote unavailable"],
                    "hostScope": {"omittedHostIds": ["remote"]},
                    "page": {"hasMore": True, "nextCursor": "opaque"}},
                   {"workers": [{"dispatchId": "two"}], "page": {"hasMore": False}}]
        with patch.object(hud, "query", side_effect=replies):
            result = hud.pages("orca", ["orchestration", "worker-list"], "workers")
        self.assertEqual(result["hostScope"]["omittedHostIds"], ["remote"])
        self.assertIn("remote unavailable", result["warnings"])

    def test_query_validates_envelopes_timeout_and_reuses_existing_query(self):
        for raw in ("[]", "null", '{"ok":true,"result":{"runs":[null]}}', '{"ok":false,"result":{"runs":[]}}'):
            with patch.object(hud.subprocess, "run", return_value=SimpleNamespace(stdout=raw, returncode=0)) as run:
                with self.assertRaises(RuntimeError):
                    hud.query("orca", ["orchestration", "run-list"], "runs")
                self.assertEqual(run.call_args.kwargs["timeout"], 10)
        with patch.object(hud.hud, "query", return_value={"tasks": []}) as existing:
            hud.query("orca", ["orchestration", "task-list", "--run", "run_legacy_local"], "tasks")
        self.assertIn("run_legacy_local", existing.call_args.args[1])

    def test_watch_ctrl_c_restores_terminal_and_no_color(self):
        with patch.object(hud.sys, "argv", ["project_hud.py", "--repo", "id:" + REPO, "--project", PROJECT, "--watch"]), \
             patch.object(hud, "ProjectHud") as collector, patch.object(hud.sys, "stdout", new_callable=io.StringIO) as output, \
             patch.object(hud, "render", return_value="screen") as renderer, patch.object(hud.time, "sleep", side_effect=KeyboardInterrupt), \
             patch.dict(hud.os.environ, {"NO_COLOR": "1"}):
            output.isatty = lambda: True
            collector.return_value.refresh.return_value = {"warnings": [], "entries": []}
            self.assertEqual(hud.main(), 0)
            self.assertIn("\x1b[?1049l", output.getvalue())
            self.assertFalse(renderer.call_args.args[4])


if __name__ == "__main__":
    unittest.main()
