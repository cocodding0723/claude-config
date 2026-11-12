"""첨부 시험: 일반·결정·스레드 지정 보고의 Slack 파일 첨부, 본문 경로 경고, 첨부 실패 처리. 가짜 알림 스크립트·가짜 미리보기만 씀."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import hub_report  # noqa: E402
import report_sender  # noqa: E402

FAKE = os.path.join(HERE, "fake_notify.py")
PREVIEW = os.path.join(HERE, "fake_preview.sh")
CLI = os.path.join(SCRIPTS, "hub_report.py")
TOPIC_TS = "1700000000.000001"


class AttachTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.p = lambda n: os.path.join(self.dir.name, n)
        self.conf = report_sender.Conf(outbox=self.p("box.jsonl"), state=self.p("state.json"), notify=FAKE, preview=PREVIEW,
                                       ledger=self.p("ledger.tsv"), sync_state=self.p("sync.json"), sent=self.p("sent.jsonl"),
                                       log=self.p("log.txt"), backoff_s=0)
        os.environ["FAKE_DIR"] = self.dir.name
        self.addCleanup(os.environ.pop, "FAKE_DIR", None)
        os.environ.pop("FAKE_PREVIEW_FAIL", None)
        self.addCleanup(os.environ.pop, "FAKE_PREVIEW_FAIL", None)
        pathlib.Path(self.p("sync.json")).write_text(json.dumps({"ts": "1699999999.000001", "channel": "D1"}))
        self.png, self.html = self.make("a.png", "png"), self.make("b.html", "<p>x</p>")

    def make(self, name, body):
        path = self.p(name)
        pathlib.Path(path).write_text(body)
        return path

    def report(self, text="수정 완료", kind="done", files=(), **kw):
        rep = hub_report.make_report("ui-layer", "C0012", kind, text, list(files), **kw)
        return hub_report.add(self.conf.outbox, rep, 1.0, False)[0]

    def records(self):
        try:
            with open(self.p("calls.jsonl"), encoding="utf-8") as f:
                return [json.loads(ln) for ln in f]
        except FileNotFoundError:
            return []

    def calls(self):
        return [r["args"] for r in self.records()]

    def uploads(self):  # 미리보기 PNG 는 임시 폴더라 호출 뒤 사라지므로 파일 존재 대신 가짜 스크립트의 업로드 표시를 씀
        return [r["args"] for r in self.records() if r["upload"]]

    def lines(self):
        return [c[-1] for c in self.calls() if c[-1].startswith("첨부 실패")]

    def set_fake(self, name, value):
        pathlib.Path(self.p(name)).write_text(str(value))

    def state(self):
        return report_sender.load_state(self.conf.state)

    def log_text(self):
        with open(self.conf.log, encoding="utf-8") as f:
            return f.read()

    def test_general_report_attaches_files_to_topic_thread_with_html_preview(self):
        rep = self.report(files=[self.png, self.html])
        res = report_sender.run(self.conf)
        self.assertEqual((res["status"], res["sent"], res["failed"]), ("done", 1, 0))
        calls = self.calls()
        self.assertIn("progress", calls[0])  # 주제 시작 → 본문 → 파일 → 진행 줄 순서
        self.assertNotIn("참고:", calls[1][-1])  # 경로 텍스트 대신 첨부
        ups = self.uploads()
        self.assertEqual([os.path.basename(u[-1]) for u in ups], ["a.png", "b.html", "b-preview.png"])
        for u in ups:
            self.assertEqual(u[u.index("--thread") + 1], TOPIC_TS)  # 본문과 같은 주제 스레드
        self.assertEqual(calls.index(ups[0]), 2)
        self.assertIn("slack.com/archives", calls[-1][-1])  # 마지막은 진행 줄
        self.assertEqual(self.state()["reports"][rep["id"]]["att_done"], [self.png, self.html, self.html + "#preview"])

    def test_decide_card_first_then_files_in_card_thread(self):
        self.report(text="알림 시각 정하기\n배경", kind="decide", files=[self.png], options="1=9시,2=10시", recommend="1", decision_id="D-0101")
        res = report_sender.run(self.conf)
        self.assertEqual(res["sent"], 1)
        calls = self.calls()
        self.assertEqual(calls[0][calls[0].index("--kind") + 1], "decide")
        self.assertNotIn(self.png, calls[0])  # 카드에는 파일을 달지 않음(버튼 유지)
        up = self.uploads()[0]
        self.assertEqual(up[up.index("--thread") + 1], "1700000000.000001")  # 카드 ts
        self.assertEqual(calls.index(up), 1)

    def test_thread_pinned_report_attaches_in_that_thread(self):
        self.report(kind="warn", files=[self.png], thread="1730790000.123456")
        report_sender.run(self.conf)
        up = self.uploads()[0]
        self.assertEqual(up[up.index("--thread") + 1], "1730790000.123456")
        self.assertEqual(len(self.calls()), 2)  # 본문 + 파일(주제·진행 줄 없음)

    def test_missing_file_is_permanent_and_body_still_sent(self):
        rep = self.report(files=[self.p("none.pdf"), self.png])
        res = report_sender.run(self.conf)
        self.assertEqual((res["status"], res["sent"], res["failed"]), ("done", 1, 0))
        self.assertEqual(self.lines(), ["첨부 실패: none.pdf — 파일 없음"])
        self.assertEqual([os.path.basename(u[-1]) for u in self.uploads()], ["a.png"])  # 뒤 파일은 계속 올림
        self.assertTrue(self.state()["reports"][rep["id"]]["sent"])
        self.assertNotIn(rep["id"], self.state()["bad"])  # 보고는 중단하지 않음

    def test_oversize_file_is_permanent(self):
        orig = hub_report.MAX_FILE_BYTES
        hub_report.MAX_FILE_BYTES = 2
        self.addCleanup(setattr, hub_report, "MAX_FILE_BYTES", orig)
        self.report(files=[self.png])
        res = report_sender.run(self.conf)
        self.assertEqual(res["sent"], 1)
        self.assertEqual(self.uploads(), [])
        self.assertTrue(self.lines()[0].startswith("첨부 실패: a.png — 크기 초과"))

    def test_transient_upload_failure_retries_without_reposting_body_or_done_files(self):
        rep = self.report(files=[self.png, self.html])
        self.set_fake("fail.up.n", 2)  # a.png 업로드 2번 실패
        res = report_sender.run(self.conf)
        self.assertEqual((res["status"], res["sent"], res["failed"]), ("partial", 0, 1))
        self.assertIn("첨부 실패: a.png", res["lines"][0])
        self.assertFalse(self.state()["reports"][rep["id"]].get("sent"))
        bodies = [c for c in self.calls() if "--kind" in c and c[c.index("--kind") + 1] == "done"]
        self.assertEqual(len(bodies), 1)
        res = report_sender.run(self.conf)  # 2번째 실패
        self.assertEqual(res["failed"], 1)
        res = report_sender.run(self.conf)
        self.assertEqual((res["sent"], res["failed"]), (1, 0))
        bodies = [c for c in self.calls() if "--kind" in c and c[c.index("--kind") + 1] == "done"]
        self.assertEqual(len(bodies), 1)  # 본문 재전송 없음
        ok_uploads = self.state()["reports"][rep["id"]]["att_done"]
        self.assertEqual(ok_uploads, [self.png, self.html, self.html + "#preview"])
        self.assertEqual(self.lines(), [])  # 일시 실패는 스레드 줄 없이 재시도

    def test_script_permanent_rc_posts_one_line_and_continues(self):
        self.report(files=[self.png])
        self.set_fake("fail.up.n", 1)
        self.set_fake("fail.up.rc", 2)
        self.set_fake("fail.up.msg", "파일 형식 거부")
        res = report_sender.run(self.conf)
        self.assertEqual((res["sent"], res["failed"]), (1, 0))
        self.assertEqual(self.lines(), ["첨부 실패: a.png — 파일 형식 거부"])

    def test_preview_failure_is_skipped_and_report_continues(self):
        os.environ["FAKE_PREVIEW_FAIL"] = "1"
        rep = self.report(files=[self.html])
        res = report_sender.run(self.conf)
        self.assertEqual((res["sent"], res["failed"]), (1, 0))
        self.assertEqual([os.path.basename(u[-1]) for u in self.uploads()], ["b.html"])
        self.assertIn("미리보기 생략", self.log_text())
        self.assertIn(self.html + "#preview", self.state()["reports"][rep["id"]]["att_done"])

    def test_missing_preview_script_is_skipped(self):
        self.conf.preview = self.p("no-such.sh")
        self.report(files=[self.html])
        self.assertEqual(report_sender.run(self.conf)["sent"], 1)
        self.assertEqual(len(self.uploads()), 1)

    def test_sender_logs_warning_for_path_only_body(self):
        self.report(text="결과는 /Users/me/out/report.html 참고")
        report_sender.run(self.conf)
        self.assertIn("본문에 로컬 경로(/Users/me/out/report.html)만 있고 첨부 없음", self.log_text())

    def test_sender_no_warning_when_attached(self):
        self.report(text="결과는 docs/a.png 참고", files=[self.png])
        report_sender.run(self.conf)
        self.assertNotIn("경고:", self.log_text())


class PathRuleTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {**os.environ, "LRM_REPORT_OUTBOX": os.path.join(self.dir.name, "box.jsonl")}

    def cli(self, text, *files):
        a = ["add", "--hub", "ui-layer", "--task", "C0012", "--kind", "done", "--text", text]
        for f in files:
            a += ["--file", f]
        return subprocess.run([sys.executable, "-I", CLI, *a], env=self.env, capture_output=True, text=True, cwd=self.dir.name)

    def test_local_path_rule(self):
        found = hub_report.local_paths("절대 /Users/me/a.html 홈 ~/b.mp4 상대 ./c.png ../d.pdf docs/e.md 파일 f.txt")
        self.assertEqual(found, ["/Users/me/a.html", "~/b.mp4", "./c.png", "../d.pdf", "docs/e.md", "f.txt"])
        self.assertEqual(hub_report.local_paths("https://x.com/a.html 링크, v1.2 수정, 버튼 정렬"), [])
        self.assertEqual(hub_report.local_paths("docs/a.png와 비교"), ["docs/a.png"])

    def test_add_warns_on_path_only_body_with_exit_0(self):
        r = self.cli("결과 /Users/me/report.html 참고")
        self.assertEqual(r.returncode, 0)
        self.assertIn("경고: 본문에 로컬 경로(/Users/me/report.html)만 있고 --file 첨부 없음", r.stderr)

    def test_add_no_warning_with_file_or_plain_text(self):
        pathlib.Path(self.dir.name, "r.html").write_text("x")
        self.assertEqual(self.cli("결과 r.html 참고", "r.html").stderr, "")
        self.assertEqual(self.cli("버튼 정렬 수정").stderr, "")

    def test_add_warns_on_missing_file_and_still_records(self):
        r = self.cli("완료", "nope.pdf")
        self.assertEqual(r.returncode, 0)
        self.assertIn("첨부 불가(파일 없음)", r.stderr)
        self.assertEqual(len(hub_report.read_outbox(self.env["LRM_REPORT_OUTBOX"])), 1)

    def test_existing_relative_file_is_stored_absolute(self):
        pathlib.Path(self.dir.name, "r.html").write_text("x")
        self.cli("완료", "r.html")
        stored = hub_report.read_outbox(self.env["LRM_REPORT_OUTBOX"])[0]["files"][0]
        self.assertEqual(os.path.realpath(stored), os.path.realpath(os.path.join(self.dir.name, "r.html")))
        self.assertTrue(os.path.isabs(stored))


if __name__ == "__main__":
    unittest.main()
