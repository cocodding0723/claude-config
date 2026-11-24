"""검수 카드 올리기(board_flow.ask_review) 시험. 보고 도구는 가짜 실행기로 바꾸고, 진짜 hub_report.py 는 임시 허브 폴더로 한 건만 부름.
규칙: 결정 번호 = review_id, 1번 '통과', 앞 카드가 있으면 제목 그대로, 답 기다리는 카드가 있으면 올리지 않음, 허브·작업 공간을 모르면 건너뛰고 명령만 보임."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(REPO, "board", "lib"))
import board_flow as bf  # noqa: E402

EPIC = {"id": "lrm-abc", "title": "[LRM-9] 우편함", "labels": ["feature:mail"], "description": "- 기능 코디네이터 허브: /w/coord-feat-mail-hub"}
DID = bf.review_id("lrm-abc")


class Fake:
    """보고 도구 대역: 부른 명령을 모으고 정해 둔 종료 코드로 끝남"""
    def __init__(self, code=0, err=""):
        self.calls, self.code, self.err = [], code, err

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, self.code, "", self.err)


def write(path, *rows):
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class AskReview(unittest.TestCase):
    def setUp(self):
        self.hub = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.hub, ignore_errors=True))
        open(os.path.join(self.hub, bf.REPORT_NAME), "w").close()  # 파일만 있으면 됨(실행은 가짜)
        self._env = {k: os.environ.pop(k, None) for k in (bf.REPORT_ENV, "LRM_REPORT_OUTBOX")}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) for k, v in self._env.items() if v is not None])

    def args(self, cmd):
        return {cmd[i]: cmd[i + 1] for i in range(len(cmd) - 1) if cmd[i].startswith("--")}

    def test_first_review_posts_decide_card(self):
        fake = Fake()
        r = bf.ask_review(EPIC, self.hub, run=fake)
        self.assertTrue(r["posted"] and not r["failed"] and not r["skipped"])
        cmd, kw = fake.calls[0]
        a = self.args(cmd)
        self.assertEqual((a["--kind"], a["--decision-id"], a["--recommend"], a["--hub"], a["--ws"], a["--card"]),
                         ("decide", DID, "1", "feat-mail", "/w/coord-feat-mail-hub", "lrm-abc"))
        self.assertTrue(a["--options"].startswith(f"1={bf.PASS_WORD} =>"))
        self.assertEqual(a["--text"].split("\n")[0], "우편함 검수")  # 티켓 머리표 제거
        self.assertEqual(kw["env"]["LRM_HUB_DIR"], self.hub)
        self.assertEqual(os.path.basename(cmd[1]), bf.REPORT_NAME)

    def test_result_doc_attached(self):
        doc = os.path.join(self.hub, "result.html")
        open(doc, "w").close()
        found = bf.result_doc(["작업 중", f"기능 완료 결과: {doc}", "기능 완료 /없는/경로.html"])
        self.assertEqual(found, doc)  # 최근 코멘트의 없는 경로는 건너뛰고 실제 파일을 고름
        fake = Fake()
        bf.ask_review(EPIC, self.hub, run=fake, doc=found)
        cmd = fake.calls[0][0]
        self.assertEqual(self.args(cmd)["--file"], doc)
        self.assertNotIn("--no-doc", cmd)
        self.assertIn("첨부한 완료 보고서", self.args(cmd)["--text"])

    def test_no_result_doc_keeps_no_doc(self):
        self.assertEqual(bf.result_doc(["진행 중 /tmp/x.html"]), "")  # '기능 완료' 코멘트만 봄
        fake = Fake()
        bf.ask_review(EPIC, self.hub, run=fake)
        self.assertIn("--no-doc", fake.calls[0][0])
        self.assertIn("찾지 못했다", self.args(fake.calls[0][0])["--text"])

    def test_small_done_report(self):
        doc = os.path.join(self.hub, "result.html")
        open(doc, "w").close()
        fake = Fake()
        self.assertEqual(bf.send_done(EPIC, self.hub, doc, run=fake), "")
        a = self.args(fake.calls[0][0])
        self.assertEqual((a["--kind"], a["--file"]), ("done", doc))
        self.assertIn("완료 보고서 없음", bf.send_done(EPIC, self.hub, "", run=Fake()))

    def test_title_fits_limit(self):
        long = {**EPIC, "title": "가" * 60}
        t = bf.review_title(self.hub, DID, long)
        self.assertLessEqual(len(t), bf.TITLE_MAX)
        self.assertTrue(t.endswith(bf.TITLE_TAIL))

    def test_title_reused_from_outbox_then_sent(self):
        write(os.path.join(self.hub, bf.OUTBOX_NAME), {"decision_id": DID, "kind": "decide", "text": "옛 제목\n배경: x", "at": 1})
        self.assertEqual(bf.review_title(self.hub, DID, EPIC), "옛 제목")
        os.remove(os.path.join(self.hub, bf.OUTBOX_NAME))
        write(os.path.join(self.hub, bf.SENT_NAME), {"decision_id": DID, "form_kind": "decide", "title": ":비서-선택해주세요: 보낸 제목", "ts": "1.1", "at": "5"})
        self.assertEqual(bf.review_title(self.hub, DID, EPIC), "보낸 제목")

    def test_pending_card_not_reposted(self):
        write(os.path.join(self.hub, bf.SENT_NAME), {"decision_id": DID, "form_kind": "decide", "title": "t", "ts": "1.1", "at": "5", "options": {"1": "통과"}})
        fake = Fake()
        r = bf.ask_review(EPIC, self.hub, run=fake)
        self.assertTrue(r["skipped"] and not r["posted"] and not r["failed"])
        self.assertEqual(fake.calls, [])

    def test_repost_after_fix_answer(self):
        write(os.path.join(self.hub, bf.SENT_NAME), {"decision_id": DID, "form_kind": "decide", "title": "t", "ts": "1.1", "at": "5", "options": {"1": "통과"}})
        write(os.path.join(self.hub, bf.INBOX_NAME), {"decision_id": DID, "parent_ts": "1.1", "action": "pick", "pick": 2, "source": "slack"})
        fake = Fake()
        self.assertTrue(bf.ask_review(EPIC, self.hub, run=fake)["posted"])

    def test_queued_but_unsent_card_counts_as_pending(self):
        write(os.path.join(self.hub, bf.OUTBOX_NAME), {"decision_id": DID, "kind": "decide", "text": "t", "at": 10})
        self.assertTrue(bf.ask_review(EPIC, self.hub, run=Fake())["skipped"])

    def test_old_outbox_before_answered_card_is_not_pending(self):
        write(os.path.join(self.hub, bf.OUTBOX_NAME), {"decision_id": DID, "kind": "decide", "text": "t", "at": 1})
        write(os.path.join(self.hub, bf.SENT_NAME), {"decision_id": DID, "form_kind": "decide", "title": "t", "ts": "1.1", "at": "5", "options": {"1": "통과"}})
        write(os.path.join(self.hub, bf.INBOX_NAME), {"decision_id": DID, "parent_ts": "1.1", "action": "pick", "pick": 2, "source": "slack"})
        self.assertFalse(bf.review_pending(self.hub, DID))

    def test_tool_failure_returns_failed_with_manual_command(self):
        r = bf.ask_review(EPIC, self.hub, run=Fake(2, "오류: 양식"))
        self.assertFalse(r["posted"])
        self.assertIn("종료 2", r["failed"])
        self.assertIn(DID, r["manual"])

    def test_missing_hub_or_workspace_skips_with_command(self):
        for epic, word in (({**EPIC, "labels": []}, "허브 이름"), ({**EPIC, "description": ""}, "작업 공간")):
            fake = Fake()
            r = bf.ask_review(epic, self.hub, run=fake)
            self.assertIn(word, r["failed"])
            self.assertEqual(fake.calls, [])
            self.assertIn("hub_report.py", r["manual"])

    def test_missing_report_tool(self):
        os.remove(os.path.join(self.hub, bf.REPORT_NAME))
        self.assertIn(bf.REPORT_NAME, bf.ask_review(EPIC, self.hub, run=Fake())["failed"])

    def test_report_path_override_env(self):
        os.environ[bf.REPORT_ENV] = os.path.join(self.hub, "other.py")
        open(os.environ[bf.REPORT_ENV], "w").close()
        fake = Fake()
        bf.ask_review(EPIC, self.hub, run=fake)
        self.assertEqual(fake.calls[0][0][1], os.environ[bf.REPORT_ENV])

    def test_dry_run_posts_nothing(self):
        fake = Fake()
        r = bf.ask_review(EPIC, self.hub, dry_run=True, run=fake)
        self.assertTrue(r["skipped"] and not r["posted"])
        self.assertEqual(fake.calls, [])

    def test_note_line(self):
        fake = Fake()
        bf.ask_review(EPIC, self.hub, note="툴팁 위치를 고쳤다", run=fake)
        self.assertIn(f"{bf.REVIEW_NOTE_HEAD}: 툴팁 위치를 고쳤다", self.args(fake.calls[0][0])["--text"].split("\n"))

    def test_real_report_tool_accepts_card(self):
        """진짜 hub_report.py 를 임시 허브 폴더로 불러 양식 검사(배경·용어·선택지 결과·제목 길이)를 통과하는지"""
        os.environ[bf.REPORT_ENV] = os.path.abspath(os.path.join(REPO, "hubs", "scripts", "hub_report.py"))
        self.addCleanup(os.environ.pop, bf.REPORT_ENV, None)
        r = bf.ask_review({**EPIC, "title": "[LRM-9] " + "긴 이름 " * 12}, self.hub, note="한 줄 메모")
        self.assertTrue(r["posted"], r["failed"])
        rows = bf.read_jsonl(os.path.join(self.hub, bf.OUTBOX_NAME))
        self.assertEqual((rows[0]["decision_id"], rows[0]["kind"]), (DID, "decide"))
        self.assertTrue(bf.ask_review(EPIC, self.hub)["skipped"])  # 대기열에 있는 카드는 다시 올리지 않음
        self.assertEqual(len(bf.read_jsonl(os.path.join(self.hub, bf.OUTBOX_NAME))), 1)


if __name__ == "__main__":
    unittest.main()
