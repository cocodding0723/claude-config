"""첨부 접수 복사 시험: 원본을 못 읽는 백그라운드(TCC 대역 = chmod 000)에서도 접수 때 만든 복사본으로 전송됨. 가짜 알림 스크립트만 씀."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import hub_report  # noqa: E402
import report_sender  # noqa: E402

FAKE = os.path.join(HERE, "fake_notify.py")
PREVIEW = os.path.join(HERE, "fake_preview.sh")
CLI = os.path.join(SCRIPTS, "hub_report.py")
NAME = "보고 서 결과.md"  # 한글·공백


@unittest.skipIf(os.geteuid() == 0, "root 는 chmod 000 파일도 읽음")
class StageTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.p = lambda n: os.path.join(self.dir.name, n)
        self.conf = report_sender.Conf(outbox=self.p("box.jsonl"), state=self.p("state.json"), notify=FAKE, preview=PREVIEW,
                                       ledger=self.p("ledger.tsv"), sync_state=self.p("sync.json"), sent=self.p("sent.jsonl"),
                                       log=self.p("log.txt"), backoff_s=0)
        for k in ("LRM_REPORT_FILES", "LRM_REPORT_OUTBOX"):
            self.addCleanup(os.environ.pop, k, None)
            os.environ.pop(k, None)
        os.environ["FAKE_DIR"] = self.dir.name
        self.addCleanup(os.environ.pop, "FAKE_DIR", None)
        pathlib.Path(self.p("sync.json")).write_text(json.dumps({"ts": "1699999999.000001", "channel": "D1"}))
        self.src = self.p(NAME)
        pathlib.Path(self.src).write_text("본문")

    def lock(self, path):  # 백그라운드가 못 읽는 원본 흉내
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o600)

    def add(self, files, stage=True, now=1.0):
        rep = hub_report.make_report("ui-layer", "C0012", "done", "수정 완료", list(files))
        return hub_report.add(self.conf.outbox, rep, now, False, stage=stage)[0]

    def uploads(self):
        with open(self.p("calls.jsonl"), encoding="utf-8") as f:
            return [r["args"] for r in map(json.loads, f) if r["upload"]]

    def fail_lines(self):
        with open(self.p("calls.jsonl"), encoding="utf-8") as f:
            return [r["args"][-1] for r in map(json.loads, f) if r["args"][-1].startswith("첨부 실패")]

    def test_staged_copy_is_uploaded_when_original_unreadable_then_removed(self):
        rep = self.add([self.src])
        copy = rep["staged"][self.src]
        self.assertEqual(os.path.basename(copy), NAME)  # 원본 이름 보존
        self.assertTrue(copy.startswith(hub_report.stage_dir(self.conf.outbox) + os.sep + rep["id"]))
        self.assertEqual(rep["files"], [self.src])  # 원본 경로도 기록에 남음
        self.lock(self.src)
        res = report_sender.run(self.conf)
        self.assertEqual((res["status"], res["sent"], res["failed"]), ("done", 1, 0))
        self.assertEqual([u[-1] for u in self.uploads()], [copy])  # 복사본으로 업로드
        self.assertEqual(self.fail_lines(), [])
        self.assertFalse(os.path.exists(os.path.join(hub_report.stage_dir(self.conf.outbox), rep["id"])))  # 완료 뒤 정리

    def test_old_record_without_copy_and_unreadable_original_fails_once(self):
        rep = self.add([self.src], stage=False)
        self.assertNotIn("staged", rep)
        self.lock(self.src)
        res = report_sender.run(self.conf)
        self.assertEqual((res["status"], res["sent"], res["failed"]), ("done", 1, 0))  # 영구 실패라 재시도 없이 본문은 올라감
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.fail_lines(), [f"첨부 실패: {NAME} — {report_sender.UNREADABLE}"])
        report_sender.run(self.conf)
        self.assertEqual(len(self.fail_lines()), 1)  # 경고는 1회

    def test_missing_copy_falls_back_to_original(self):
        rep = self.add([self.src])
        os.unlink(rep["staged"][self.src])
        report_sender.run(self.conf)
        self.assertEqual([u[-1] for u in self.uploads()], [self.src])

    def test_html_preview_built_from_copy(self):
        html = self.p("페이지 하나.html")
        pathlib.Path(html).write_text("<p>x</p>")
        rep = self.add([html])
        self.lock(html)
        report_sender.run(self.conf)
        names = [os.path.basename(u[-1]) for u in self.uploads()]
        self.assertEqual(names[0], "페이지 하나.html")
        self.assertTrue(names[1].endswith("-preview.png"))  # 원본을 못 읽어도 미리보기까지 감
        self.assertNotIn(rep["id"], report_sender.load_state(self.conf.state)["bad"])

    def test_stale_copy_dirs_are_collected(self):
        root = hub_report.stage_dir(self.conf.outbox)
        old, fresh = os.path.join(root, "aaaaaaaaaaaa"), os.path.join(root, "bbbbbbbbbbbb")
        for d in (old, fresh):
            os.makedirs(d)
        past = time.time() - report_sender.STAGE_KEEP_S - 60
        os.utime(old, (past, past))
        report_sender.run(self.conf)
        self.assertEqual((os.path.exists(old), os.path.exists(fresh)), (False, True))

    def test_cli_add_copies_once_skips_dry_run_and_secret_files(self):
        env = {**os.environ, "LRM_REPORT_OUTBOX": self.conf.outbox}
        secret = self.p(".env")
        pathlib.Path(secret).write_text("A=1")
        add = lambda *extra: subprocess.run([sys.executable, CLI, "add", "--hub", "ui-layer", "--task", "C0012", "--kind", "done", "--text", "완료", "--file", self.src, *extra],  # noqa: E731
                                            capture_output=True, text=True, env=env)
        root = hub_report.stage_dir(self.conf.outbox)
        self.assertEqual(add("--dry-run").returncode, 0)
        self.assertFalse(os.path.exists(root))  # dry-run 은 복사 안 함
        self.assertEqual(add().returncode, 0)
        rows = hub_report.read_outbox(self.conf.outbox)
        copy = rows[0]["staged"][self.src]
        self.assertTrue(os.path.isfile(copy))
        os.unlink(copy)
        self.assertIn("중복 무시", add().stdout)
        self.assertFalse(os.path.exists(copy))  # 중복은 다시 복사하지 않음
        self.assertEqual(hub_report.stage_files(self.conf.outbox, "cccccccccccc", [secret]), {})  # 민감 파일은 복사 안 함

    def test_record_with_bad_staged_field_is_rejected(self):
        rep = self.add([self.src])
        self.assertTrue(hub_report.valid_record(rep))
        self.assertFalse(hub_report.valid_record({**rep, "staged": {"/elsewhere": "/x"}}))
        self.assertFalse(hub_report.valid_record({**rep, "staged": ["x"]}))


if __name__ == "__main__":
    unittest.main()
