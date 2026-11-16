"""task_ledger.py 시험: 반입 3종 멱등, 증거 없는 완료·순환·없는 needs 거부, 잠금, dry-run 무변경. 임시 폴더만 씀."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = os.path.join(REPO, "hubs", "scripts")
ROUTES = os.path.join(REPO, "hubs", "intake-routes.tsv")
sys.path.insert(0, SCRIPTS)
import task_ledger as tl  # noqa: E402

INBOX = [
    {"type": "note", "ts": "1793858751.000001", "text": "스파인도 일단 킵"},
    {"type": "note", "ts": "1793858752.000001", "text": "대기"},  # 봇 즉답 명령
    {"type": "note", "ts": "1793858753.000001", "text": "   "},
    {"type": "thread_reply", "ts": "1793858760.000001", "text": "배경이 밋밋함", "parent_ts": "P1", "title": "파스텔 비교"},
    {"type": "thread_reply", "ts": "1793858761.000001", "text": "제목 없는 답", "parent_ts": "P2"},
    {"type": "thread_reply", "ts": "1793858762.000001", "text": "전부 추천대로", "parent_ts": "P3", "title": "결정: X", "kind": "decide", "decision_id": "D-1", "action": "free"},
    {"type": "button", "ts": "1793858763.000001", "text": "1. A", "parent_ts": "P3", "title": "결정: X", "decision_id": "D-1"},
    {"type": "reaction", "emoji": "a", "parent_ts": "P1", "title": "파스텔 비교"},
    {"type": "note", "ts": "1793858764.000001", "text": "스파인도 일단 킵 (재전송)"},
    {"type": "message", "ts": "1", "text": "알 수 없는 종류"},
]
INBOX_EXTRA = [{"type": "reaction", "parent_ts": "P2", "title": "P2 부모 제목", "emoji": "x"}]
HUBS = "# slug\t이름\tcwd\nvfx\t연출·VFX 폴리싱\t-\nconfig\t개인 설정\t-\ncoord-server-hub\t서버\t-\n"
FEEDBACK = ("id\t접수\t요청(원문 요약)\t상태\t담당\t진행·증거\t갱신\n"
            "F1\t11-12\t첫 요청\t진행\t개인설정\t원인 분석 중\t2026-11-12 11:00\n"
            "F2\t11-12\t둘째\t완료\tmain\t끝남 · 증거: PR #10\t2026-11-13 09:00\n"
            "F3\t11-12\t셋째\t접수\t개인설정\t\t2026-11-12 12:00\n"
            "F4\t11-12\t넷째\t보류\t없는담당\t대기\t2026-11-12 13:00\n")
TODO = """<html><body><h2>직접</h2>
<div class="c me">확인 창 누르기 <small>UI 만 No</small></div>
<div class="c hub">트윈 줄이기</div>
<div class="c q">가상 커서가 사라지는지</div>
<div class="c done">#730 #726 병합 완료</div>
</body></html>"""


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = tl.main(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.path = os.path.join(d, "tasks.jsonl")
        self.inbox = os.path.join(d, "inbox.jsonl")
        self.hubs = os.path.join(d, "hubs.tsv")
        self.fb = os.path.join(d, "fb.tsv")
        self.todo = os.path.join(d, "할일목록-20261116.html")
        with open(self.inbox, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(i, ensure_ascii=False) for i in INBOX + INBOX_EXTRA) + "\n깨진 줄\n")
        for p, t in ((self.hubs, HUBS), (self.fb, FEEDBACK), (self.todo, TODO)):
            with open(p, "w", encoding="utf-8") as f:
                f.write(t)
        self.env = mock.patch.dict(os.environ, {"LRM_TASKS": self.path, "LRM_HUBS_TABLE": self.hubs})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def rows(self):
        return tl.read_rows(self.path)

    def raw(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def add(self, text="일", *extra):
        rc, out, err = run("add", "--text", text, "--json", *extra)
        self.assertEqual(rc, 0, err)
        return json.loads(out)["id"]


class CrudTest(Base):
    def test_add_then_set_flow_records_history(self):
        rid = self.add("지시 하나", "--hub", "vfx")
        self.assertEqual(rid, "T0001")
        self.assertEqual(run("set", rid, "--state", "진행", "--note", "착수")[0], 0)
        self.assertEqual(run("set", rid, "--state", "완료", "--evidence", "PR #1", "--evidence", "abc123")[0], 0)
        row = self.rows()[0]
        self.assertEqual((row["state"], row["evidence"]), ("완료", ["PR #1", "abc123"]))
        self.assertEqual([h["state"] for h in row["history"]], ["미분류", "진행", "완료"])

    def test_done_without_evidence_exits_2_and_keeps_file(self):
        rid = self.add()
        before = self.raw()
        rc, _, err = run("set", rid, "--state", "완료")
        self.assertEqual(rc, 2)
        self.assertIn("증거", err)
        self.assertEqual(self.raw(), before)

    def test_done_accepts_evidence_already_on_row(self):
        rid = self.add("일", "--evidence", "PR #5")
        self.assertEqual(run("set", rid, "--state", "완료")[0], 0)

    def test_needs_unknown_id_rejected(self):
        rid = self.add()
        rc, _, err = run("set", rid, "--needs", "T9999")
        self.assertEqual(rc, 2)
        self.assertIn("T9999", err)

    def test_self_and_cycle_rejected(self):
        a, b, c = self.add("a"), self.add("b"), self.add("c")
        self.assertEqual(run("set", a, "--needs", a)[0], 2)
        self.assertEqual(run("set", a, "--needs", b)[0], 0)
        self.assertEqual(run("set", b, "--needs", c)[0], 0)
        before = self.raw()
        rc, _, err = run("set", c, "--needs", a)
        self.assertEqual(rc, 2)
        self.assertIn("순환", err)
        self.assertEqual(self.raw(), before)

    def test_missing_id_exit_3_and_bad_state_exit_2(self):
        self.assertEqual(run("set", "T0042", "--state", "진행")[0], 3)
        self.assertEqual(run("show", "T0042")[0], 3)
        self.assertEqual(run("set", "T0001", "--state", "끝")[0], 2)

    def test_hub_validated_against_table_and_normalized(self):
        self.assertEqual(run("add", "--text", "x", "--hub", "nope")[0], 2)
        rid = self.add("y", "--hub", "coord-coord-server-hub-hub")  # 옛 별칭 → hubs.tsv slug
        self.assertEqual(self.rows()[0]["hub"], "coord-server-hub")
        self.add("z", "--hub", "사용자")
        _, out, _ = run("list", "--hub", "사용자", "--json")
        self.assertEqual(len(json.loads(out)), 1)

    def test_same_source_ref_added_once(self):
        self.assertEqual(run("add", "--text", "a", "--source-kind", "manual", "--source-ref", "R1")[0], 0)
        self.assertEqual(run("add", "--text", "a", "--source-kind", "manual", "--source-ref", "R1")[0], 3)
        self.assertEqual(len(self.rows()), 1)

    def test_list_filters_and_show(self):
        a = self.add("a")
        self.add("b")
        run("set", a, "--state", "막힘")
        _, out, _ = run("list", "--state", "막힘", "--json")
        self.assertEqual([r["id"] for r in json.loads(out)], [a])
        rc, out, _ = run("show", a)
        self.assertEqual(rc, 0)
        self.assertIn("막힘", out)

    def test_corrupt_line_exit_1(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{깨짐\n")
        self.assertEqual(run("list")[0], 1)


class DryRunTest(Base):
    def test_dry_run_never_touches_file(self):
        self.assertEqual(run("add", "--text", "a", "--dry-run")[0], 0)
        self.assertIsNone(self.raw())
        rid = self.add("b")
        before = self.raw()
        self.assertEqual(run("set", rid, "--state", "진행", "--dry-run")[0], 0)
        self.assertEqual(run("set", rid, "--state", "완료", "--dry-run")[0], 2)  # 검사는 그대로
        for cmd in (["import-inbox", "--inbox", self.inbox, "--routes", ROUTES], ["import-feedback", "--feedback", self.fb], ["import-todo", self.todo]):
            rc, out, _ = run(*cmd, "--dry-run", "--json")
            self.assertEqual(rc, 0)
            self.assertGreater(json.loads(out)["added"], 0)
        self.assertEqual(self.raw(), before)


class ImportTest(Base):
    def inbox_cmd(self):
        return ["import-inbox", "--inbox", self.inbox, "--routes", ROUTES, "--json"]

    def test_inbox_keeps_only_user_free_text_with_parent_context(self):
        rc, out, err = run(*self.inbox_cmd())
        self.assertEqual(rc, 0, err)
        rows = self.rows()
        self.assertEqual([r["text"] for r in rows], ["스파인도 일단 킵", "배경이 밋밋함", "제목 없는 답", "스파인도 일단 킵 (재전송)"])
        self.assertTrue(all(r["state"] == "미분류" and r["hub"] == "" and r["source"]["kind"] == "inbox" for r in rows))
        self.assertEqual(rows[1]["source"]["context"], "파스텔 비교")
        self.assertEqual(rows[2]["source"]["context"], "P2 부모 제목")  # 같은 parent_ts 다른 줄의 제목
        self.assertRegex(rows[0]["received_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d$")

    def test_inbox_idempotent(self):
        run(*self.inbox_cmd())
        first = self.raw()
        rc, out, _ = run(*self.inbox_cmd())
        self.assertEqual((rc, json.loads(out)["added"], json.loads(out)["skipped"]), (0, 0, 4))
        self.assertEqual(self.raw(), first)

    def test_feedback_state_mapping_and_evidence(self):
        rc, out, err = run("import-feedback", "--feedback", self.fb, "--json")
        self.assertEqual(rc, 0, err)
        by = {r["source"]["ref"]: r for r in self.rows()}
        self.assertEqual({k: v["state"] for k, v in by.items()}, {"F1": "진행", "F2": "완료", "F3": "미분류", "F4": "막힘"})
        self.assertEqual(by["F2"]["evidence"], ["PR #10"])
        self.assertEqual((by["F1"]["hub"], by["F2"]["hub"], by["F4"]["hub"]), ("config", "main", ""))
        self.assertEqual(by["F1"]["received_at"], "2026-11-12 00:00")
        self.assertEqual(run("import-feedback", "--feedback", self.fb, "--json")[1].count('"added": 0'), 1)
        self.assertEqual(len(self.rows()), 4)

    def test_feedback_done_without_evidence_writes_nothing(self):
        with open(self.fb, "a", encoding="utf-8") as f:
            f.write("F5\t11-13\t증거 없음\t완료\t개인설정\t끝\t2026-11-13 10:00\n")
        rc, _, err = run("import-feedback", "--feedback", self.fb)
        self.assertEqual(rc, 2)
        self.assertIn("F5", err)
        self.assertIsNone(self.raw())

    def test_todo_groups_mapping(self):
        rc, _, err = run("import-todo", self.todo)
        self.assertEqual(rc, 0, err)
        rows = self.rows()
        self.assertEqual([(r["state"], r["hub"]) for r in rows], [("배정", "사용자"), ("진행", ""), ("막힘", ""), ("완료", ""), ("완료", "")])
        self.assertEqual(rows[0]["text"], "확인 창 누르기 — UI 만 No")
        self.assertEqual((rows[3]["text"], rows[3]["evidence"]), ("PR #730 병합 완료", ["PR #730"]))
        self.assertEqual(rows[0]["received_at"], "2026-11-16 00:00")

    def test_todo_idempotent_and_new_item_only(self):
        run("import-todo", self.todo)
        first = self.raw()
        self.assertEqual(run("import-todo", self.todo)[0], 0)
        self.assertEqual(self.raw(), first)
        with open(self.todo, "a", encoding="utf-8") as f:
            f.write('<div class="c hub">새 일</div>')
        run("import-todo", self.todo)
        self.assertEqual(len(self.rows()), 6)

    def test_ids_stay_unique_across_imports(self):
        run("import-todo", self.todo)
        run("import-feedback", "--feedback", self.fb)
        ids = [r["id"] for r in self.rows()]
        self.assertEqual(len(ids), len(set(ids)))


class LockTest(Base):
    def test_parallel_adds_all_land_with_unique_ids(self):
        env = {**os.environ, "LRM_TASKS": self.path}
        procs = [subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "task_ledger.py"), "add", "--text", f"동시 {i}"], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for i in range(12)]
        for p in procs:
            _, err = p.communicate()
            self.assertEqual(p.returncode, 0, err)
        ids = [r["id"] for r in self.rows()]
        self.assertEqual(sorted(ids), [f"T{n:04d}" for n in range(1, 13)])

    def test_no_temp_files_left_behind(self):
        self.add()
        self.assertEqual(sorted(os.listdir(self.dir.name)), sorted(["tasks.jsonl", "tasks.jsonl.lock", "inbox.jsonl", "hubs.tsv", "fb.tsv", os.path.basename(self.todo)]))


if __name__ == "__main__":
    unittest.main()
