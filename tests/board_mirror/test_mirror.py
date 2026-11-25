import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import time
import datetime as dt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hubs/scripts"))
import board_mirror as bm


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = dict(os.environ)
        os.environ.update(LRM_HUB_DIR=str(self.home), LRM_REPORT_OUTBOX=str(self.home / "queue"))
        self.policy = ROOT / "hubs/board_mirror.tsv"
        self.root = dict(id="lrm-test", title="시험 기능", issue_type="epic", status="open",
                         labels=["flow:v1", "feature:test", "slack-thread:123.456"])
        self.child = dict(id="lrm-test.1", parent="lrm-test", status="closed", labels=["ws:/tmp/test"])
        self.rows = {"lrm-test": [], "lrm-test.1": []}
        self.cards = [self.root, self.child]

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        self.temp.cleanup()

    def mirror(self, dry=False):
        mirror = bm.Mirror(self.policy, dry)
        mirror.bd = lambda *a: self.cards if a[0] == "list" else self.rows[a[1]]
        return mirror

    def run_mirror(self, dry=False):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.mirror(dry).run()
        return out.getvalue()

    def add(self, text, cid="lrm-test.1", author="worker-test"):
        n = len(self.rows[cid]) + 1
        self.rows[cid].append(dict(id=f"id-{n}", created_at=f"2026-11-21T00:00:{n:02d}Z",
                                  text=text, author=author))

    def queue(self):
        path = self.home / "queue"
        return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []

    def test_baseline_batch_duplicate_and_format(self):
        self.add("지난 기록")
        self.run_mirror()
        self.assertEqual([], self.queue())
        self.add("worker-test: 동작 수정 완료 a12bc34")
        self.add("관련 시험 통과")
        self.run_mirror()
        q = self.queue()
        self.assertEqual(1, len(q))
        rep = q[0]
        self.assertEqual(["id-2", "id-3"], rep["mirror_ids"])
        self.assertEqual(("feat-test", "lrm-test"), (rep["hub"], rep["card"]))
        self.assertNotIn("ws", rep)
        self.assertEqual("lrm-test.1", rep["mirror_card"])
        import report_sender
        self.assertEqual("card:lrm-test", report_sender.topic_key(rep))
        self.assertIn("관련 시험 통과", rep["text"])
        self.assertNotIn("worker-test", rep["text"].split("참고:")[0])
        self.assertTrue(bm.report.valid_record(rep))
        bm.report.make_report(rep["hub"], rep["task"], rep["kind"], rep["text"], [], card=rep["card"])
        self.run_mirror()
        self.assertEqual(q, self.queue())
        state = self.home / "board-mirror.state.json"
        data = json.loads(state.read_text()); data["lrm-test.1"] = "id-1"
        data["_seen"]["lrm-test.1"] = ["id-1"]
        state.write_text(json.dumps(data))
        self.run_mirror()
        self.assertEqual(q, self.queue())

    def test_filters_manual_and_long_paste(self):
        self.run_mirror()
        for text in ["Slack 결정 답: 승인", "Slack 스레드 123.456 연결. 답", "Slack 답글: 확인",
                     "Slack 피드백(원문): 확인", "Slack 결정 질문: 확인"]:
            self.add(text)
        self.run_mirror()
        self.assertEqual([], self.queue())
        self.add("지시 #1: 브리프\n" + "원문" * 200, author="사람")
        self.run_mirror()
        self.assertIn("원문 411자", self.queue()[0]["text"])
        self.assertNotIn("원문원문", self.queue()[0]["text"])

    def test_existing_report_and_identical_new_ids(self):
        self.run_mirror()
        rep = bm.report.make_report("feat-test", "없음", "info", "이미 보낸 보고", [], card="lrm-test.1")
        bm.report.add(str(self.home / "queue"), rep, 0, False)
        self.add("이미 보낸 보고")
        self.run_mirror()
        self.assertEqual(1, len(self.queue()))
        for _ in range(2):
            self.add("같은 내용의 새 기록")
            self.run_mirror()
        self.assertEqual(3, len(self.queue()))

    def test_overflow_no_attachment_and_dry_no_write(self):
        self.run_mirror()
        for n in range(8):
            self.add(f"기록{n} " + "설명" * 40)
        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        self.assertIn("작업판 카드 기록에", self.run_mirror(True))
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()})
        self.run_mirror()
        rep = self.queue()[0]
        self.assertEqual([], rep["files"])
        self.assertEqual(3, len(rep["text"].splitlines()))
        self.assertLessEqual(len(rep["text"]), bm.report.MAX_CHARS)
        self.assertIn("기록7", rep["text"])
        self.assertNotIn("기록0 ", rep["text"])
        self.assertEqual(8, len(rep["mirror_ids"]))

    def test_status_line_stage_doing_todo(self):
        self.run_mirror()
        self.root["labels"].append("stage:review")
        self.cards.append(dict(id="lrm-test.2", parent="lrm-test", status="in_progress", title="셔터 색 맞춤", labels=[]))
        self.rows["lrm-test.2"] = []
        self.add("관련 시험 통과")
        self.run_mirror()
        head = self.queue()[0]["text"].splitlines()[0]
        self.assertEqual("시험 기능 — 지금 검수 단계 · 진행 중 1장(셔터 색 맞춤) · 할 일: 검수 카드에 통과·고칠 점 답", head)

    def test_status_picks_latest_stage(self):
        self.run_mirror()
        self.root["labels"] += ["stage:build", "stage:ship"]
        self.add("관련 시험 통과")
        self.run_mirror()
        self.assertIn("지금 반영 단계", self.queue()[0]["text"])

    def test_status_without_stage_says_none(self):
        self.run_mirror()
        self.add("관련 시험 통과")
        self.run_mirror()
        self.assertIn("정해지지 않은 단계 · 할 일: 없음", self.queue()[0]["text"])

    def test_jargon_wrapped_and_machine_records_counted(self):
        self.run_mirror()
        for text in ["local-gate 13/13(--no-unity)", "bd unclaim --force 로 해제",
                     "draft PR 생성 (head )", "원격 통합 브랜치 = . 확인", "board-done 통과"]:
            self.add(text)
        self.run_mirror()
        text = self.queue()[0]["text"]
        self.assertIn("`local-gate`", text)
        self.assertIn("`board-done` 통과", text)
        self.assertNotIn("unclaim", text)
        self.assertNotIn("(head )", text)
        self.assertIn("외 3건은 작업판 카드 기록에", text)

    def test_block_record_never_dropped(self):
        self.run_mirror()
        self.add("막힘: heartbeat 끊김, board-done 실패")
        self.add("[block] unclaim 뒤 = . 남음")
        self.run_mirror()
        text = self.queue()[0]["text"]
        self.assertIn("막힘: `heartbeat` 끊김, `board-done` 실패", text)
        self.assertIn("[block] `unclaim`", text)

    def test_backtick_record_allowed(self):
        self.run_mirror()
        self.add("다시 돌리려면 `board-run --once` 실행")
        self.run_mirror()
        self.assertIn("`board-run --once` 실행", self.queue()[0]["text"])

    def test_tool_name_in_card_title_wrapped(self):
        self.run_mirror()
        self.root["title"] = "board-done 정리"
        self.add("관련 시험 통과")
        self.run_mirror()
        self.assertTrue(self.queue()[0]["text"].startswith("`board-done 정리` — 지금"))

    def test_plain_words_and_evidence_lines(self):
        self.run_mirror()
        self.add("evidence:compile=/tmp/a.log")
        self.run_mirror()
        self.assertIn("`evidence:compile=/tmp/a.log`", self.queue()[0]["text"])
        self.add("워커가 EditMode 시험 12건 통과")
        self.run_mirror()
        self.assertIn("작업자가 에디터 시험 12건 통과", self.queue()[-1]["text"])

    def test_rules_come_from_shared_plain_words_table(self):
        table = self.home / "plain.tsv"
        table.write_text("rewrite\t셔터\t문\nreject\t자판기\t'기계'\n", encoding="utf-8")
        os.environ["LRM_PLAIN_WORDS"] = str(table)
        self.run_mirror()
        self.add("자판기 점검")
        self.add("셔터 색 수정")
        self.run_mirror()
        text = self.queue()[0]["text"]
        self.assertIn("문 색 수정", text)
        self.assertIn("`자판기` 점검", text)

    def test_url_numbers_kept(self):
        self.run_mirror()
        self.add("CI 취소 https://github.com/o/r/actions/runs/45309007393 확인")
        self.run_mirror()
        self.assertIn("runs/45309007393", self.queue()[0]["text"])

    def test_instruction_record_rewritten_plain(self):
        self.run_mirror()
        self.add("지시 처리 #3: 반영 — 프레임 캡처 시작")
        self.run_mirror()
        text = self.queue()[0]["text"]
        self.assertIn("요청 3 반영: 프레임 캡처 시작", text)
        self.assertNotIn("지시 처리", text)

    def test_bd_failure_and_dry_bootstrap(self):
        self.assertIn("baseline", self.run_mirror(True))
        self.assertEqual([], list(self.home.iterdir()))
        env = dict(os.environ, BOARD_BD="/usr/bin/false")
        p = subprocess.run([sys.executable, "-B", str(ROOT / "hubs/scripts/board_mirror.py"), "--once"],
                           env=env, capture_output=True)
        self.assertEqual(1, p.returncode)
        self.assertFalse((self.home / "board-mirror.state.json").exists())

    def test_uuid_order_with_same_second(self):
        self.add("기존 기록")
        self.rows["lrm-test.1"][0]["id"] = "z-uuid"
        self.run_mirror()
        self.add("같은 초에 추가한 기록")
        self.rows["lrm-test.1"][-1].update(id="a-uuid", created_at="2026-11-21T00:00:01Z")
        self.run_mirror()
        self.assertEqual(["a-uuid"], self.queue()[0]["mirror_ids"])
        self.run_mirror()
        self.assertEqual(1, len(self.queue()))

    def test_cli_fake_bd(self):
        fixture = self.home / "fixture"
        fake = self.home / "bd"
        fake.write_text("#!" + sys.executable + "\nimport json,sys\n"
                        + "data=json.load(open(" + repr(str(fixture)) + "))\n"
                        + "print(json.dumps(data['cards'] if sys.argv[1]=='list' else data['rows'][sys.argv[2]]))\n")
        fake.chmod(0o755)
        env = dict(os.environ, BOARD_BD=str(fake))
        cmd = [sys.executable, "-B", str(ROOT / "hubs/scripts/board_mirror.py"), "--once"]
        for text in [None, "실제 CLI 새 기록", None]:
            if text:
                self.add(text)
            fixture.write_text(json.dumps(dict(cards=self.cards, rows=self.rows)))
            p = subprocess.run(cmd, env=env, capture_output=True, text=True)
            self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual(1, len(self.queue()))

    def test_bd_found_without_path(self):
        # launchd 의 짧은 PATH 재현. 가짜 bd 는 BOARD_BD 로만 닿음
        short = "/usr/bin:/bin"
        os.environ.pop("BOARD_BD", None)
        with patch.dict(os.environ, PATH=short):
            self.assertEqual("/opt/homebrew/bin/bd", bm.Mirror(self.policy, True).bd_path)
        fixture = self.home / "fixture"
        fixture.write_text(json.dumps(dict(cards=self.cards, rows=self.rows)))
        fake = self.home / "bd"
        fake.write_text("#!" + sys.executable + "\nimport json,sys\n"
                        + "data=json.load(open(" + repr(str(fixture)) + "))\n"
                        + "print(json.dumps(data['cards'] if sys.argv[1]=='list' else data['rows'][sys.argv[2]]))\n")
        fake.chmod(0o755)
        env = dict(HOME=str(self.home), PATH=short, BOARD_BD=str(fake), BOARD_DIR=str(self.home),
                   LRM_HUB_DIR=str(self.home),
                   LRM_REPORT_OUTBOX=str(self.home / "queue"))
        p = subprocess.run([sys.executable, "-I", str(ROOT / "hubs/scripts/board_mirror.py"),
                            "--dry-run", "--card", "lrm-test"], env=env, capture_output=True, text=True)
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertIn('"baseline": "lrm-test.1"', p.stdout)

    def test_lock_keeps_body_error(self):
        with self.assertRaises(FileNotFoundError):
            with bm.report.locked(str(self.home / "state")):
                raise FileNotFoundError("bd")
        blocker = self.home / "file"
        blocker.write_text("")
        with self.assertRaisesRegex(bm.report.ReportError, "잠금 실패"):
            with bm.report.locked(str(blocker / "state")):
                pass

    def test_bad_record_isolated_then_skipped_on_third_failure(self):
        self.run_mirror()
        self.add("password=pretend-value")
        self.add("뒤 정상 기록")
        self.add("다른 카드 정상 기록", "lrm-test")
        for n in range(1, 4):
            self.run_mirror()
            state = json.loads((self.home / "board-mirror.state.json").read_text())
            self.assertEqual("id-1", state["lrm-test"])
            self.assertEqual("" if n < 3 else "id-2", state["lrm-test.1"])
        self.assertEqual(2, len(self.queue()))
        log = (self.home / "board-mirror.log").read_text()
        self.assertIn("건너뜀 3회", log)
        self.assertNotIn("pretend-value", log)
        self.run_mirror()
        self.assertEqual(2, len(self.queue()))

    def test_backtick_relative_repo_uppercase_slug(self):
        self.run_mirror()
        self.child["labels"] = ["repo:Basakansoft/project"]
        self.root["labels"][1] = "feature:Test"
        self.add("문서 `docs/out/result.html` 확인")
        self.run_mirror()
        rep = self.queue()[0]
        self.assertIn("`docs/out/result.html`", rep["text"])
        self.assertNotIn("``", rep["text"])
        self.assertNotIn("ws", rep)
        self.assertEqual("feat-test", rep["hub"])

    def test_deleted_last_and_late_old_timestamp(self):
        self.add("기준 기록")
        self.run_mirror()
        self.add("옛 시각으로 늦게 도착한 기록")
        self.rows["lrm-test.1"][-1]["created_at"] = "2000-01-01T00:00:00Z"
        self.rows["lrm-test.1"].pop(0)
        self.run_mirror()
        self.assertEqual(["id-2"], self.queue()[0]["mirror_ids"])

    def test_new_child_is_not_baseline(self):
        self.run_mirror()
        self.cards.append(dict(id="lrm-test.2", parent="lrm-test", labels=[]))
        self.rows["lrm-test.2"] = [dict(id="new", text="지시 #1: 새 하위 작업 시작")]
        self.run_mirror()
        self.assertEqual(["new"], self.queue()[0]["mirror_ids"])

    def test_seen_ids_bounded_and_author_only_prefix(self):
        for i in range(510):
            self.rows["lrm-test.1"].append(dict(id=f"row-{i}", text="과거 기록"))
        self.run_mirror()
        state = json.loads((self.home / "board-mirror.state.json").read_text())
        self.assertEqual(500, len(state["_seen"]["lrm-test.1"]))
        self.run_mirror()
        self.assertEqual([], self.queue())
        self.add("worker-test: worker-test와 worker-test의 요청 확인")
        self.run_mirror()
        self.assertIn("worker-test와 worker-test의 요청", self.queue()[0]["text"])

    def test_recent_closed_feature_and_expired_files(self):
        self.run_mirror()
        self.root.update(status="closed", closed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        folder = self.home / "board-mirror-files"
        folder.mkdir()
        old, new = folder / "old.txt", folder / "new.txt"
        old.write_text("지난 첨부"); new.write_text("새 첨부")
        os.utime(old, (time.time() - 8 * 86400,) * 2)
        self.add("닫힌 뒤 시험 결과")
        self.run_mirror()
        self.assertEqual(1, len(self.queue()))
        self.assertFalse(old.exists()); self.assertTrue(new.exists())
        self.root["closed_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25)).isoformat()
        self.add("종료 하루 뒤 기록")
        self.run_mirror()
        self.assertEqual(1, len(self.queue()))

    def test_budget_and_other_card_query_failure(self):
        self.run_mirror()
        self.add("정상 기록", "lrm-test")
        mirror = self.mirror()
        query = mirror.bd
        def fail_child(*args):
            if args[:2] == ("comments", "lrm-test.1"):
                raise ValueError("가짜 bd 조회 실패")
            return query(*args)
        mirror.bd = fail_child
        with contextlib.redirect_stdout(io.StringIO()):
            mirror.run()
        self.assertEqual(1, len(self.queue()))
        self.assertIn("가짜 bd 조회 실패", (self.home / "board-mirror.log").read_text())
        mirror = self.mirror()
        query = mirror.bd
        def expire(*args):
            result = query(*args)
            if args[0] == "list":
                mirror.deadline = time.monotonic() - 1
            return result
        mirror.bd = expire
        mirror.run()
        self.assertIn("전체 시간 예산 소진", (self.home / "board-mirror.log").read_text())

    def test_outbox_exception_preserves_records_until_recovery(self):
        self.run_mirror()
        self.add("기록기 예외 재현")
        with patch.object(bm.report, "add", side_effect=OSError("가짜 대기열 실패")):
            for _ in range(5):
                self.run_mirror()
                state = json.loads((self.home / "board-mirror.state.json").read_text())
                self.assertEqual("", state["lrm-test.1"])
                self.assertEqual([], state["_seen"]["lrm-test.1"])
                self.assertEqual({}, state["_failures"].get("lrm-test.1", {}))
        self.assertNotIn("건너뜀", (self.home / "board-mirror.log").read_text())
        self.run_mirror()
        state = json.loads((self.home / "board-mirror.state.json").read_text())
        self.assertEqual("id-1", state["lrm-test.1"])
        self.assertEqual(["id-1"], self.queue()[0]["mirror_ids"])

    def test_first_baseline_retries_failed_child_without_replay(self):
        self.add("과거 하위 기록")
        mirror = self.mirror()
        query = mirror.bd
        def fail_child(*args):
            if args[:2] == ("comments", "lrm-test.1"):
                raise ValueError("첫 주기 bd 실패")
            return query(*args)
        mirror.bd = fail_child
        with contextlib.redirect_stdout(io.StringIO()):
            mirror.run()
        state = json.loads((self.home / "board-mirror.state.json").read_text())
        self.assertNotIn("lrm-test", state["_features"])
        self.assertEqual(["lrm-test.1"], state["_bootstrap"]["lrm-test"])
        self.run_mirror()
        self.assertEqual([], self.queue())
        self.add("기준선 이후 새 기록")
        self.run_mirror()
        self.assertEqual(["id-2"], self.queue()[0]["mirror_ids"])

    def test_budget_resume_keeps_initial_baseline_and_reaches_later_cards(self):
        self.cards.append(dict(id="lrm-test.2", parent="lrm-test", labels=[]))
        self.rows["lrm-test.2"] = [dict(id="old", text="뒤 카드 과거 기록")]
        self.add("기존 하위 기록")
        mirror = self.mirror()
        query = mirror.bd
        def interrupt(*args):
            if args[:2] == ("comments", "lrm-test.1"):
                raise bm.BudgetExpired("시험 예산 소진")
            return query(*args)
        mirror.bd = interrupt
        with contextlib.redirect_stdout(io.StringIO()):
            mirror.run()
        state = json.loads((self.home / "board-mirror.state.json").read_text())
        self.assertEqual(["lrm-test", "lrm-test.1"], state["_resume"])
        mirror = self.mirror()
        query = mirror.bd
        visited = []
        def track(*args):
            if args[0] == "comments":
                visited.append(args[1])
            return query(*args)
        mirror.bd = track
        with contextlib.redirect_stdout(io.StringIO()):
            mirror.run()
        self.assertEqual(["lrm-test.1", "lrm-test.2", "lrm-test"], visited)
        self.assertEqual([], self.queue())
        self.add("뒤 카드 새 기록", "lrm-test.2")
        self.run_mirror()
        self.assertEqual("lrm-test.2", self.queue()[0]["mirror_card"])

    def test_cli_alarm_bounds_lock_wait(self):
        policy = self.home / "policy.tsv"
        policy.write_text(self.policy.read_text().replace("budget_sec\t30", "budget_sec\t0.1"))
        import fcntl
        lock = self.home / "board-mirror.state.json.lock"
        with lock.open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            start = time.monotonic()
            p = subprocess.run([sys.executable, "-B", str(ROOT / "hubs/scripts/board_mirror.py"),
                                "--once", "--policy", str(policy)], capture_output=True, timeout=2)
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(1, p.returncode)
        self.assertIn("BudgetExpired", (self.home / "board-mirror.log").read_text())

    def test_poller_recomputes_mirror_budget_after_sender(self):
        script = (ROOT / "hubs/scripts/slack_poll.sh").read_text()
        tail = script[script.index('left=$((TOTAL', script.index('# 허브 보고 전송')):script.rindex('exit "$RC"')]
        clock = self.home / "clock"
        budget = self.home / "mirror-budget"
        py = self.home / "fake-python"
        py.write_text('#!/bin/bash\ncase "$2" in *report_sender.py) echo 5 > "$LRM_HUB_DIR/clock";; '
                      '*) echo "$5" > "$LRM_HUB_DIR/mirror-budget";; esac\n')
        py.chmod(0o755)
        clock.write_text("0")
        setup = 'TOTAL=10; T0=0; DIR="$1"; PY="$2"; date() { cat "$DIR/clock"; };\n'
        p = subprocess.run(["bash", "-c", setup + tail, "test", str(self.home), str(py)], capture_output=True)
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual("3", budget.read_text().strip())

    def test_pending_baseline_sends_records_after_cutoff(self):
        self.add("과거 기록")
        mirror = self.mirror()
        query = mirror.bd
        def interrupt(*args):
            if args[:2] == ("comments", "lrm-test.1"):
                raise bm.BudgetExpired("기준선 중단")
            return query(*args)
        mirror.bd = interrupt
        with contextlib.redirect_stdout(io.StringIO()):
            mirror.run()
        self.add("기준선 대기 중 새 기록")
        self.rows["lrm-test.1"][-1]["created_at"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)).isoformat()
        self.run_mirror()
        self.assertEqual(["id-2"], self.queue()[0]["mirror_ids"])

    def test_log_size_bounded(self):
        mirror = self.mirror()
        mirror.cfg["log_bytes"] = "4096"
        for _ in range(100):
            mirror.log("lrm-test", "반복 실패" * 20)
        path = self.home / "board-mirror.log"
        self.assertLessEqual(path.stat().st_size, 4096)
        self.assertIn("반복 실패", path.read_text())

    def test_audit_receives_baseline_skip_and_shared_filter_metadata(self):
        self.add("과거 기준선")
        self.run_mirror()
        self.add("password=pretend-value")
        self.add("참고: lrm-test")
        for _ in range(3):
            self.run_mirror()
        state = json.loads((self.home / "board-mirror.state.json").read_text())
        self.assertEqual(["id-1"], state["_baseline_ids"]["lrm-test.1"])
        self.assertGreater(state["_baseline_tracking_at"], 0)
        self.assertEqual(["id-2"], state["_skipped_ids"]["lrm-test.1"])
        self.assertEqual([], self.queue())

    def test_poller_skips_mirror_without_reserve(self):
        script = (ROOT / "hubs/scripts/slack_poll.sh").read_text()
        tail = script[script.index('left=$((TOTAL', script.index('# 허브 보고 전송')):script.rindex('exit "$RC"')]
        for left in (1, 2, 3, 40):
            capture = self.home / "calls"
            setup = 'TOTAL="$1"; T0=0; DIR="$2"; PY=python_call; date() { echo 0; }; python_call() { echo "$*" >> "$DIR/calls"; };\n'
            subprocess.run(["bash", "-c", setup + tail, "test", str(left), str(self.home)], check=True)
            calls = capture.read_text()
            self.assertEqual(left > 2, "board_mirror.py" in calls)
            if left > 2:
                self.assertIn(f"--budget-seconds {left - 2}", calls)
            capture.unlink()


if __name__ == "__main__":
    unittest.main()
