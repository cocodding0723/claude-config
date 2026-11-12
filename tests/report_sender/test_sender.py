"""report_sender 시험: 주제 스레드·진행 줄·같은 작업 답글·실패 재시도·3회 경고·동시 실행·decide 카드. 가짜 전송 스크립트만 씀."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import hub_report  # noqa: E402
import report_sender  # noqa: E402

FAKE = os.path.join(HERE, "fake_notify.py")
PARENT = "1699999999.000001"  # ledger_sync 가 저장한 진행 스레드 부모 ts
WARN_TITLE = "허브 보고 전송 중단"


class SenderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.p = lambda n: os.path.join(self.dir.name, n)
        p = self.p
        self.conf = report_sender.Conf(outbox=p("box.jsonl"), state=p("state.json"), notify=FAKE, ledger=p("ledger.tsv"),
                                       sync_state=p("sync.json"), sent=p("sent.jsonl"), log=p("log.txt"), backoff_s=0)
        os.environ["FAKE_DIR"] = self.dir.name
        self.addCleanup(os.environ.pop, "FAKE_DIR", None)
        with open(p("sync.json"), "w") as f:
            json.dump({"ts": PARENT, "channel": "D1"}, f)
        with open(p("ledger.tsv"), "w", encoding="utf-8") as f:
            f.write("id\t접수\t요청(원문 요약)\t상태\t담당\t진행·증거\t갱신\nC0012\t11-12\t알림 시각 통일\t진행\tui\t\t2026-11-12 09:00\n")

    def report(self, text="수정 완료", task="C0012", kind="done", hub="ui-layer", files=(), **kw):
        rep = hub_report.make_report(hub, task, kind, text, list(files), **kw)
        return hub_report.add(self.conf.outbox, rep, 1.0, False)[0]

    def calls(self):
        try:
            with open(self.p("calls.jsonl"), encoding="utf-8") as f:
                return [json.loads(ln)["args"] for ln in f]
        except FileNotFoundError:
            return []

    def warns(self):
        return [c for c in self.calls() if "warn" in c and WARN_TITLE in c[-1]]

    def state(self):
        return report_sender.load_state(self.conf.state)

    def set_fake(self, name, value):
        with open(self.p(name), "w") as f:
            f.write(str(value))

    def test_first_report_makes_topic_thread_and_one_progress_line(self):
        rep = self.report(files=["docs/a.html"])
        res = report_sender.run(self.conf)
        self.assertEqual((res["status"], res["sent"], res["failed"]), ("done", 1, 0))
        topic, body, line = self.calls()
        self.assertIn("progress", topic)
        self.assertIn("알림 시각 통일", topic[-1])  # 주제 시작 메시지에 장부 요청 이름
        self.assertEqual(body[body.index("--thread") + 1], "1700000000.000001")  # 본문은 주제 스레드 답글
        self.assertEqual(body[body.index("--kind") + 1], "done")
        self.assertIn("참고: docs/a.html", body[-1])
        self.assertEqual(line[line.index("--thread") + 1], PARENT)  # 진행 줄은 진행 스레드 부모의 답글
        self.assertIn("https://slack.com/archives/D1/p1700000000000001", line[-1])
        self.assertEqual(len(line[-1].splitlines()), 1)
        self.assertTrue(self.state()["reports"][rep["id"]]["sent"])
        self.assertIn(f"전송 {rep['id']} ui-layer done → 주제 C0012 스레드 1700000000.000001", res["lines"][0])

    def test_second_report_same_task_replies_in_same_thread(self):
        self.report(text="첫 보고")
        report_sender.run(self.conf)
        self.report(text="둘째 보고", kind="block")
        report_sender.run(self.conf)
        calls = self.calls()
        self.assertEqual(sum("progress" in c and "--thread" not in c for c in calls), 1)  # 주제 시작은 1번
        second = calls[3]
        self.assertEqual(second[second.index("--thread") + 1], "1700000000.000001")
        self.assertEqual(second[second.index("--kind") + 1], "warn")  # block → warn 양식

    def test_other_task_gets_its_own_topic_thread(self):
        self.report(task="C0012")
        self.report(task="없음")
        report_sender.run(self.conf)
        self.assertEqual(set(self.state()["topics"]), {"C0012", "hub:ui-layer"})

    def test_sent_report_not_resent(self):
        self.report()
        report_sender.run(self.conf)
        n = len(self.calls())
        res = report_sender.run(self.conf)
        self.assertEqual((res["status"], len(self.calls())), ("idle", n))

    def test_failure_keeps_unsent_and_retries_next_round(self):
        rep = self.report()
        self.set_fake("fail.n", 1)
        res = report_sender.run(self.conf)
        self.assertEqual((res["status"], res["sent"], res["failed"]), ("partial", 0, 1))
        self.assertFalse(self.state()["reports"][rep["id"]].get("sent"))
        res = report_sender.run(self.conf)
        self.assertEqual((res["sent"], res["failed"]), (1, 0))
        self.assertTrue(self.state()["reports"][rep["id"]]["sent"])

    def test_progress_line_failure_retry_does_not_repost_body(self):
        self.report(text="첫 보고")
        report_sender.run(self.conf)
        rep2 = self.report(text="둘째 보고")
        orig, tripped = report_sender.run_notify, []

        def flaky(conf, args, deadline):  # 진행 줄 호출만 한 번 실패
            if "--thread" in args and args[args.index("--thread") + 1] == PARENT and not tripped:
                tripped.append(1)
                raise report_sender.NotifyError("줄 실패")
            return orig(conf, args, deadline)

        report_sender.run_notify = flaky
        self.addCleanup(setattr, report_sender, "run_notify", orig)
        self.assertEqual(report_sender.run(self.conf)["failed"], 1)
        self.assertEqual(report_sender.run(self.conf)["sent"], 1)
        bodies = [c for c in self.calls() if "--kind" in c and "둘째 보고" in c[-1]]
        self.assertEqual(len(bodies), 1)  # 본문은 한 번만
        self.assertTrue(self.state()["reports"][rep2["id"]]["sent"])

    def test_retry_cap_stops_with_one_warning_in_topic_thread(self):
        self.conf.fail_limit = 3
        rep = self.report()
        report_sender.run(self.conf)  # 주제·본문·진행 줄 정상 전송 후 새 보고로 실패 시험
        rep2 = self.report(text="둘째 보고")
        self.set_fake("fail.all", 1)
        report_sender.run(self.conf)
        report_sender.run(self.conf)
        self.assertEqual(self.warns(), [])  # 아직 2회
        self.set_fake("fail.all", "")
        self.set_fake("fail.n", 1)  # 3회째 본 전송만 실패하고 경고 전송은 성공
        res = report_sender.run(self.conf)
        self.assertEqual(res["stopped"], 1)
        warn, = self.warns()
        self.assertIn("3회 연속", warn[-1])
        self.assertEqual(warn[warn.index("--thread") + 1], "1700000000.000001")  # 원래 주제 스레드
        self.assertIn(rep2["id"], self.state()["bad"])
        self.assertIn(rep2["id"], self.state()["warned"])
        self.set_fake("fail.all", 1)
        before = len(self.calls())
        res = report_sender.run(self.conf)
        self.assertEqual((res["pending"], res["failed"], len(self.calls())), (0, 0, before))  # 재시도 대상 아님
        self.assertEqual(len(self.warns()), 1)
        self.assertIn(rep["id"], self.state()["reports"])

    def test_exit_code_2_is_permanent_one_attempt_one_warning(self):
        rep = self.report()
        self.set_fake("fail.all", 1)
        self.set_fake("fail.rc", 2)
        self.set_fake("fail.msg", "--kind 형식 오류")
        res = report_sender.run(self.conf)
        self.assertEqual((res["failed"], res["stopped"], res["status"]), (0, 1, "done"))
        self.assertIn(rep["id"], self.state()["bad"])
        sends = lambda: [c for c in self.calls() if c not in self.warns()]  # noqa: E731
        self.assertEqual(len(sends()), 1)  # 전송 시도 1회(경고 전송도 실패하는 중이라 경고만 다음 회차에 재시도)
        report_sender.run(self.conf)
        self.assertEqual(len(sends()), 1)
        self.assertIn("재시도해도 같은 결과", self.warns()[0][-1])
        self.set_fake("fail.all", "")
        report_sender.run(self.conf)
        report_sender.run(self.conf)
        self.assertEqual(len(sends()), 1)
        self.assertIn(rep["id"], self.state()["warned"])
        n = len(self.warns())
        report_sender.run(self.conf)
        self.assertEqual(len(self.warns()), n)  # 경고가 나간 뒤에는 더 보내지 않음

    def test_missing_env_exit_2_is_transient(self):
        self.report()
        self.set_fake("fail.all", 1)
        self.set_fake("fail.rc", 2)
        self.set_fake("fail.msg", "slack-bot.env 없음 — 사용자 설정 필요")
        res = report_sender.run(self.conf)
        self.assertEqual((res["failed"], res.get("stopped", 0)), (1, 0))
        self.assertEqual(self.state()["bad"], {})

    def test_transient_failures_back_off(self):
        self.conf.backoff_s = 60
        self.report()
        self.set_fake("fail.all", 1)
        self.assertEqual(report_sender.run(self.conf)["failed"], 1)
        n = len(self.calls())
        res = report_sender.run(self.conf)  # 대기 시간 안 = 호출 없음
        self.assertEqual((res["failed"], len(self.calls())), (0, n))

    def test_queued_invalid_decision_ids_are_stopped_without_resend(self):
        """설치 전 접수돼 큐·상태에 이미 걸린 형식 오류 결정 카드(62회 재시도 중이던 것)가 다음 회차에 영구 실패로 처리됨"""
        ids = []
        for did in ("D-data design!", "D-" + "x" * 17):
            rec = {"hub": "data-design", "task": "없음", "kind": "decide", "text": "제목\n배경", "files": [],
                   "options": "1=a,2=b", "recommend": "1", "decision_id": did}
            rec = {"id": hub_report.report_id(rec), "at": 1, **rec}
            ids.append(rec["id"])
            with open(self.conf.outbox, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        st = {"topics": {}, "reports": {i: {"fails": 62, "error": "--decision-id 형식 오류"} for i in ids}, "warned": {ids[0]: 1}, "bad": {}}
        report_sender.save_state(self.conf.state, st)
        res = report_sender.run(self.conf)
        self.assertEqual((res["failed"], res["stopped"], res["status"]), (0, 2, "done"))
        self.assertEqual(sorted(self.state()["bad"]), sorted(ids))
        self.assertEqual([c for c in self.calls() if "decide" in c], [])  # 카드 재전송 없음
        self.assertEqual(len(self.warns()), 1)  # 이미 경고한 건은 다시 경고하지 않음
        self.assertEqual(report_sender.run(self.conf)["pending"], 0)
        self.assertEqual(len(self.warns()), 1)

    def test_decide_uses_button_card_form_without_thread(self):
        self.report(text="알림 시각 정하기\n지금은 시각이 제각각", kind="decide",
                    options="1=오전 9시,2=오전 10시", recommend="1", decision_id="D-0101")
        report_sender.run(self.conf)
        card, line = self.calls()
        self.assertEqual(card[card.index("--kind") + 1], "decide")
        self.assertEqual(card[card.index("--options") + 1], "1=오전 9시,2=오전 10시")
        self.assertEqual(card[card.index("--recommend") + 1], "1")
        self.assertEqual(card[card.index("--decision-id") + 1], "D-0101")
        self.assertNotIn("--thread", card)  # 버튼 카드는 스레드 답글로 못 보냄
        self.assertTrue(card[-1].startswith("알림 시각 정하기"))
        self.assertEqual(self.state()["topics"], {})
        self.assertIn("결정 카드", line[-1])

    def test_missing_progress_parent_skips_line_but_marks_sent(self):
        os.unlink(self.p("sync.json"))
        rep = self.report()
        res = report_sender.run(self.conf)
        self.assertEqual(res["sent"], 1)
        self.assertEqual(len(self.calls()), 2)  # 주제 + 본문만
        self.assertIn("진행 줄 생략", res["lines"][0])
        self.assertTrue(self.state()["reports"][rep["id"]]["sent"])

    def test_failed_topic_blocks_later_reports_of_same_topic_only(self):
        self.report(text="A", task="C0012")
        self.report(text="B", task="C0012")
        self.report(text="C", task="C0099")
        self.set_fake("fail.n", 1)
        res = report_sender.run(self.conf)
        self.assertEqual((res["failed"], res["sent"]), (1, 1))  # C0012 은 첫 실패로 막히고 C0099 만 진행

    def test_concurrent_runs_only_one_proceeds(self):
        self.report()
        self.set_fake("sleep", 0.6)
        results = []
        ts = [threading.Thread(target=lambda: results.append(report_sender.run(self.conf)["status"])) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(results), ["busy", "done"])
        self.assertEqual(sum("progress" in c and "--thread" not in c for c in self.calls()), 1)

    def test_dry_run_sends_nothing_and_writes_no_state(self):
        self.report()
        res = report_sender.run(self.conf, dry=True)
        self.assertEqual(res["status"], "dry-run")
        self.assertEqual(self.calls(), [])
        self.assertFalse(os.path.exists(self.conf.state))

    def test_sender_passes_no_slack_secrets_to_notify(self):
        from unittest import mock
        with mock.patch.dict(os.environ):  # 시험 환경에 SLACK_* 가 있어도(로컬 API 주소·토큰) 결과가 같도록 비밀 변수를 비움
            for k in ("SLACK_BOT_TOKEN", "SLACK_USER_ID", "SLACK_APP_TOKEN", "SLACK_SIGNING_SECRET"):
                os.environ.pop(k, None)
            os.environ["SLACK_API_BASE"] = "http://127.0.0.1:9"
            self.report()
            report_sender.run(self.conf)
        with open(self.p("calls.jsonl"), encoding="utf-8") as f:
            self.assertTrue(all(json.loads(ln)["slack_env"] == 0 for ln in f))

    def test_cli_once_prints_one_processing_line_per_report(self):
        self.report()
        env = {**os.environ, "LRM_HUB_DIR": self.dir.name, "LRM_REPORT_OUTBOX": self.conf.outbox, "LRM_REPORT_STATE": self.conf.state,
               "LRM_REPORT_NOTIFY": FAKE, "LRM_LEDGER": self.conf.ledger, "LRM_LEDGER_STATE": self.conf.sync_state,
               "LRM_REPORT_SENT": self.conf.sent, "LRM_REPORT_LOG": self.conf.log}
        script = os.path.join(SCRIPTS, "report_sender.py")
        r = subprocess.run([sys.executable, "-I", script, "--once"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        print("\n처리 로그:", r.stdout.splitlines()[0])
        self.assertIn("전송기: done", r.stdout)
        self.assertEqual(subprocess.run([sys.executable, "-I", script], env=env, capture_output=True).returncode, 2)


    # --- Codex 리뷰 회귀(3·4·5) ---
    def test_corrupt_state_halts_without_reposting_and_warns_once(self):
        self.report()
        report_sender.run(self.conf)
        n = len(self.calls())
        with open(self.conf.state, "w") as f:
            f.write("{broken")
        res = report_sender.run(self.conf)
        self.assertEqual(res["status"], "halted")
        self.assertIn("복구", res["guide"] + "복구")
        self.assertEqual(len(self.calls()), n + 1)  # 주제·본문 재게시 없이 경고 카드 1건만
        self.assertIn("허브 보고 전송 중단", self.calls()[-1][-1])
        report_sender.run(self.conf)
        report_sender.run(self.conf)
        self.assertEqual(len(self.calls()), n + 1)  # 같은 손상에는 경고 1회
        with open(self.conf.state) as f:
            self.assertEqual(f.read(), "{broken")  # 손상 파일을 덮어쓰지 않음

    def test_unreadable_state_halts_and_wrong_shape_halts(self):
        self.report()
        with open(self.conf.state, "w") as f:
            f.write("[]")
        self.assertEqual(report_sender.run(self.conf)["status"], "halted")
        os.chmod(self.conf.state, 0)
        self.addCleanup(os.chmod, self.conf.state, 0o600)
        if os.access(self.conf.state, os.R_OK):  # root 면 권한 시험 불가
            return
        self.assertEqual(report_sender.run(self.conf, dry=True)["status"], "halted")
        self.assertEqual(len([c for c in self.calls() if "progress" in c]), 0)

    def test_missing_state_file_is_initial_state(self):
        self.assertFalse(os.path.exists(self.conf.state))
        self.report()
        self.assertEqual(report_sender.run(self.conf)["status"], "done")

    def test_failing_topic_does_not_starve_healthy_topic(self):
        for i in range(12):
            self.report(text=f"bad-topic-{i}", task="C1")
        self.report(text="healthy", task="C2")
        orig = report_sender.run_notify

        def fail_c1(conf, args, deadline):
            if args[-1].startswith("C1 보고"):
                raise report_sender.NotifyError("permanent C1 failure")
            return orig(conf, args, deadline)

        report_sender.run_notify = fail_c1
        self.addCleanup(setattr, report_sender, "run_notify", orig)
        res = report_sender.run(self.conf)
        self.assertEqual(res["sent"], 1)  # 첫 라운드에 C2 가 나감
        self.assertTrue(any("healthy" in c[-1] for c in self.calls()))

    def test_attempt_cap_counts_real_attempts(self):
        for i in range(5):
            self.report(text=f"r{i}", task=f"C{i}")
        self.conf.max_per_run = 2
        res = report_sender.run(self.conf)
        self.assertEqual(res["sent"], 2)
        self.assertEqual(report_sender.run(self.conf)["sent"], 2)

    def test_invalid_record_isolated_and_later_reports_still_sent(self):
        with open(self.conf.outbox, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "bad", "hub": "ui", "kind": "done", "text": "x"}) + "\n")  # task 없음
        good = self.report(text="뒤 보고")
        res = report_sender.run(self.conf)
        self.assertEqual((res["sent"], res["bad"]), (1, 1))
        self.assertTrue(self.state()["reports"][good["id"]]["sent"])
        with open(self.conf.outbox + ".corrupt", encoding="utf-8") as f:
            self.assertIn('"id": "bad"', f.read())
        report_sender.run(self.conf)
        with open(self.conf.outbox + ".corrupt", encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 1)  # 다시 돌려도 중복 격리 없음

    def test_secret_in_legacy_record_never_reaches_slack_or_log(self):
        bad = "xox" + "b-review-sentinel"
        with open(self.conf.outbox, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "old", "at": 1, "hub": bad, "task": bad, "kind": "done", "text": "x", "files": []}) + "\n")
        report_sender.run(self.conf)
        self.assertEqual(self.calls(), [])
        self.assertNotIn(bad, open(self.conf.log, encoding="utf-8").read() if os.path.exists(self.conf.log) else "")


    # --- Codex 재확인 회귀(1·2·3·4) ---
    def test_record_without_files_key_does_not_stop_later_reports(self):
        with open(self.conf.outbox, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "nf", "at": 1, "hub": "ui", "task": "C1", "kind": "done", "text": "x"}) + "\n")
        good = self.report(text="뒤 보고")
        res = report_sender.run(self.conf)
        self.assertEqual((res["sent"], res["bad"]), (1, 1))
        self.assertTrue(self.state()["reports"][good["id"]]["sent"])

    def test_emptied_or_malformed_state_halts_instead_of_reposting(self):
        self.report()
        report_sender.run(self.conf)
        n = len(self.calls())
        good = open(self.conf.state, encoding="utf-8").read()
        rid = next(iter(json.loads(good)["reports"]))
        for i, bad in enumerate(("{}", json.dumps({"topics": {}, "reports": {rid: None}, "warned": {}}),
                                 json.dumps({"topics": {"C0012": "x"}, "reports": {}, "warned": {}}),
                                 json.dumps({"topics": {}, "reports": {rid: {"sent": "yes"}}, "warned": {}}),
                                 json.dumps({"topics": {}, "reports": [], "warned": {}}))):
            with open(self.conf.state, "w", encoding="utf-8") as f:
                f.write(bad)
            res = report_sender.run(self.conf)
            self.assertEqual(res["status"], "halted", bad)
            self.assertFalse(any("progress" in c and "--thread" not in c for c in self.calls()[n + i:]), bad)  # 주제·본문 재게시 없음
        self.assertEqual(len([c for c in self.calls() if "허브 보고 전송 중단" in c[-1]]), len(self.calls()) - n)  # 경고만 늘어남

    def test_halt_warning_respects_round_deadline(self):
        import time
        self.report()
        with open(self.conf.state, "w") as f:
            f.write("{broken")
        self.set_fake("sleep", 0.75)
        self.conf.budget_s = 0.15
        t0 = time.monotonic()
        res = report_sender.run(self.conf)
        self.assertEqual(res["status"], "halted")
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertFalse(os.path.exists(self.conf.state + ".warned"))  # 경고가 못 나갔으니 다음 회차에 재시도

    def test_sender_repairs_missing_trailing_newline_of_whole_record(self):
        rep = self.report()
        with open(self.conf.outbox, encoding="utf-8") as f:
            line = f.read().rstrip("\n")
        with open(self.conf.outbox, "w", encoding="utf-8") as f:
            f.write(line)  # 정상 JSON 인데 끝 개행만 없음
        res = report_sender.run(self.conf)
        self.assertEqual(res["sent"], 1)
        self.assertTrue(self.state()["reports"][rep["id"]]["sent"])
        with open(self.conf.outbox, encoding="utf-8") as f:
            self.assertTrue(f.read().endswith("\n"))

    def test_sender_quarantines_truncated_tail_and_continues(self):
        good = self.report()
        with open(self.conf.outbox, "a", encoding="utf-8") as f:
            f.write('{"id":"cut')
        res = report_sender.run(self.conf)
        self.assertEqual(res["sent"], 1)
        with open(self.conf.outbox + ".corrupt", encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"id":"cut\n')
        self.assertTrue(self.state()["reports"][good["id"]]["sent"])


    # --- Codex 3차 회귀(2·3) ---
    def test_repair_lock_wait_respects_budget(self):
        import fcntl
        import time
        self.report()
        with open(self.conf.outbox, encoding="utf-8") as f:
            line = f.read().rstrip("\n")
        with open(self.conf.outbox, "w", encoding="utf-8") as f:
            f.write(line)  # 끝 개행 없음 → 복구 필요
        holder = open(self.conf.outbox + ".lock", "a")
        fcntl.flock(holder, fcntl.LOCK_EX)  # 다른 쪽이 잠금을 쥠
        self.addCleanup(holder.close)
        self.conf.budget_s = 0.05
        t0 = time.monotonic()
        res = report_sender.run(self.conf)
        self.assertLess(time.monotonic() - t0, 0.4)  # 800ms 대기 없이 예산 안에서 포기
        self.assertEqual(res["sent"], 0)  # 개행 없는 끝줄은 복구 전이라 읽지 않음(유실 아님)
        fcntl.flock(holder, fcntl.LOCK_UN)
        self.conf.budget_s = 40
        self.assertEqual(report_sender.run(self.conf)["sent"], 1)  # 다음 회차에 복구되고 전송

    def test_unreleased_repair_lock_never_blocks_now_run_forever(self):
        import fcntl
        import time
        self.report()
        with open(self.conf.outbox, encoding="utf-8") as f:
            line = f.read().rstrip("\n")
        with open(self.conf.outbox, "w", encoding="utf-8") as f:
            f.write(line)
        holder = open(self.conf.outbox + ".lock", "a")
        fcntl.flock(holder, fcntl.LOCK_EX)
        self.addCleanup(holder.close)
        self.conf.budget_s = 0.3
        t0 = time.monotonic()
        report_sender.run(self.conf)
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_empty_body_ts_state_halts_instead_of_silent_sent(self):
        rep = self.report()
        report_sender.run(self.conf)
        for bad in ({"body_ts": ""}, {"body_ts": "abc"}, {"sent": 1}, {"sent": 1, "body_ts": "1700000000.000002"},
                    {"body_ts": "1700000000.000002", "line_ts": "x"}):
            with open(self.conf.state, "w", encoding="utf-8") as f:
                json.dump({"topics": {"C0012": {"ts": "1700000000.000001"}}, "reports": {rep["id"]: bad}, "warned": {}}, f)
            n = len(self.calls())
            res = report_sender.run(self.conf)
            self.assertEqual(res["status"], "halted", bad)
            self.assertEqual(res["sent"], 0)
            self.assertFalse(any("--thread" in c and "수정 완료" in c[-1] for c in self.calls()[n:]), bad)

    def test_empty_topic_ts_or_missing_topic_halts(self):
        rep = self.report()
        report_sender.run(self.conf)
        good = json.load(open(self.conf.state, encoding="utf-8"))
        for mutate in (lambda st: st["topics"]["C0012"].update(ts=""), lambda st: st["topics"].pop("C0012")):
            st = json.loads(json.dumps(good))
            mutate(st)
            with open(self.conf.state, "w", encoding="utf-8") as f:
                json.dump(st, f)
            self.assertEqual(report_sender.run(self.conf)["status"], "halted")
        with open(self.conf.state, "w", encoding="utf-8") as f:
            json.dump(good, f)
        self.assertEqual(report_sender.run(self.conf)["status"], "idle")  # 정상 상태는 그대로 통과
        self.assertTrue(self.state()["reports"][rep["id"]]["sent"])


if __name__ == "__main__":
    unittest.main()
