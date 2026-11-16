"""task_ledger 야간 큐 칸 시험: 새 칸 검증·거부, 승인 기록 멱등, 목록 필터, 머리말 접수, 옛 행 호환. 임시 폴더만 씀."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_task_ledger import Base, run, tl  # noqa: E402


class NightFieldsTest(Base):
    def test_new_fields_roundtrip(self):
        rid = self.add("야간 후보", "--hub", "vfx", "--state", "배정", "--gate", "auto", "--goal", "문서 이름 규칙 반영",
                       "--after", "pr:#12,decision:D-X1", "--question", "파일 이름?|A|쉬움", "--question", "범위?||", "--uses", "unity", "--night")
        row = self.rows()[0]
        self.assertEqual(row["id"], rid)
        self.assertEqual((row["gate"], row["goal"], row["uses"]), ("auto", "문서 이름 규칙 반영", ["unity"]))
        self.assertEqual(row["after"], [{"kind": "pr", "ref": "#12"}, {"kind": "decision", "ref": "D-X1"}])
        self.assertEqual(row["questions"][0], {"q": "파일 이름?", "rec": "A", "revert": "쉬움", "answer": None})
        self.assertEqual(row["questions"][1]["revert"], "보통")  # 되돌리기 칸이 비면 보통(안전한 쪽)
        self.assertEqual(row["night"], {"tries": 0, "sent_at": "", "approved_at": "", "approved_by_card": ""})

    def test_bad_values_rejected_and_file_untouched(self):
        rid = self.add("일")
        before = self.raw()
        for args in (("--gate", "maybe"), ("--after", "pr:abc"), ("--after", "foo:T0001"), ("--uses", "gpu"),
                     ("--goal", "가" * 201), ("--question", "|A|쉬움"), ("--question", "질문|A|아무거나")):
            rc, _, err = run("set", rid, *args)
            self.assertEqual(rc, 2, args)
            self.assertIn(rid, err)
        self.assertEqual(self.raw(), before)

    def test_old_rows_without_new_fields_still_valid(self):
        rid = self.add("옛 행")
        self.assertEqual(run("set", rid, "--state", "진행")[0], 0)
        row = self.rows()[0]
        self.assertFalse({"gate", "goal", "after", "questions", "uses", "night"} & set(row))

    def test_night_header_marks_inbox_row(self):
        with open(self.inbox, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "note", "ts": "1793858800.000001", "text": "야간: 문서 이름 정리"}, ensure_ascii=False) + "\n"
                    + json.dumps({"type": "note", "ts": "1793858801.000001", "text": "낮 지시"}, ensure_ascii=False) + "\n")
        rc, _, err = run("import-inbox", "--inbox", self.inbox, "--routes", tl.os.path.join(tl.os.path.dirname(__file__), "..", "..", "hubs", "intake-routes.tsv"))
        self.assertEqual(rc, 0, err)
        night, day = self.rows()
        self.assertTrue(tl.is_night(night) and not tl.is_night(day))

    def test_routes_table_has_night_header(self):
        """intake-routes.tsv 의 머리말 행과 task_ledger.NIGHT_HEADER 가 같은 이름"""
        with open(os.path.join(os.path.dirname(__file__), "..", "..", "hubs", "intake-routes.tsv"), encoding="utf-8") as f:
            keys = [ln.split("\t")[0] for ln in f if ln.strip() and not ln.startswith("#")]
        self.assertIn(tl.NIGHT_HEADER, keys)

    def test_approve_night_records_once(self):
        rid = self.add("야간 일", "--night")
        self.assertEqual(run("approve-night", rid, "--card", "D-NQ1")[0], 0)
        first = self.rows()[0]["night"]
        self.assertTrue(first["approved_at"])
        self.assertEqual(first["approved_by_card"], "D-NQ1")
        self.assertEqual(run("approve-night", rid, "--card", "D-NQ2")[0], 0)  # 먼저 온 승인만 인정
        self.assertEqual(self.rows()[0]["night"], first)

    def test_approve_requires_night_row_and_dry_run_keeps_file(self):
        plain, night = self.add("낮 일"), self.add("야간 일", "--night")
        self.assertEqual(run("approve-night", plain, "--card", "D-NQ1")[0], 2)
        before = self.raw()
        self.assertEqual(run("approve-night", night, "--card", "D-NQ1", "--dry-run")[0], 0)
        self.assertEqual(self.raw(), before)
        self.assertEqual(run("approve-night", "T9999", "--card", "D-NQ1")[0], 3)

    def test_half_approval_rejected(self):
        rid = self.add("야간 일", "--night")
        rows = self.rows()
        rows[0]["night"]["approved_at"] = "2026-11-16T15:00:00+09:00"
        with self.assertRaises(tl.LedgerError):
            tl.check_rows(rows)

    def test_list_filters(self):
        a = self.add("낮 일")
        b = self.add("야간 미승인", "--night", "--gate", "auto")
        c = self.add("야간 승인", "--night", "--gate", "morning")
        run("approve-night", c, "--card", "D-NQ1")
        ids = lambda *f: [r["id"] for r in json.loads(run("list", "--json", *f)[1])]  # noqa: E731
        self.assertEqual(ids("--night"), [b, c])
        self.assertEqual(ids("--night-unapproved"), [b])
        self.assertEqual(ids("--gate", "auto"), [b])
        self.assertEqual(ids(), [a, b, c])

    def test_set_clears_and_night_off(self):
        rid = self.add("야간 일", "--night", "--gate", "auto")
        self.assertEqual(run("set", rid, "--gate", "", "--night-off")[0], 0)
        row = self.rows()[0]
        self.assertFalse({"gate", "night"} & set(row))

    def test_show_prints_night_lines(self):
        rid = self.add("야간 일", "--night", "--goal", "목표", "--question", "이름?|A|쉬움")
        out = run("show", rid)[1]
        self.assertIn("goal: 목표", out)
        self.assertIn("질문: 이름?", out)
        self.assertIn("미승인", out)


if __name__ == "__main__":
    unittest.main()
