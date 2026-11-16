"""night_draft.py 시험: 카드 본문(양식 A·decide 검사 통과), dry-run 무기록, 전송·같은 날 1회, 승인 수확(답 1번만), 제외 규칙. 임시 폴더만 씀(Slack·라이브 파일 없음)."""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "hubs", "scripts"))
import answer_store  # noqa: E402
import night_draft as nd  # noqa: E402
import task_ledger as tl  # noqa: E402

DAY1 = datetime.datetime(2026, 11, 17, 15, 0).timestamp()
DAY2 = DAY1 + 86400
HUBS = "# slug\t이름\tcwd\ndocs\t문서\t-\nui\tUI\t-\n"


def quiet(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*a)
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.d = self.tmp.name
        self.tasks, self.outbox = os.path.join(d, "tasks.jsonl"), os.path.join(d, "hub-reports.jsonl")
        with open(os.path.join(d, "hubs.tsv"), "w", encoding="utf-8") as f:
            f.write(HUBS)
        self.env = mock.patch.dict(os.environ, {"LRM_HUB_DIR": d, "LRM_TASKS": self.tasks, "LRM_REPORT_OUTBOX": self.outbox,
                                                "LRM_HUBS_TABLE": os.path.join(d, "hubs.tsv"), "LRM_NIGHT_NOW": str(DAY1)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def add(self, text, *extra):
        rc, out, err = quiet(tl.main, ["add", "--text", text, "--json", *extra])
        self.assertEqual(rc, 0, err)
        return json.loads(out)["id"]

    def full(self, text="야간: 문서 이름 정리", *extra):
        return self.add(text, "--hub", "docs", "--state", "배정", "--gate", "auto", "--goal", "문서 이름 규칙 반영", "--done-when", "draft PR 게시",
                        "--question", "파일 이름?|A|쉬움", *extra)

    def run_draft(self, *args, now=None):
        env = {"LRM_NIGHT_NOW": str(now)} if now else {}
        with mock.patch.dict(os.environ, env):
            return quiet(nd.main, list(args))

    def outbox_rows(self):
        try:
            with open(self.outbox, encoding="utf-8") as f:
                return [json.loads(x) for x in f if x.strip()]
        except FileNotFoundError:
            return []

    def ledger_text(self):
        with open(self.tasks, encoding="utf-8") as f:
            return f.read()

    def row(self, rid):
        return next(r for r in tl.read_rows(self.tasks) if r["id"] == rid)

    def answer(self, did, pick, ts="1793000000.000100"):
        """카드가 나간 것으로 보내진 카드 기록을 쓰고, 버튼 답을 첫 답으로 기록"""
        with open(os.path.join(self.d, "slack-sent.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"form_kind": "decide", "ts": ts, "options": "x", "decision_id": did}) + "\n")
        answer_store.AnswerStore(self.d).claim(answer_store.Answer(did, ts, "D1", "야간 진행 승인", "라벨", answer_store.SLACK, {"action": "pick", "pick": pick}))


class CardTest(Base):
    def test_card_passes_decide_form_and_limits(self):
        rid = self.full()
        card = nd.build_card(self.row(rid), DAY1)
        lines = card["text"].split("\n")
        self.assertLessEqual(len(lines[0]), nd.TITLE_MAX)
        self.assertTrue(lines[1].startswith("배경:"))
        self.assertTrue(any(ln.startswith("용어:") for ln in lines))
        self.assertTrue(any(ln.startswith("질문: 파일 이름?") for ln in lines))
        self.assertEqual(card["did"], "D-NQ00011117")
        ok, note = nd.hub_report_add(card, "", True)  # hub_report 의 양식 A·decide 검사
        self.assertTrue(ok, note)

    def test_recommend_hold_when_draft_incomplete_or_hard_question_open(self):
        complete = self.full()
        no_goal = self.add("야간: 목표 없음", "--hub", "docs", "--done-when", "x")
        hard = self.add("야간: 어려운 질문", "--hub", "docs", "--goal", "g", "--done-when", "x", "--question", "병합할까?|아니오|어려움")
        self.assertEqual(nd.recommend_for(self.row(complete)), nd.APPROVE)
        self.assertEqual(nd.recommend_for(self.row(no_goal)), nd.HOLD)
        self.assertEqual(nd.recommend_for(self.row(hard)), nd.HOLD)
        for rid in (no_goal, hard):
            ok, note = nd.hub_report_add(nd.build_card(self.row(rid), DAY1), "", True)
            self.assertTrue(ok, note)

    def test_unassigned_hub_falls_back_and_many_questions_fit(self):
        rid = self.add("야간: 담당 없음", "--goal", "g", "--done-when", "d", *[x for i in range(5) for x in ("--question", f"질문{i}|A|쉬움")])
        card = nd.build_card(self.row(rid), DAY1)
        self.assertEqual(card["hub"], nd.FALLBACK_HUB)
        self.assertIn("질문 외 2건", card["text"])
        ok, note = nd.hub_report_add(card, "", True)
        self.assertTrue(ok, note)

    def test_bundle_escapes_html(self):
        rid = self.add("야간: <script>x</script>", "--goal", "g<b>")
        body = nd.render_bundle("2026-11-17", [self.row(rid)])
        self.assertNotIn("<script>x", body)
        self.assertIn("&lt;script&gt;", body)


class RunTest(Base):
    def test_dry_run_writes_nothing(self):
        self.full()
        before = self.ledger_text()
        rc, out, _ = self.run_draft()
        self.assertEqual(rc, 0)
        self.assertIn("[dry-run] 승인 0건 · 카드 1장(실패 0)", out)
        self.assertEqual(self.outbox_rows(), [])
        self.assertFalse(os.path.exists(os.path.join(self.d, nd.LOG_NAME)))
        self.assertFalse(os.path.exists(os.path.join(self.d, nd.DOC_DIR)))
        self.assertEqual(self.ledger_text(), before)

    def test_send_records_cards_first_gets_bundle_and_same_day_once(self):
        a, b = self.full(), self.full("야간: 둘째")
        self.assertEqual(self.run_draft("--send")[0], 0)
        rows = self.outbox_rows()
        self.assertEqual([r["task"] for r in rows], [a, b])
        self.assertTrue(all(r["kind"] == "decide" and r["decision_id"].startswith("D-NQ") for r in rows))
        self.assertEqual(len(rows[0]["files"]), 1)
        self.assertTrue(rows[0]["files"][0].endswith("night-drafts/2026-11-17.html") and os.path.isfile(rows[0]["files"][0]))
        self.assertEqual(rows[1]["files"], [])
        self.assertTrue(rows[1].get("no_doc"))
        rc, out, _ = self.run_draft("--send")  # 같은 날 재실행
        self.assertIn("카드 0장", out)
        self.assertEqual(len(self.outbox_rows()), 2)

    def test_no_doc_flag(self):
        self.full()
        self.assertEqual(self.run_draft("--send", "--no-doc")[0], 0)
        self.assertTrue(self.outbox_rows()[0].get("no_doc"))
        self.assertFalse(os.path.exists(os.path.join(self.d, nd.DOC_DIR)))

    def test_excluded_rows(self):
        self.add("낮 지시", "--hub", "docs")
        done = self.full("야간: 끝난 것")
        self.assertEqual(quiet(tl.main, ["set", done, "--state", "완료", "--evidence", "PR #1"])[0], 0)
        approved = self.full("야간: 승인된 것")
        self.assertEqual(quiet(tl.main, ["approve-night", approved, "--card", "D-X"])[0], 0)
        self.assertIn("카드 0장", self.run_draft("--send")[1])
        self.assertEqual(self.outbox_rows(), [])

    def test_invalid_card_reports_failure_without_log(self):
        rid = self.add("야간: 비밀", "--goal", "token=abcd1234secretvalue9999 정리", "--hub", "docs")
        rc, out, _ = self.run_draft("--send")
        self.assertEqual(rc, 1)
        self.assertIn("실패", out)
        self.assertEqual(nd.read_log(), [])
        self.assertEqual(self.outbox_rows(), [])
        self.assertFalse(tl.night_approved(self.row(rid)))


class HarvestTest(Base):
    def test_approve_pick_records_approval_and_stops_drafting(self):
        rid = self.full()
        self.run_draft("--send")
        did = self.outbox_rows()[0]["decision_id"]
        self.answer(did, 1)
        rc, out, _ = self.run_draft("--send", now=DAY2)
        self.assertEqual(rc, 0)
        self.assertIn("승인 1건 · 카드 0장", out)
        night = self.row(rid)["night"]
        self.assertEqual(night["approved_by_card"], did)
        self.assertTrue(night["approved_at"])
        self.assertEqual(len(self.outbox_rows()), 1)  # 다음 날 다시 묻지 않음

    def test_hold_or_no_answer_redrafts_next_day_with_new_number(self):
        held, unseen = self.full(), self.full("야간: 안 본 것")
        self.run_draft("--send")
        dids = {r["task"]: r["decision_id"] for r in self.outbox_rows()}
        self.answer(dids[held], 2)
        rc, out, _ = self.run_draft("--send", now=DAY2)
        self.assertIn("승인 0건 · 카드 2장", out)
        again = {r["task"]: r["decision_id"] for r in self.outbox_rows()[2:]}
        self.assertEqual(set(again), {held, unseen})
        self.assertTrue(all(again[t] != dids[t] for t in again))
        self.assertFalse(tl.night_approved(self.row(held)) or tl.night_approved(self.row(unseen)))

    def test_free_text_reply_is_not_approval(self):
        rid = self.full()
        self.run_draft("--send")
        did = self.outbox_rows()[0]["decision_id"]
        with open(os.path.join(self.d, "slack-sent.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"form_kind": "decide", "ts": "1.1", "options": "x", "decision_id": did}) + "\n")
        answer_store.AnswerStore(self.d).claim(answer_store.Answer(did, "1.1", "D1", "t", "그냥 해", answer_store.SLACK, {"action": "custom", "text": "그냥 해"}))
        self.run_draft("--send", now=DAY2)
        self.assertFalse(tl.night_approved(self.row(rid)))

    def test_dry_run_reports_approval_without_writing(self):
        rid = self.full()
        self.run_draft("--send")
        self.answer(self.outbox_rows()[0]["decision_id"], 1)
        before = self.ledger_text()
        rc, out, _ = self.run_draft(now=DAY2)
        self.assertIn("[dry-run] 승인 1건", out)
        self.assertEqual(self.ledger_text(), before)
        self.assertFalse(tl.night_approved(self.row(rid)))


if __name__ == "__main__":
    unittest.main()
