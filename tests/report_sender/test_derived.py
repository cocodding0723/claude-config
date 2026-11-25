"""PDF·미리보기 작업(report_derived.py) 시험. 가짜 알림·변환 스크립트만 씀."""
import fcntl
import json
import os
import pathlib
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_attach as base  # noqa: E402
from test_attach import PDF, hub_report, report_sender  # noqa: E402
import report_derived  # noqa: E402


class DerivedTest(unittest.TestCase):
    setUp, make, report, records, calls, uploads, lines, state, log_text = (
        getattr(base.AttachTest, n) for n in ("setUp", "make", "report", "records", "calls", "uploads", "lines", "state", "log_text"))

    def names(self):
        return [os.path.basename(u[-1]) for u in self.uploads()]

    def send(self, **kw):
        rep = self.report(files=[self.html], **kw)
        self.assertEqual(report_sender.run(self.conf)["sent"], 1)
        return rep

    def mine(self):
        return report_derived.load(report_derived.state_path(self.conf))

    def due(self, rid):
        d = self.mine()
        d[rid]["next_at"] = 0
        report_sender.save_state(report_derived.state_path(self.conf), d)

    def patch(self, name, value, mod=report_derived):
        self.addCleanup(setattr, mod, name, getattr(mod, name))
        setattr(mod, name, value)

    def script(self, name, body):
        path = self.make(name, body)
        os.chmod(path, 0o755)
        return path

    def test_uploads_pdf_then_preview_in_same_thread_once(self):
        rep = self.send()
        before = pathlib.Path(self.conf.state).read_bytes()
        self.assertEqual(report_derived.run(self.conf), {"status": "done", "left": 0})
        report_derived.run(self.conf)
        names = self.names()
        self.assertEqual((names[:2], len(names), names[2].endswith("-b-preview.png")), (["b.html", "b.pdf"], 3, True))
        self.assertEqual({u[u.index("--thread") + 1] for u in self.uploads()}, {base.TOPIC_TS})
        self.assertTrue(self.mine()[rep["id"]]["finished"])
        self.assertEqual(pathlib.Path(self.conf.state).read_bytes(), before)  # 전송기 상태는 읽기만
        self.assertEqual([f for _, _, fs in os.walk(self.p("report-previews")) for f in fs], [])

    def test_runs_while_sender_lock_is_held(self):
        """전송기가 계속 바빠 잠금을 쥐고 있어도 나감(11-25 10건 대기의 재현)"""
        self.send()
        with open(self.conf.state + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            report_derived.run(self.conf)
        self.assertEqual(len(self.uploads()), 3)

    def test_drains_over_rounds_when_budget_is_short(self):
        """한 회차에 다 못 해도 회차마다 나아가 결국 전부 나가고, 예산 부족은 실패로 세지 않음"""
        slow = self.script("slow.sh", '#!/bin/bash\nsleep 0.3\nprintf x > "$2"\n')
        self.conf.pdf = self.conf.preview = slow
        self.patch("MIN_LEFT_S", 0.1)
        for n in range(4):
            self.report(text=f"보고 {n}번 완료", files=[self.make(f"d{n}.html", f"<p>{n}</p>")])
        report_sender.run(self.conf)
        for _ in range(30):
            if report_derived.run(self.conf, 0.8)["left"] == 0:
                break
        self.assertEqual(len(self.uploads()), 12)
        self.assertEqual({e["tries"] for e in self.mine().values()}, {0})

    def test_no_budget_starts_nothing(self):
        rep = self.send()
        self.assertEqual(report_derived.run(self.conf, report_derived.MIN_LEFT_S - 1)["left"], 1)
        self.assertEqual((self.names(), self.mine().get(rep["id"], {}).get("tries", 0)), (["b.html"], 0))

    def test_failing_report_does_not_block_others(self):
        bad = self.make("bad.html", "<p>bad</p>")
        self.conf.pdf = self.script("pick.sh", '#!/bin/bash\ncase "$1" in *bad.html) exit 1;; esac\nprintf x > "$2"\n')
        self.report(text="나쁜 문서", files=[bad])
        self.report(text="좋은 문서", files=[self.html])
        report_sender.run(self.conf)
        report_derived.run(self.conf)
        names = self.names()
        self.assertIn("b.pdf", names)
        self.assertNotIn("bad.pdf", names)
        self.assertTrue(any(n.endswith("-bad-preview.png") for n in names))  # PDF 만 빠지고 미리보기는 감

    def test_stuck_chrome_is_killed_and_retried_later(self):
        self.patch("MAKE_TIMEOUT_S", 1)
        self.conf.pdf = self.script("stuck.sh", '#!/bin/bash\nsleep 60 &\necho $! > "%s"\nwait\n' % self.p("child.pid"))
        rep = self.send()
        report_derived.run(self.conf)
        pid = int(pathlib.Path(self.p("child.pid")).read_text())
        for _ in range(50):  # 강제 종료된 손자가 정리될 때까지
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("멈춘 자식 프로세스가 남음")
        self.assertIn("PDF 보류 1회째", self.log_text())
        self.conf.pdf = PDF
        report_derived.run(self.conf)
        self.assertNotIn("b.pdf", self.names())  # 대기 시간 전에는 다시 안 함
        self.due(rep["id"])
        report_derived.run(self.conf)
        self.assertEqual((self.names().count("b.pdf"), len(self.uploads())), (1, 3))

    def test_gives_up_with_one_line_in_thread(self):
        self.patch("DERIVED_TRIES", 2)
        os.environ["FAKE_PDF_FAIL"] = "1"
        rep = self.send()
        for _ in range(3):
            report_derived.run(self.conf)
            self.due(rep["id"])
        self.assertEqual(self.lines(), ["첨부 실패: b.html PDF — 변환 실패(종료 코드 1)"])
        self.assertTrue(self.mine()[rep["id"]]["finished"])

    def test_missing_script_is_skipped(self):
        self.conf.pdf = self.p("no-such.sh")
        self.send()
        report_derived.run(self.conf)
        self.assertEqual(len(self.uploads()), 2)
        self.assertEqual(self.lines(), [])

    def test_transient_upload_failure_retries_without_duplicates(self):
        rep, orig, n = self.send(), report_sender.run_notify, {"n": 0}

        def flaky(conf, args, deadline, upload=False):
            if upload and args[-1].endswith(".pdf") and not n["n"]:
                n["n"] += 1
                raise report_sender.NotifyError("네트워크 끊김")
            return orig(conf, args, deadline, upload)

        self.patch("run_notify", flaky, report_sender)
        report_derived.run(self.conf)
        self.due(rep["id"])
        report_derived.run(self.conf)
        names = self.names()
        self.assertEqual((names.count("b.html"), names.count("b.pdf"), len(names)), (1, 1, 3))

    def test_staged_copy_kept_until_done(self):
        """원본이 사라져도 접수 때 복사본으로 만들고, 끝난 뒤 복사본을 지움"""
        rep = hub_report.add(self.conf.outbox, hub_report.make_report("ui-layer", "C0012", "done", "수정 완료", [self.html]), 1.0, False, stage=True)[0]
        report_sender.run(self.conf)
        copy = rep["staged"][self.html]
        self.assertTrue(os.path.isfile(copy))
        os.unlink(self.html)
        report_derived.run(self.conf)
        self.assertEqual(len(self.uploads()), 3)
        self.assertFalse(os.path.exists(copy))

    def test_continues_old_sender_records(self):
        """분리 전 전송기가 PDF 까지 올린 보고는 미리보기만 올림"""
        rep = self.send()
        st = self.state()
        st["reports"][rep["id"]]["att_done"][self.html + "#pdf:" + report_sender.content_hash(self.html)] = "Fold"
        report_sender.save_state(self.conf.state, st)
        report_derived.run(self.conf)
        self.assertEqual([n.endswith("-preview.png") for n in self.names()], [False, True])

    def skipped(self):
        """분리 전 '생략' 으로 굳은 보고"""
        rep = self.send()
        st, h = self.state(), report_sender.content_hash(self.html)
        rs = st["reports"][rep["id"]]
        rs.pop("derived")
        rs["att_done"].update({f"{self.html}#pdf:{h}": "", f"{self.html}#preview:{h}": ""})
        report_sender.save_state(self.conf.state, st)
        pathlib.Path(self.conf.sent).write_text(json.dumps({"kind": "file", "file_id": rs["att_done"][self.html], "thread_ts": base.TOPIC_TS}) + "\n")
        return rep

    def test_retry_resends_skipped_report(self):
        rep = self.skipped()
        report_derived.run(self.conf)
        self.assertEqual(self.names(), ["b.html"])
        self.assertEqual(report_derived.retry(self.conf, rep["id"]), "")
        report_derived.run(self.conf)
        self.assertEqual(len(self.uploads()), 3)

    def test_retry_refuses_changed_html(self):
        rep = self.skipped()
        pathlib.Path(self.html).write_text("<p>바뀜</p>")
        self.assertIn("HTML 이 바뀜", report_derived.retry(self.conf, rep["id"]))

    def test_corrupt_state_stops_without_upload(self):
        self.send()
        pathlib.Path(report_derived.state_path(self.conf)).write_text("{")
        self.assertEqual(report_derived.run(self.conf)["status"], "halted")
        self.assertEqual(self.names(), ["b.html"])


if __name__ == "__main__":
    unittest.main()
