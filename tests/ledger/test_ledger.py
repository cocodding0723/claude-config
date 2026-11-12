"""ledger.py 시험: add/set/list, 증거 없는 완료 거부, 원자적 쓰기, 동시 쓰기. 임시 폴더만 씀."""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "hubs", "scripts"))
import ledger  # noqa: E402

SEED = ("id\t접수\t요청(원문 요약)\t상태\t담당\t진행·증거\t갱신\n"
        "F1\t11-12\t첫 요청\t진행\t개인설정\t원인 분석 중\t2026-11-12 11:00\n")


def run(*argv):
    """main 실행 → (종료 코드, stdout, stderr)"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = ledger.main(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "ledger.tsv")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SEED)
        self.env = mock.patch.dict(os.environ, {"LRM_LEDGER": self.path})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def text(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_add_appends_row_in_intake_state(self):
        rc, out, _ = run("add", "--id", "F2", "--req", "새 요청", "--owner", "개인설정")
        self.assertEqual(rc, 0)
        row = ledger.read_rows(self.path)[1]
        self.assertEqual((row["id"], row["state"], row["request"], row["owner"]), ("F2", "접수", "새 요청", "개인설정"))
        self.assertRegex(row["updated"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertTrue(self.text().startswith(SEED))  # 기존 행 그대로

    def test_add_duplicate_id_exit_3(self):
        rc, _, err = run("add", "--id", "F1", "--req", "x", "--owner", "y")
        self.assertEqual(rc, 3)
        self.assertIn("id 중복", err)
        self.assertEqual(self.text(), SEED)

    def test_set_state_updates_time_and_note(self):
        rc, _, _ = run("set", "F1", "--state", "보류", "--note", "기획 확인 대기")
        self.assertEqual(rc, 0)
        row = ledger.read_rows(self.path)[0]
        self.assertEqual((row["state"], row["note"]), ("보류", "기획 확인 대기"))
        self.assertNotEqual(row["updated"], "2026-11-12 11:00")

    def test_done_without_evidence_rejected_and_file_untouched(self):
        rc, _, err = run("set", "F1", "--state", "완료")
        self.assertEqual(rc, 2)
        self.assertIn("증거 없는 완료 불가", err)
        self.assertEqual(self.text(), SEED)

    def test_done_with_blank_evidence_rejected(self):
        rc, _, _ = run("set", "F1", "--state", "완료", "--evidence", "   ")
        self.assertEqual(rc, 2)
        self.assertEqual(self.text(), SEED)

    def test_done_with_evidence_records_it(self):
        rc, _, _ = run("set", "F1", "--state", "완료", "--evidence", "3f2a9c1")
        self.assertEqual(rc, 0)
        row = ledger.read_rows(self.path)[0]
        self.assertEqual((row["state"], row["evidence"], row["note"]), ("완료", "3f2a9c1", "원인 분석 중"))

    def test_set_unknown_id_exit_3_and_bad_state_exit_2(self):
        self.assertEqual(run("set", "F9", "--state", "진행")[0], 3)
        self.assertEqual(run("set", "F1", "--state", "끝")[0], 2)

    def test_list_open_excludes_done_and_json_shape(self):
        run("add", "--id", "F2", "--req", "둘째", "--owner", "a")
        run("set", "F2", "--state", "완료", "--evidence", "abc")
        rc, out, _ = run("list", "--open", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual([r["id"] for r in json.loads(out)], ["F1"])
        all_ids = [r["id"] for r in json.loads(run("list", "--json")[1])]
        self.assertEqual(all_ids, ["F1", "F2"])

    def test_dry_run_does_not_write(self):
        run("add", "--id", "F2", "--req", "x", "--owner", "y", "--dry-run")
        run("set", "F1", "--state", "보류", "--dry-run")
        self.assertEqual(self.text(), SEED)

    def test_tabs_and_newlines_in_values_flattened(self):
        run("add", "--id", "F2", "--req", "줄\n바꿈\t탭", "--owner", "y")
        rows = ledger.read_rows(self.path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["request"], "줄 바꿈 탭")

    def test_quotes_survive_roundtrip(self):
        run("add", "--id", "F2", "--req", '따옴표 "있음"', "--owner", "y")
        run("set", "F2", "--state", "진행")
        self.assertEqual(ledger.read_rows(self.path)[1]["request"], '따옴표 "있음"')

    def test_atomic_write_failure_keeps_original_and_leaves_no_temp(self):
        with mock.patch("os.replace", side_effect=OSError(5, "I/O error")):
            rc, _, err = run("add", "--id", "F2", "--req", "x", "--owner", "y")
        self.assertEqual(rc, 1)
        self.assertIn("장부 쓰기 실패", err)
        self.assertEqual(self.text(), SEED)
        self.assertEqual([n for n in os.listdir(self.dir.name) if n.endswith(".tmp")], [])

    def test_write_goes_through_temp_then_replace(self):
        calls = []
        real = os.replace
        with mock.patch("os.replace", side_effect=lambda a, b: (calls.append((os.path.basename(a), b)), real(a, b))[1]):
            run("add", "--id", "F2", "--req", "x", "--owner", "y")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].startswith(".ledger-") and calls[0][0].endswith(".tmp"))
        self.assertEqual(calls[0][1], self.path)

    def test_concurrent_adds_lose_nothing(self):
        errs = []

        def worker(n):
            try:
                ledger.add(self.path, f"C{n}", "동시", "t", 0, False)
            except ledger.LedgerError as e:
                errs.append(str(e))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errs, [])
        ids = {r["id"] for r in ledger.read_rows(self.path)}
        self.assertEqual(ids, {"F1"} | {f"C{n}" for n in range(12)})

    def test_quoted_tab_and_newline_stay_in_one_cell_and_survive_set(self):
        """리뷰 재현: 요청 칸의 인용된 탭·줄바꿈이 항목을 둘로 쪼개고 set 이 잘린 장부를 덮어쓰던 결함"""
        head, seed = SEED.split("\n", 1)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(head + "\n" + '"F2"\t11-12\t"앞\t뒤\n다음 줄"\t진행\t개인설정\t분석\t2026-11-12 11:00\n' + seed)
        rows = ledger.read_rows(self.path)
        self.assertEqual([r["id"] for r in rows], ["F2", "F1"])
        self.assertEqual((rows[0]["request"], rows[0]["state"], rows[0]["owner"]), ("앞\t뒤\n다음 줄", "진행", "개인설정"))
        self.assertEqual(run("set", "F1", "--state", "보류")[0], 0)
        after = ledger.read_rows(self.path)
        self.assertEqual(after[0]["request"], "앞\t뒤\n다음 줄")
        self.assertEqual((len(after), after[1]["state"]), (2, "보류"))
        self.assertEqual(ledger.render(ledger.read_rows(self.path)), self.text())  # 왕복 동일

    def test_cell_with_quote_char_round_trips(self):
        ledger.add(self.path, "Q1", '5" 화면', "t", 0, False)
        self.assertEqual(ledger.read_rows(self.path)[1]["request"], '5" 화면')

    def test_row_wider_than_seven_columns_is_rejected_before_any_write(self):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("F9\t11-12\t요청\t진행\t담당\t메모\t2026-11-12 11:00\t여분\n")
        before = self.text()
        rc, _, err = run("set", "F1", "--state", "보류")
        self.assertEqual(rc, 2)
        self.assertIn("7열", err)
        self.assertEqual(self.text(), before)

    def test_unclosed_quote_is_rejected(self):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('F9\t11-12\t"안녕" 버튼\t진행\t담당\t\t2026-11-12 11:00\n')
        with self.assertRaises(ledger.LedgerError):
            ledger.read_rows(self.path)

    def test_check_rows_rejects_hand_edited_done_without_evidence(self):
        """리뷰 재현: 장부를 직접 고쳐 증거 없이 완료로 바꾸면 set 의 거부를 우회함"""
        ledger.check_rows(ledger.read_rows(self.path))  # 정상 장부는 통과
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("F2\t11-12\t요청\t완료\t담당\t메모만\t2026-11-12 11:00\n")
        with self.assertRaises(ledger.LedgerError) as cm:
            ledger.check_rows(ledger.read_rows(self.path))
        self.assertIn("F2: 증거 없는 완료", str(cm.exception))

    def test_check_rows_rejects_unknown_state_empty_and_duplicate_id(self):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("F1\t11-12\t중복\t진행\t담당\t\t2026-11-12 11:00\nF3\t11-12\t상태 오타\t끝\t담당\t\t2026-11-12 11:00\n"
                    "\t11-12\tid 없음\t진행\t담당\t\t2026-11-12 11:00\n")
        with self.assertRaises(ledger.LedgerError) as cm:
            ledger.check_rows(ledger.read_rows(self.path))
        for word in ("F1: id 중복", "F3: 알 수 없는 상태 '끝'", "(id 없음): id 비어 있음"):
            self.assertIn(word, str(cm.exception))

    def test_check_rows_rejects_whitespace_only_evidence(self):
        """재리뷰 재현: 증거 칸이 공백뿐이면 증거 있는 것으로 통과해 완료가 게시되던 결함"""
        for ev in (" ", "   ", "\u00a0"):
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(SEED + f"F2\t11-12\t요청\t완료\t담당\t메모 · 증거: {ev}\t2026-11-12 11:00\n")
            with self.assertRaises(ledger.LedgerError, msg=repr(ev)):
                ledger.check_rows(ledger.read_rows(self.path))

    def test_help_lists_commands_and_exit_codes(self):
        rc, out, _ = run("--help")
        self.assertEqual(rc, 0)
        for word in ("add", "set", "list", "종료 코드"):
            self.assertIn(word, out)


if __name__ == "__main__":
    unittest.main()
