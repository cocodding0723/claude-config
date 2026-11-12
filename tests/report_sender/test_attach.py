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
        names = [os.path.basename(u[-1]) for u in ups]
        self.assertEqual((names[:2], names[2].endswith("-b-preview.png")), (["a.png", "b.html"], True))
        for u in ups:
            self.assertEqual(u[u.index("--thread") + 1], TOPIC_TS)  # 본문과 같은 주제 스레드
        self.assertEqual(calls.index(ups[0]), 2)
        self.assertIn("slack.com/archives", calls[-1][-1])  # 마지막은 진행 줄
        done = self.state()["reports"][rep["id"]]["att_done"]
        self.assertEqual(set(done) - {self.png, self.html}, {self.html + "#preview:" + report_sender.content_hash(self.html)})  # 미리보기 키 = 원본 경로 + 내용 해시
        self.assertEqual(len(done), 3)
        self.assertTrue(all(v.startswith("F") for v in done.values()))  # 업로드 완료 file_id 저장

    def test_decide_card_first_then_files_in_card_thread(self):
        self.report(text="알림 시각 정하기\n배경", kind="decide", files=[self.png], options="1=9시,2=10시", recommend="1", decision_id="D-0101")
        res = report_sender.run(self.conf)
        self.assertEqual(res["sent"], 1)
        calls = self.calls()
        self.assertEqual(calls[0][calls[0].index("--kind") + 1], "decide")
        self.assertFalse(self.records()[0]["upload"])  # 카드 호출은 업로드가 아님(파일은 따로 --attached 로 알리기만)
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
        self.assertEqual(len(self.state()["reports"][rep["id"]]["att_done"]), 3)
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
        self.assertIn(self.html + "#preview:" + report_sender.content_hash(self.html), self.state()["reports"][rep["id"]]["att_done"])

    def test_missing_preview_script_is_skipped(self):
        self.conf.preview = self.p("no-such.sh")
        self.report(files=[self.html])
        self.assertEqual(report_sender.run(self.conf)["sent"], 1)
        self.assertEqual(len(self.uploads()), 1)

    def legacy(self, **kw):
        """접수 규칙을 거치지 않고 outbox 에 직접 쓴 옛 기록(규칙 강화 이전에 쌓인 것)"""
        rec = {"id": "leg1", "at": 1, "hub": "ui-layer", "task": "C0012", "kind": "done", "text": "수정 완료", "files": [], **kw}
        with open(self.conf.outbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def test_sender_stops_legacy_record_with_unattached_path_without_calling_notify(self):
        self.legacy(text="결과는 /Users/me/out/report.html 참고")
        res = report_sender.run(self.conf)
        self.assertEqual((res["sent"], res["stopped"]), (0, 1))
        self.assertEqual([c for c in self.calls() if "warn" not in c], [])  # 중단 경고 1건만 나감(전송·업로드 없음)
        self.assertIn("leg1", self.state()["bad"])

    def test_sender_stops_legacy_decide_without_doc(self):
        self.legacy(kind="decide", text="제목\n배경", options="1=가,2=나", recommend="1", decision_id="D-0301")
        res = report_sender.run(self.conf)
        self.assertEqual(res["stopped"], 1)
        self.assertNotIn("decide", [x for c in self.calls() for x in c if x == "decide" and "--options" in c])
        self.assertIn("--no-doc", self.warn_text())

    def test_sender_logs_warning_for_bare_filename_only_body(self):
        self.report(text="결과는 report.html 참고")  # 폴더 없는 파일명은 거부가 아니라 경고
        report_sender.run(self.conf)
        self.assertIn("본문에 로컬 경로(report.html)만 있고 첨부 없음", self.log_text())

    def warn_text(self):
        return " ".join(c[-1] for c in self.calls() if "warn" in c)

    def test_secret_file_attachment_rejected_in_sender(self):
        key = self.make("server.pem", "x")
        self.legacy(files=[key])
        res = report_sender.run(self.conf)
        self.assertEqual(res["sent"], 1)
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.lines(), ["첨부 실패: server.pem — 민감 파일(키·토큰·인증서·.env·민감 폴더)은 첨부하지 않음"])

    def test_lost_upload_response_does_not_reupload(self):
        """업로드는 Slack 에 갔는데 응답이 유실돼 실패로 보인 경우: 재시도는 보낸 기록(file_id)으로 건너뜀"""
        rep = self.report(files=[self.png])
        self.set_fake("fail.up.lost", 1)
        res = report_sender.run(self.conf)
        self.assertEqual((res["status"], res["failed"]), ("partial", 1))
        self.assertEqual(len(self.uploads()), 1)
        res = report_sender.run(self.conf)
        self.assertEqual((res["sent"], res["failed"]), (1, 0))
        self.assertEqual(len(self.uploads()), 1)  # 두 번째 업로드 호출 없음
        self.assertEqual(self.state()["reports"][rep["id"]]["att_done"][self.png], "Ffake1")

    def test_done_file_ids_survive_retry_and_are_not_reuploaded(self):
        self.report(files=[self.png, self.make("c.png", "png")])
        self.set_fake("fail.n", 0)
        first = report_sender.run(self.conf)
        self.assertEqual(first["sent"], 1)
        self.assertEqual(len(self.uploads()), 2)

    def test_outbox_record_with_more_than_five_files_is_isolated(self):
        files = [self.make(f"f{i}.png", "x") for i in range(6)]
        self.legacy(files=files)
        bad = []
        self.assertEqual(hub_report.read_outbox(self.conf.outbox, bad), [])
        self.assertEqual(len(bad), 1)
        self.assertEqual(report_sender.run(self.conf)["sent"], 0)
        self.assertEqual(self.calls(), [])

    def test_sender_no_warning_when_attached(self):
        self.report(text="결과는 docs/a.png 참고", files=[self.png])
        report_sender.run(self.conf)
        self.assertNotIn("경고:", self.log_text())

    def test_preview_retry_does_not_reupload_original_or_preview(self):
        """미리보기 임시 경로가 회차마다 바뀌어도 키는 원본 경로+내용 해시라 이미 올린 파일을 다시 올리지 않음"""
        self.report(files=[self.html])
        calls = {"n": 0}
        orig = report_sender.run_notify

        def flaky(conf, args, deadline, upload=False):  # 미리보기 업로드 첫 시도만 일시 실패
            if upload and args[-1].endswith("-preview.png") and not calls["n"]:
                calls["n"] += 1
                raise report_sender.NotifyError("네트워크 끊김")
            return orig(conf, args, deadline, upload)

        report_sender.run_notify = flaky
        self.addCleanup(setattr, report_sender, "run_notify", orig)
        self.assertEqual(report_sender.run(self.conf)["failed"], 1)
        self.assertEqual(report_sender.run(self.conf)["sent"], 1)
        names = [os.path.basename(u[-1]) for u in self.uploads()]
        self.assertEqual(names.count("b.html"), 1)
        self.assertEqual(sum(n.endswith("-preview.png") for n in names), 1)

    def test_changed_html_content_gets_new_preview_key(self):
        self.assertNotEqual(report_sender.content_hash(self.html), (self.make("b2.html", "<p>y</p>"), report_sender.content_hash(self.p("b2.html")))[1])

    def test_abort_warning_has_no_path_so_notify_check_passes(self):
        """중단 경고 문구에 원문 경로가 들어가면 알림 스크립트의 경로 검사가 경고도 거부함 → 파일 이름(확장자 없이)만"""
        self.legacy(text="결과는 /Users/me/out/report.html 참고")
        self.assertEqual(report_sender.run(self.conf)["stopped"], 1)
        warns = [c[-1] for c in self.calls() if "warn" in c]
        self.assertEqual(len(warns), 1)
        self.assertNotIn("/Users/me", warns[0])
        self.assertIn("(report)", warns[0])
        self.assertEqual(hub_report.path_problem(warns[0], []), "")  # 알림 스크립트가 쓰는 판정으로도 통과
        self.assertIn(report_sender.safe_text("a /x/y/한글(1).pdf b"), "a 한글(1) b")

    def test_decide_no_doc_flag_reaches_card_only_without_files(self):
        self.report(text="제목\n배경", kind="decide", no_doc=True, options="1=가,2=나", recommend="1", decision_id="D-0501")
        self.report(text="제목2\n배경", kind="decide", files=[self.png], options="1=가,2=나", recommend="1", decision_id="D-0502")
        report_sender.run(self.conf)
        cards = [c for c in self.calls() if "decide" in c]
        self.assertIn("--no-doc", cards[0])
        self.assertNotIn("--no-doc", cards[1])
        self.assertIn("--attached", cards[1])


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

    def test_add_rejects_path_without_matching_attachment(self):
        r = self.cli("결과 /Users/me/report.html 참고")
        self.assertEqual(r.returncode, 2)
        self.assertIn("본문에 로컬 경로(/Users/me/report.html)가 있는데 같은 파일이 --file 첨부로 오지 않음", r.stderr)
        self.assertEqual(hub_report.read_outbox(self.env["LRM_REPORT_OUTBOX"]), [])
        for ext in ("png", "jpg", "gif", "mp4", "pdf"):
            self.assertEqual(self.cli(f"결과 ./out/a.{ext} 참고").returncode, 2, ext)

    def test_add_rejects_when_attachment_is_a_different_file(self):
        pathlib.Path(self.dir.name, "other.html").write_text("x")
        self.assertEqual(self.cli("결과 /Users/me/report.html", "other.html").returncode, 2)

    def test_add_accepts_path_when_same_file_attached(self):
        pathlib.Path(self.dir.name, "report.html").write_text("x")
        r = self.cli(f"결과 {self.dir.name}/report.html 참고", "report.html")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_add_warns_but_accepts_bare_filename(self):
        r = self.cli("결과 report.html 참고")
        self.assertEqual(r.returncode, 0)
        self.assertIn("경고: 본문에 로컬 경로(report.html)만 있고 --file 첨부 없음", r.stderr)

    def test_url_and_code_references_are_not_paths(self):
        for t in ("링크 https://x.com/a/b.html?f=/tmp/c.pdf#d.png 보기", "코드 `docs/a.html` 참조", "```\n/tmp/a.png\n```", "<https://x.co/a.pdf|문서>"):
            self.assertEqual(hub_report.local_paths(t), [], t)
            self.assertEqual(self.cli(t).returncode, 0, t)

    def test_paths_with_parentheses_and_spaces_are_detected_exactly(self):
        self.assertEqual(hub_report.local_paths("/Users/me/결과(최종).pdf 확인"), ["/Users/me/결과(최종).pdf"])
        self.assertEqual(hub_report.local_paths("/Users/me/결과 보고서.pdf 확인"), ["/Users/me/결과 보고서.pdf"])
        self.assertEqual(hub_report.local_paths("(참고: ./out/a.mp4)"), ["./out/a.mp4"])
        self.assertEqual(hub_report.path_problem("결과 보고서.pdf 참고", ["/x/결과 보고서.pdf"]), "")  # 첨부와 같은 이름이면 공백이 있어도 통과

    def test_secret_extensions_added_by_review(self):
        for n in ("a.p8", "a.crt", "a.cer", "a.der", "a.pfx", "a.jks", "a.gpg", "a.asc", "A.PEM"):
            self.assertTrue(hub_report.secret_file_problem(n), n)
        self.assertEqual(hub_report.secret_file_problem("report.html"), "")

    def test_path_right_after_colon_and_spaced_relative_path_are_detected(self):
        self.assertEqual(hub_report.local_paths("참고:/tmp/a.pdf"), ["/tmp/a.pdf"])
        self.assertEqual(hub_report.local_paths("참고: docs/결과 보고서.pdf"), ["docs/결과 보고서.pdf"])
        self.assertEqual(hub_report.local_paths("참고:docs/a.png"), ["docs/a.png"])
        self.assertIn("/tmp/a.pdf", hub_report.path_problem("참고:/tmp/a.pdf", []))
        self.assertIn("docs/결과 보고서.pdf", hub_report.path_problem("참고: docs/결과 보고서.pdf", []))
        self.assertEqual(hub_report.local_paths("링크 https://x.co/a.pdf 와 참고:https://y.co/b.png"), [])  # URL 은 여전히 제외
        self.assertEqual(self.cli("참고:/tmp/a.pdf").returncode, 2)

    def test_decide_requires_doc_or_no_doc(self):
        base = ["--options", "1=가,2=나", "--recommend", "1", "--decision-id", "D-0401"]
        a = ["add", "--hub", "ui-layer", "--task", "C0012", "--kind", "decide", "--text", "제목\n배경", *base]
        r = subprocess.run([sys.executable, "-I", CLI, *a], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--no-doc", r.stderr)
        pathlib.Path(self.dir.name, "d.html").write_text("x")
        ok = subprocess.run([sys.executable, "-I", CLI, *a, "--file", "d.html"], env=self.env, capture_output=True, text=True, cwd=self.dir.name)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        ok2 = subprocess.run([sys.executable, "-I", CLI, *a[:-1], "D-0402", "--no-doc"], env=self.env, capture_output=True, text=True)
        self.assertEqual(ok2.returncode, 0, ok2.stderr)
        self.assertTrue(hub_report.read_outbox(self.env["LRM_REPORT_OUTBOX"])[-1]["no_doc"])

    def test_no_doc_rejected_for_non_decide(self):
        r = subprocess.run([sys.executable, "-I", CLI, "add", "--hub", "ui", "--task", "C1", "--kind", "done", "--text", "x", "--no-doc"],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_secret_files_rejected_at_add(self):
        d = pathlib.Path(self.dir.name)
        (d / ".ssh").mkdir()
        (d / ".ssh" / "notes.txt").write_text("x")
        names = [".env", "prod.env", "a.pem", "b.key", "c.p12", "d.keystore", "id_rsa", "id_ed25519.pub", "token.txt", "credentials.json"]
        for n in names:
            (d / n).write_text("x")
        for n in names + [".ssh/notes.txt"]:
            r = self.cli("완료", n)
            self.assertEqual(r.returncode, 2, n)
            self.assertIn("민감 파일", r.stderr)
        self.assertEqual(hub_report.read_outbox(self.env["LRM_REPORT_OUTBOX"]), [])

    def test_symlink_to_secret_is_rejected(self):
        d = pathlib.Path(self.dir.name)
        (d / "server.pem").write_text("x")
        (d / "innocent.html").symlink_to(d / "server.pem")
        r = self.cli("완료", "innocent.html")
        self.assertEqual(r.returncode, 2)
        self.assertIn("민감 파일", r.stderr)

    def test_report_body_reference_line_stays_at_bottom_without_bullet(self):
        for kind in ("done", "decide"):
            rep = {"hub": "ui-layer", "task": "C0012", "kind": kind, "text": "제목\n배경 한 줄\n참고: main 9c972aa", "files": []}
            body = report_sender.body_text(rep)
            self.assertEqual(body.splitlines()[-1], "참고: main 9c972aa", kind)
            self.assertEqual(body.splitlines()[-2], "", kind)
            self.assertNotIn("- 참고:", body)

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
