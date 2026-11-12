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
WARN_TITLE = "허브 보고 전송 실패"


class SenderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.p = lambda n: os.path.join(self.dir.name, n)
        p = self.p
        self.conf = report_sender.Conf(outbox=p("box.jsonl"), state=p("state.json"), notify=FAKE, ledger=p("ledger.tsv"),
                                       sync_state=p("sync.json"), sent=p("sent.jsonl"), log=p("log.txt"))
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

    def test_three_failures_send_exactly_one_warning(self):
        rep = self.report()
        self.set_fake("fail.all", 1)
        report_sender.run(self.conf)
        report_sender.run(self.conf)
        self.assertEqual(self.warns(), [])  # 아직 2회
        self.set_fake("fail.all", "")
        self.set_fake("fail.n", 1)  # 3회째 본 전송만 실패하고 경고 전송은 성공
        self.assertEqual(report_sender.run(self.conf)["failed"], 1)
        self.assertEqual(len(self.warns()), 1)
        self.assertIn("3회 연속", self.warns()[0][-1])
        self.assertIn(rep["id"], self.state()["warned"])
        self.set_fake("fail.all", 1)
        report_sender.run(self.conf)
        report_sender.run(self.conf)
        self.assertEqual(len(self.warns()), 1)  # 4·5회째 실패에도 경고는 그대로 1건

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


if __name__ == "__main__":
    unittest.main()
