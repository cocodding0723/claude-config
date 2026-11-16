"""task_ledger.py 시험: 반입 3종 멱등·중복 키, 증거·버림 사유·순환·없는 needs 거부, 깨진 줄, 스키마 칸, JSON 오류, 잠금, dry-run 무변경. 임시 폴더만 씀."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ISO = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$"
TZ = r"[+-]\d\d:\d\d$"
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
    {"type": "thread_reply", "ts": "1793858770.000001", "text": ":white_check_mark: B 로 받음. *다음을 사용하여 보냄* Claude", "parent_ts": "P3", "title": "결정: X"},  # 봇 중계
    {"type": "thread_reply", "ts": "1793858771.000001", "text": "문구가 있어도 사람 글 *다음을 사용하여 보냄*", "parent_ts": "P3", "user": "U1"},
    {"type": "note", "ts": "1793858772.000001", "text": "봇 id 가 있는 글", "bot_id": "B1"},
    {"type": "note", "ts": "1793858773.000001", "text": "ㅇㅋ"},
    {"type": "note", "ts": "1793858774.000001", "text": "이거 맞나요?"},
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
        self.assertEqual(run("set", rid, "--state", "완료", "--evidence", "PR #1", "--evidence", "abc1234")[0], 0)
        row = self.rows()[0]
        self.assertEqual((row["state"], row["evidence"]), ("완료", ["PR #1", "abc1234"]))
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
        self.assertEqual(len(rows), 8)
        texts = [r["text"] for r in rows]
        self.assertIn("전부 추천대로", texts)  # 결정 카드 답은 사용자 지시라 반입
        self.assertNotIn("봇 id 가 있는 글", texts)
        self.assertFalse([t for t in texts if t.startswith(":white_check_mark:")])
        self.assertIn("문구가 있어도 사람 글 *다음을 사용하여 보냄*", texts)  # user 가 있으면 문구 검사 생략
        self.assertTrue(all(r["state"] == "미분류" and r["hub"] == "" and r["sources"][0]["kind"] == "inbox" for r in rows))
        by = {r["text"]: r for r in rows}
        self.assertEqual(by["배경이 밋밋함"]["sources"][0]["context"], "파스텔 비교")
        self.assertEqual(by["제목 없는 답"]["sources"][0]["context"], "P2 부모 제목")  # 같은 parent_ts 다른 줄의 제목
        self.assertEqual(by["전부 추천대로"]["sources"][0]["context"], "결정: X · kind=decide")
        self.assertRegex(rows[0]["received_at"], ISO)
        self.assertEqual((by["ㅇㅋ"]["hint"], by["이거 맞나요?"]["hint"]), ("버림후보", "질문"))

    def test_inbox_idempotent(self):
        run(*self.inbox_cmd())
        first = self.raw()
        rc, out, _ = run(*self.inbox_cmd())
        self.assertEqual((rc, json.loads(out)["added"], json.loads(out)["skipped"]), (0, 0, 8))
        self.assertEqual(self.raw(), first)

    def test_feedback_state_mapping_and_evidence(self):
        rc, out, err = run("import-feedback", "--feedback", self.fb, "--json")
        self.assertEqual(rc, 0, err)
        by = {r["sources"][0]["ref"]: r for r in self.rows()}
        self.assertEqual({k: v["state"] for k, v in by.items()}, {"F1": "진행", "F2": "완료", "F3": "미분류", "F4": "막힘"})
        self.assertEqual(by["F2"]["evidence"], ["PR #10"])
        self.assertEqual((by["F1"]["hub"], by["F2"]["hub"], by["F4"]["hub"]), ("config", "main", ""))
        self.assertRegex(by["F1"]["received_at"], "^2026-11-12T00:00:00" + TZ)
        self.assertEqual(by["F4"]["blocked_reason"]["kind"], "기타")
        self.assertRegex(by["F2"]["done_at"], "^2026-11-13T09:00:00" + TZ)
        self.assertEqual(run("import-feedback", "--feedback", self.fb, "--json")[1].count('"added": 0'), 1)
        self.assertEqual(len(self.rows()), 4)

    def test_feedback_done_without_valid_evidence_imports_as_doing(self):
        with open(self.fb, "a", encoding="utf-8") as f:
            f.write("F5\t11-13\t증거 없음\t완료\t개인설정\t끝 · 증거: feat/x 병합, 로그 15:08\t2026-11-13 10:00\n")
        rc, out, err = run("import-feedback", "--feedback", self.fb, "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["demoted"], 1)
        row = next(r for r in self.rows() if r["sources"][0]["ref"] == "F5")
        self.assertEqual((row["state"], row["evidence"]), ("진행", []))

    def test_todo_groups_mapping(self):
        rc, _, err = run("import-todo", self.todo)
        self.assertEqual(rc, 0, err)
        rows = self.rows()
        self.assertEqual([(r["state"], r["hub"]) for r in rows], [("배정", "사용자"), ("진행", ""), ("막힘", ""), ("완료", ""), ("완료", "")])
        self.assertEqual(rows[0]["text"], "확인 창 누르기 — UI 만 No")
        self.assertEqual((rows[3]["text"], rows[3]["evidence"]), ("PR #730 병합 완료", ["PR #730"]))
        self.assertEqual(rows[3]["sources"][0]["ref"], "pr:730")
        self.assertRegex(rows[0]["received_at"], "^2026-11-16T00:00:00" + TZ)
        self.assertEqual(rows[2]["blocked_reason"]["kind"], "사용자답")

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


NEW_TODO = '<html><body><div class="c hub">트윈 줄이기</div><div class="c me">확인 창 누르기</div></body></html>'


class DupKeyTest(Base):
    def test_todo_reimport_under_next_day_file_adds_nothing(self):
        run("import-todo", self.todo)
        first, n = self.raw(), len(self.rows())
        tomorrow = os.path.join(self.dir.name, "할일목록-20261117.html")
        with open(self.todo, encoding="utf-8") as f, open(tomorrow, "w", encoding="utf-8") as g:
            g.write(f.read())
        rc, out, _ = run("import-todo", tomorrow, "--json")
        self.assertEqual((rc, json.loads(out)["added"]), (0, 0))
        self.assertEqual((self.raw(), len(self.rows())), (first, n))

    def test_same_sentence_in_other_group_is_same_task(self):
        run("import-todo", self.todo)
        other = os.path.join(self.dir.name, "x.html")
        with open(other, "w", encoding="utf-8") as g:
            g.write(NEW_TODO)
        self.assertEqual(json.loads(run("import-todo", other, "--json")[1])["added"], 0)

    def test_done_pr_key_is_global_across_feedback_and_todo(self):
        run("import-feedback", "--feedback", self.fb)  # F2 가 PR #10 증거
        other = os.path.join(self.dir.name, "d.html")
        with open(other, "w", encoding="utf-8") as g:
            g.write('<div class="c done">#10 #11 병합 완료</div>')
        res = json.loads(run("import-todo", other, "--json")[1])
        self.assertEqual((res["added"], res["skipped"]), (1, 1))  # #11 만 새로

    def test_feedback_after_todo_links_source_and_restores_request(self):
        other = os.path.join(self.dir.name, "d.html")
        with open(other, "w", encoding="utf-8") as g:
            g.write('<div class="c done">#10 병합 완료</div>')
        run("import-todo", other)
        res = json.loads(run("import-feedback", "--feedback", self.fb, "--json")[1])
        self.assertEqual((res["linked"], res["added"]), (1, 3))
        row = next(r for r in self.rows() if "PR #10" in r["evidence"])
        self.assertEqual((row["text"], [s["kind"] for s in row["sources"]]), ("둘째", ["todo-html", "feedback"]))
        again = json.loads(run("import-feedback", "--feedback", self.fb, "--json")[1])
        self.assertEqual((again["added"], again["linked"]), (0, 0))

    def test_similar_sentence_is_flagged_not_dropped(self):
        run("import-todo", self.todo)
        other = os.path.join(self.dir.name, "s.html")
        with open(other, "w", encoding="utf-8") as g:
            g.write('<div class="c me">확인 창 3 누르기 <small>UI 만 No</small></div>')
        rc, out, _ = run("import-todo", other, "--dry-run", "--json")
        res = json.loads(out)
        self.assertEqual((res["added"], len(res["dup_candidates"]), res["dup_candidates"][0]["similar_to"]), (1, 1, "T0001"))
        self.assertIn("중복 후보", run("import-todo", other, "--dry-run")[1])

    def test_distinct_pr_rows_are_not_flagged_as_similar(self):
        other = os.path.join(self.dir.name, "p.html")
        with open(other, "w", encoding="utf-8") as g:
            g.write('<div class="c done">#1 #2 #3 병합 완료</div>')
        res = json.loads(run("import-todo", other, "--dry-run", "--json")[1])
        self.assertEqual((res["added"], res["dup_candidates"]), (3, []))

    def test_link_adds_source_only(self):
        rid = self.add("원래 일")
        rc, out, err = run("add", "--text", "같은 일", "--link", rid, "--source-kind", "inbox", "--source-ref", "1.2", "--json")
        self.assertEqual(rc, 0, err)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual([s["kind"] for s in rows[0]["sources"]], ["manual", "inbox"])
        self.assertEqual(run("add", "--text", "x", "--link", rid, "--source-kind", "inbox", "--source-ref", "1.2")[0], 3)
        self.assertEqual(run("add", "--text", "x", "--link", "T0099", "--source-kind", "inbox", "--source-ref", "9")[0], 3)
        self.assertEqual(run("add", "--text", "x", "--link", rid)[0], 2)  # source-ref 없음


class DryCheckTest(Base):
    def test_add_dry_run_runs_same_checks(self):
        self.add("a")
        before = self.raw()
        self.assertEqual(run("add", "--text", "b", "--needs", "T9999", "--dry-run")[0], 2)
        self.assertEqual(run("add", "--text", "b", "--state", "완료", "--dry-run")[0], 2)
        self.assertEqual(self.raw(), before)


class BrokenLineTest(Base):
    def write_broken(self):
        good = json.dumps({"id": "T0001", "text": "a", "state": "미분류", "needs": [], "evidence": [], "sources": [], "history": []})
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{깨짐\n" + good + "\n[1]\n")

    def test_read_warns_and_skips(self):
        self.write_broken()
        rc, out, err = run("list", "--json")
        self.assertEqual((rc, [r["id"] for r in json.loads(out)]), (0, ["T0001"]))
        self.assertIn("깨진 줄 2건", err)

    def test_write_moves_bad_lines_raw_to_bad_file(self):
        self.write_broken()
        rc, _, err = run("add", "--text", "새 일")
        self.assertEqual(rc, 0)
        self.assertIn("2건", err)
        with open(self.path + ".bad", encoding="utf-8") as f:
            self.assertEqual(f.read(), "{깨짐\n[1]\n")
        self.assertEqual([r["id"] for r in self.rows()], ["T0001", "T0002"])
        self.assertEqual(self.raw().count("깨짐"), 0)

    def test_dry_run_leaves_bad_lines(self):
        self.write_broken()
        before = self.raw()
        run("add", "--text", "새 일", "--dry-run")
        self.assertEqual(self.raw(), before)
        self.assertFalse(os.path.exists(self.path + ".bad"))


class MissingDirTest(Base):
    def test_write_creates_missing_folder_before_lock(self):
        deep = os.path.join(self.dir.name, "a", "b", "tasks.jsonl")
        with mock.patch.dict(os.environ, {"LRM_TASKS": deep}):
            self.assertEqual(run("add", "--text", "x")[0], 0)
            self.assertEqual(run("import-todo", self.todo, "--dry-run")[0], 0)
        self.assertTrue(os.path.exists(deep))


class SchemaTest(Base):
    def test_timestamps_are_iso_with_tz_and_state_times_set_once(self):
        rid = self.add("일")
        run("set", rid, "--state", "배정")
        run("set", rid, "--state", "진행")
        row = self.rows()[0]
        for k in ("received_at", "updated_at", "assigned_at", "started_at"):
            self.assertRegex(row[k], ISO, k)
        self.assertEqual(row["done_at"], "")
        self.assertTrue(all(__import__("re").match(ISO, h["at"]) for h in row["history"]))
        started = row["started_at"]
        run("set", rid, "--state", "막힘")
        run("set", rid, "--state", "진행")
        self.assertEqual(self.rows()[0]["started_at"], started)  # 처음 들어간 시각 유지
        run("set", rid, "--state", "완료", "--evidence", "PR #3")
        done = self.rows()[0]
        self.assertRegex(done["done_at"], ISO)
        run("set", rid, "--state", "진행")
        self.assertEqual(self.rows()[0]["done_at"], "")  # 완료를 벗어나면 지움

    def test_blocked_reason_only_while_blocked_and_cleared_on_change(self):
        rid = self.add("일")
        self.assertEqual(run("set", rid, "--blocked-kind", "확인창")[0], 2)  # 막힘 아님
        self.assertEqual(run("set", rid, "--state", "막힘", "--blocked-kind", "확인창", "--blocked-note", "허브 7곳")[0], 0)
        br = self.rows()[0]["blocked_reason"]
        self.assertEqual((br["kind"], br["note"]), ("확인창", "허브 7곳"))
        self.assertRegex(br["since"], ISO)
        run("set", rid, "--blocked-note", "5곳")  # 상태 그대로 사유만 고침, since 유지
        self.assertEqual(self.rows()[0]["blocked_reason"]["since"], br["since"])
        run("set", rid, "--state", "진행")
        self.assertNotIn("blocked_reason", self.rows()[0])
        run("set", rid, "--state", "막힘")
        self.assertEqual(self.rows()[0]["blocked_reason"]["kind"], "기타")  # 기본값
        self.assertEqual(run("set", rid, "--state", "막힘", "--blocked-kind", "엉뚱")[0], 2)

    def test_order_and_outbox_optional_and_list_sorted(self):
        a, b, c = self.add("a"), self.add("b"), self.add("c")
        run("set", c, "--order", "1")
        run("set", b, "--order", "2", "--outbox-id", "O-7")
        _, out, _ = run("list", "--json")
        self.assertEqual([r["id"] for r in json.loads(out)], [c, b, a])
        self.assertNotIn("order", self.rows()[0])
        self.assertEqual(self.rows()[1]["outbox_id"], "O-7")
        run("set", b, "--order", "", "--outbox-id", "")
        self.assertNotIn("order", self.rows()[1])
        self.assertNotIn("outbox_id", self.rows()[1])
        self.assertEqual(run("set", a, "--order", "빠르게")[0], 2)

    def test_list_hub_normalized_and_derived_fields(self):
        a, b, c = self.add("a", "--hub", "coord-server-hub"), self.add("b"), self.add("c")
        run("set", b, "--needs", a)
        run("set", c, "--needs", f"{a},{b}")
        run("set", a, "--state", "완료", "--evidence", "PR #1")
        _, out, _ = run("list", "--hub", "coord-coord-server-hub-hub", "--json")  # 옛 별칭
        rows = json.loads(out)
        self.assertEqual([r["id"] for r in rows], [a])
        self.assertEqual(rows[0]["blocks_count"], 2)
        _, out, _ = run("list", "--json")
        by = {r["id"]: r for r in json.loads(out)}
        self.assertEqual((by[b]["blocked_by"], by[c]["blocked_by"], by[a]["blocked_by"]), ([], [b], []))  # a 는 완료라 풀림
        self.assertEqual(json.loads(run("show", c, "--json")[1])["blocked_by"], [b])


class DroppedTest(Base):
    def test_drop_requires_reason_and_needs_no_evidence(self):
        rid = self.add("일")
        rc, _, err = run("set", rid, "--state", "버림")
        self.assertEqual(rc, 2)
        self.assertIn("사유", err)
        self.assertEqual(run("set", rid, "--state", "버림", "--reason", "기획 변경")[0], 0)
        self.assertEqual(self.rows()[0]["dropped_reason"], "기획 변경")
        self.assertEqual(run("set", rid, "--reason", "다른 이유")[0], 0)
        run("set", rid, "--state", "진행")
        self.assertNotIn("dropped_reason", self.rows()[0])
        self.assertEqual(run("set", rid, "--reason", "x")[0], 2)  # 버림 아님

    def test_dropped_prerequisite_stays_unresolved(self):
        a, b = self.add("a"), self.add("b")
        run("set", b, "--needs", a)
        run("set", a, "--state", "버림", "--reason", "취소")
        self.assertEqual(json.loads(run("show", b, "--json")[1])["blocked_by"], [a])


class JsonErrorTest(Base):
    def test_errors_are_json_objects_with_hint(self):
        rc, out, err = run("set", "T0042", "--state", "진행", "--json")
        self.assertEqual(rc, 3)
        d = json.loads(out)
        self.assertEqual((d["code"], bool(d["error"]), bool(d["hint"])), (3, True, True))
        self.assertEqual(err, "")
        rc, out, _ = run("set", "T0001", "--state", "끝", "--json")  # 인자 오류도
        self.assertEqual((rc, json.loads(out)["code"]), (2, 2))
        self.assertEqual(run("list", "--json")[0], 0)

    def test_plain_errors_stay_text(self):
        rc, out, err = run("set", "T0042", "--state", "진행")
        self.assertEqual((rc, out), (3, ""))
        self.assertTrue(err.startswith("오류: "))

    def test_help_states_json_shapes(self):
        _, out, _ = run("--help")
        self.assertIn("list 는 배열", out)
        self.assertIn("나머지 명령은 객체", out)


class EvidenceFormTest(Base):
    def test_only_known_forms_accepted(self):
        for ok in ("PR #12", "cocodding0723/lrm#12", "abc1234", "0123456789abcdef", "https://github.com/x/y/pull/1"):
            rid = self.add(ok)
            self.assertEqual(run("set", rid, "--state", "완료", "--evidence", ok)[0], 0, ok)
        rid = self.add("나쁜 증거")
        for bad in ("병합함", "PR 12", "#12", "abc123", "feat/x"):
            rc, _, err = run("set", rid, "--state", "완료", "--evidence", bad)
            self.assertEqual(rc, 2, bad)
            self.assertIn("허용 형식", err)
        self.assertEqual(run("add", "--text", "x", "--evidence", "병합함")[0], 2)
        self.assertEqual(self.rows()[-1]["state"], "미분류")


if __name__ == "__main__":
    unittest.main()
