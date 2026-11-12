"""명령 접수기 시험: 번호·장부·라우팅·멱등·머리말·동시 실행·복사 설치 import"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = os.path.join(REPO, "hubs", "scripts")
TABLE = os.path.join(REPO, "hubs", "intake-routes.tsv")
# 화면 보강 확인(slack_push 설정)을 시험용으로 짧게. intake 가 slack_push 를 import 하기 전에 정해야 함
os.environ.update(LRM_PUSH_CONFIRM="1", LRM_PUSH_TEST_CONFIRM_GAP="0.05", LRM_PUSH_CONFIRM_TICK="0.05")
sys.path.insert(0, SCRIPTS)
import intake  # noqa: E402
import ledger  # noqa: E402

NOW = time.time()


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def msg(text, n=0, **kw):
    return {"type": "note", "ts": f"{NOW - 100 + n:.6f}", "text": text, **kw}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="intake-test-")
        self.addCleanup(shutil.rmtree, self.d, True)
        p = lambda n: os.path.join(self.d, n)  # noqa: E731
        self.conf = intake.Conf(inbox=p("inbox.jsonl"), state=p("state.json"), routes_log=p("routes.jsonl"),
                                ledger=p("ledger.tsv"), table=TABLE)

    def put(self, *items):
        with open(self.conf.inbox, "a", encoding="utf-8") as f:
            for it in items:
                f.write((it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)) + "\n")

    def run_intake(self):
        return intake.intake(self.conf, NOW)

    def routes(self):
        with open(self.conf.routes_log, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f]


class CoreTest(Base):
    def test_three_commands_numbers_ledger_routes_then_rerun_zero(self):
        self.put(msg("로그인 버튼 색 바꿔줘", 1), msg("상점 팝업 확인", 2), msg("튜토리얼 문구 점검", 3))
        res = self.run_intake()
        self.assertEqual([n["id"] for n in res["new"]], ["C0001", "C0002", "C0003"])
        rows = ledger.read_rows(self.conf.ledger)
        self.assertEqual([r["id"] for r in rows], ["C0001", "C0002", "C0003"])
        self.assertTrue(all(r["state"] == "접수" for r in rows))
        self.assertEqual(len(self.routes()), 3)
        again = self.run_intake()
        self.assertEqual(again["new"], [])
        self.assertEqual(len(ledger.read_rows(self.conf.ledger)), 3)
        self.assertEqual(len(self.routes()), 3)

    def test_new_line_after_rerun_gets_next_number(self):
        self.put(msg("a", 1))
        self.run_intake()
        self.put(msg("b", 2))
        res = self.run_intake()
        self.assertEqual([n["id"] for n in res["new"]], ["C0002"])

    def test_excluded_kinds_do_not_consume_numbers(self):
        self.put({"type": "reaction", "ts": f"{NOW - 90:.6f}", "emoji": "white_check_mark"},
                 msg("답글", 2, type="thread_reply", parent_ts="1"),
                 msg("대기", 3),
                 msg("   ", 4),
                 "깨진 줄",
                 msg("진짜 명령", 5))
        res = self.run_intake()
        self.assertEqual([n["id"] for n in res["new"]], ["C0001"])
        self.assertEqual(res["skipped"], 4)  # 반응·답글·봇 즉답·빈 줄

    def test_instant_reply_commands_excluded_but_decision_command_taken(self):
        replies = ["대기", "미승인", "승인 대기", "PR", "pr", "초안 PR", "할일", "할 일", "CI", "ci", "문서 정리", "  대기  ", "앞줄\n대기"]
        self.put(*[msg(t, i) for i, t in enumerate(replies)],
                 msg("보류 작업 중단", 20, type="command", command="hold"),
                 msg("문서", 21), msg("PR 확인해줘", 22), msg("대기열 점검", 23))
        res = self.run_intake()
        texts = [r["text"] for r in self.routes()]
        self.assertEqual(texts, ["보류 작업 중단", "문서", "PR 확인해줘", "대기열 점검"])
        self.assertEqual(len(res["new"]), 4)

    def test_reply_table_matches_slack_poll_contract(self):
        """즉답 목록이 slack_poll.sh 와 어긋나면 한쪽만 접수·제외하게 됨"""
        import re
        with open(os.path.join(SCRIPTS, "slack_poll.sh"), encoding="utf-8") as f:
            src = f.read()
        exact = set(re.findall(r'"([^"]+)"', re.search(r"CMD_REPLY_EXACT = frozenset\(\{(.*?)\}\)", src).group(1)))
        prefix = re.search(r'CMD_REPLY_PREFIX = "(.*)"', src).group(1)
        rt = intake.load_routes(TABLE)
        self.assertEqual(rt.exact, exact)
        self.assertEqual([p + " " for p in rt.prefixes], [prefix])

    def test_prefix_only_command_skipped_and_rest_continue(self):
        self.put(msg("ui:", 1), msg("vfx：  ", 2), msg("정상 명령", 3))
        res = self.run_intake()
        self.assertEqual([n["id"] for n in res["new"]], ["C0001"])
        self.assertEqual(res["skipped"], 2)
        seen = load_json(self.conf.state)["seen"]
        self.assertEqual(sorted(v for v in seen.values()), ["C0001", intake.SKIP_EMPTY, intake.SKIP_EMPTY])
        self.assertEqual(self.run_intake()["new"], [])

    def test_first_install_skips_old_backlog_but_running_state_takes_old_unprocessed(self):
        self.put(msg("이관 전 묵은 명령", -200000), msg("최근 명령", 1))
        res = self.run_intake()
        self.assertEqual([n["id"] for n in res["new"]], ["C0001"])
        self.assertIn(intake.SKIP_OLD, load_json(self.conf.state)["seen"].values())
        self.put(msg("운영 중 26시간 묵은 미처리 명령", -93600))
        res = self.run_intake()
        self.assertEqual([n["id"] for n in res["new"]], ["C0002"])
        self.conf.max_age_s = 10 ** 9  # 기한을 늘려도 이미 제외한 이관분은 다시 접수하지 않음(재이관은 상태 삭제로만)
        self.assertEqual(self.run_intake()["new"], [])

    def test_state_lost_after_ledger_write_does_not_swallow_next_command(self):
        """A 의 장부·기록만 남고 상태 저장이 실패한 뒤 B 가 추가돼도 B 가 A 번호를 가로채 누락되지 않음"""
        a, b = msg("명령 A", 1), msg("명령 B", 3)
        self.put(a)
        self.run_intake()
        os.unlink(self.conf.state)  # 상태 저장 실패 재현
        self.put(b)
        res = self.run_intake()
        self.assertEqual({n["ts"]: n["id"] for n in res["new"]}, {a["ts"]: "C0001", b["ts"]: "C0002"})
        self.assertEqual([r["request"] for r in ledger.read_rows(self.conf.ledger)], ["명령 A", "명령 B"])
        self.assertEqual([r["ts"] for r in self.routes()], [a["ts"], b["ts"]])

    def test_number_reserved_before_ledger_write(self):
        a = msg("명령 A", 1)
        self.put(a)
        real_add, seen = ledger.add, {}

        def boom(*args, **kw):
            seen["pending"] = load_json(self.conf.state)["pending"]
            raise ledger.LedgerError("디스크 오류", 1)
        ledger.add = boom
        try:
            with self.assertRaises(ledger.LedgerError):
                self.run_intake()
        finally:
            ledger.add = real_add
        self.assertEqual(seen["pending"], {a["ts"]: "C0001"})  # 장부 쓰기 시점에 이미 예약이 저장됨
        b = msg("명령 B", 0.5)  # 재실행 때 inbox 앞쪽에 다른 명령이 있어도 A 번호는 A 의 것
        self.put(b)
        res = self.run_intake()
        self.assertEqual({n["ts"]: n["id"] for n in res["new"]}, {a["ts"]: "C0001", b["ts"]: "C0002"})

    def test_taken_number_with_other_content_gets_new_number(self):
        ledger.add(self.conf.ledger, "C0001", "손으로 넣은 항목", "메인", NOW, False)
        self.put(msg("명령 A", 1))
        res = self.run_intake()
        self.assertEqual([n["id"] for n in res["new"]], ["C0002"])
        self.assertEqual(len(ledger.read_rows(self.conf.ledger)), 2)

    def test_dry_run_writes_nothing(self):
        self.put(msg("a", 1))
        res = intake.intake(self.conf, NOW, dry=True)
        self.assertEqual(len(res["new"]), 1)
        for n in ("ledger.tsv", "state.json", "routes.jsonl"):
            self.assertFalse(os.path.exists(os.path.join(self.d, n)), n)

    def test_resume_after_crash_between_ledger_and_state(self):
        self.put(msg("a", 1))
        ledger.add(self.conf.ledger, "C0001", "a", "main", NOW, False)  # 장부만 쓰고 죽은 회차
        res = self.run_intake()
        self.assertEqual([n["id"] for n in res["new"]], ["C0001"])
        self.assertEqual(len(ledger.read_rows(self.conf.ledger)), 1)
        self.assertEqual(len(self.routes()), 1)


class RouteTest(Base):
    def test_prefix_routes(self):
        cases = [("ui: 버튼", "ui", "버튼"), ("vfx: 이펙트", "vfx", "이펙트"), ("qa: 회귀", "test", "회귀"),
                 ("data: 시트", "data", "시트"), ("config: 훅", "config", "훅"),
                 ("UI：전각 콜론", "ui", "전각 콜론"), ("그냥 명령", "main", "그냥 명령"), ("foo: 모르는 머리말", "main", "foo: 모르는 머리말")]
        self.put(*[msg(t, i) for i, (t, _, _) in enumerate(cases)])
        self.run_intake()
        got = [(r["hub"], r["text"]) for r in self.routes()]
        self.assertEqual(got, [(h, b) for _, h, b in cases])
        owners = [r["owner"] for r in ledger.read_rows(self.conf.ledger)]
        self.assertEqual(owners, [h for _, h, _ in cases])

    def test_prefix_only_on_first_line_start(self):
        self.put(msg("먼저 확인\nui: 나중", 1))
        self.run_intake()
        self.assertEqual(self.routes()[0]["hub"], "main")

    def test_bad_table(self):
        bad = os.path.join(self.d, "bad.tsv")
        with open(bad, "w") as f:
            f.write("ui\tui\n")  # 기본 행 없음
        self.conf.table = bad
        with self.assertRaises(ledger.LedgerError) as cm:
            self.run_intake()
        self.assertEqual(cm.exception.code, 2)


class ProcessTest(Base):
    def setUp(self):
        super().setUp()
        self.conf.inbox = os.path.join(self.d, "slack-inbox.jsonl")  # 프로세스 실행은 기본 파일명을 읽음

    def env(self, home=None):
        e = {k: v for k, v in os.environ.items() if not k.startswith("LRM_")}
        e.update(LRM_HUB_DIR=self.d, LRM_INTAKE_ROUTES=TABLE, LRM_ORCA="/usr/bin/false")  # 하달 단계가 실제 Orca 에 닿지 않게
        return e

    def test_concurrent_runs_no_duplicates(self):
        self.put(*[msg(f"명령{i}", i) for i in range(6)])
        script = os.path.join(SCRIPTS, "intake.py")
        procs = [subprocess.Popen([sys.executable, script, "--once", "--json"], env=self.env(), stdout=subprocess.PIPE) for _ in range(4)]
        for p in procs:
            p.communicate()
            self.assertEqual(p.returncode, 0)
        ids = [r["id"] for r in ledger.read_rows(os.path.join(self.d, "feedback-ledger.tsv"))]
        self.assertEqual(sorted(ids), [f"C000{i}" for i in range(1, 7)])
        self.assertEqual(len(self.routes_default()), 6)

    def hold(self, path, secs):
        """다른 프로세스가 잠금을 secs 초 쥐고 있는 상태. 잠금을 잡은 뒤 반환"""
        code = ("import fcntl,sys,time;f=open(sys.argv[1]+'.lock','a');fcntl.flock(f,fcntl.LOCK_EX);"
                "print('ready',flush=True);time.sleep(float(sys.argv[2]))")
        proc = subprocess.Popen([sys.executable, "-c", code, path, str(secs)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.kill)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        return proc

    def run_cli(self, budget):
        env = self.env()
        env["LRM_INTAKE_BUDGET_S"] = str(budget)
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "intake.py"), "--once", "--json"], env=env, capture_output=True, text=True, timeout=30)
        return r, time.monotonic() - t0

    def test_state_lock_held_returns_busy_within_budget_then_next_round_takes_all(self):
        self.put(msg("a", 1), msg("b", 2))
        proc = self.hold(os.path.join(self.d, "intake-state.json"), 8)
        r, took = self.run_cli(0.5)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertLess(took, 4)
        self.assertFalse(os.path.exists(os.path.join(self.d, "feedback-ledger.tsv")))
        proc.kill()
        proc.wait()
        r, _ = self.run_cli(5)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([n["id"] for n in json.loads(r.stdout)["new"]], ["C0001", "C0002"])

    def test_ledger_lock_held_returns_busy_then_resumes_same_number(self):
        self.put(msg("a", 1))
        proc = self.hold(os.path.join(self.d, "feedback-ledger.tsv"), 8)
        r, took = self.run_cli(0.5)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertLess(took, 4)
        proc.kill()
        proc.wait()
        r, _ = self.run_cli(5)
        self.assertEqual([n["id"] for n in json.loads(r.stdout)["new"]], ["C0001"])
        self.assertEqual(len(ledger.read_rows(os.path.join(self.d, "feedback-ledger.tsv"))), 1)

    def routes_default(self):
        with open(os.path.join(self.d, "intake-routes.jsonl"), encoding="utf-8") as f:
            return [json.loads(ln) for ln in f]

    def test_default_ledger_stays_inside_hub_dir(self):
        self.put(msg("a", 1))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "intake.py"), "--once"], env=self.env(), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.d, "feedback-ledger.tsv")))

    def test_copy_install_imports_sibling_ledger(self):
        """복사 설치본: 설치 폴더에는 intake.py·ledger.py·main_terminal.py·slack_push.py 만 있어도 -I 로 import 되어야 함(links.tsv 등록 누락 방지)"""
        inst = os.path.join(self.d, "installed")
        os.mkdir(inst)
        for n in ("intake.py", "ledger.py", "main_terminal.py", "slack_push.py"):
            shutil.copy(os.path.join(SCRIPTS, n), os.path.join(inst, n))
        self.put(msg("a", 1), msg("ui: b", 2))
        r = subprocess.run([sys.executable, "-I", os.path.join(inst, "intake.py"), "--once", "--json"], cwd=self.d, env=self.env(), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([n["hub"] for n in json.loads(r.stdout)["new"]], ["main", "ui"])

    def test_help_and_exit_codes(self):
        script = os.path.join(SCRIPTS, "intake.py")
        self.assertEqual(subprocess.run([sys.executable, script, "--help"], capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run([sys.executable, script], capture_output=True).returncode, 2)


FAKE_ORCA = os.path.join(REPO, "tests", "slack_poll", "fake_orca.sh")
HUB_H, MAIN_H = "term_aaaaaaaa-0000-4000-8000-000000000001", "term_bbbbbbbb-0000-4000-8000-000000000002"
HUBS_TSV = "# slug\tname\trole_file\tws_root\trepo\nui-layer\tUI\t~/.config/lrm-hubs/roles/role-ui.txt\t-\t-\n"


class DeliverTest(Base):
    """접수 → 담당 허브 터미널 하달. 가짜 orca(tests/slack_poll/fake_orca.sh): 실제 Orca·Slack 에 접속하지 않음"""

    def setUp(self):
        super().setUp()
        self.fo = os.path.join(self.d, "fo")
        os.mkdir(self.fo)
        os.environ.update(FAKE_ORCA_DIR=self.fo, FAKE_MAIN="/main/checkout")
        for k in ("FAKE_ORCA_DIR", "FAKE_MAIN"):
            self.addCleanup(os.environ.pop, k, None)
        p = lambda n: os.path.join(self.d, n)  # noqa: E731
        with open(p("hubs.tsv"), "w", encoding="utf-8") as f:
            f.write(HUBS_TSV)
        with open(p("main.env"), "w", encoding="utf-8") as f:
            f.write(f"MAIN_TERMINAL={MAIN_H}\n")
        self.conf.hubs_table, self.conf.main_env, self.conf.sent, self.conf.orca = p("hubs.tsv"), p("main.env"), p("sent.json"), FAKE_ORCA
        self.conf.ws_root = "/ws"
        self.conf.main_spec = intake.mt.Spec(main_path="/main/checkout", pane="")
        self.terms(self.hub_term())

    def hub_term(self, slug="ui-layer", handle=HUB_H, **kw):
        return {"handle": handle, "worktreePath": f"/ws/coord-{slug}-hub", "agentIdentity": "claude", "connected": True, "writable": True,
                "orphaned": False, "title": "hub", **kw}

    def terms(self, *rows):
        with open(os.path.join(self.fo, "list.json"), "w", encoding="utf-8") as f:
            json.dump(list(rows), f)

    def mode(self, name, value):
        with open(os.path.join(self.fo, name), "w", encoding="utf-8") as f:
            f.write(value)

    DRAFT = "❯ [Pasted text #1 +12 lines]"
    IDLE = "작업 중\n────────────\n❯ \n────────────\n  ? for shortcuts"

    def screen(self, handle, preview=None, mode="custom"):
        """핸들별 화면 응답. 가짜 orca 의 show.mode.<핸들> + 공통 show.preview(custom). 시험이 한 핸들만 바꾸도록 같은 preview 는 한 시험에서 한 종류만 씀"""
        self.mode(f"show.mode.{handle}", mode)
        if preview is not None:
            self.mode("show.preview", preview)

    def sends(self):
        """[(핸들, 본문)] 전송 순서"""
        out = []
        try:
            with open(os.path.join(self.fo, "sends.log"), encoding="utf-8") as f:
                for i, ln in enumerate(f, 1):
                    with open(os.path.join(self.fo, f"send.{i}.txt"), encoding="utf-8") as g:
                        out.append((ln.split("\t")[2], g.read()))
        except FileNotFoundError:
            pass
        return out

    def go(self, *items):
        self.put(*items)
        res = self.run_intake()
        return res, intake.deliver(self.conf, NOW)

    def row(self, rid="C0001"):
        return next(r for r in ledger.read_rows(self.conf.ledger) if r["id"] == rid)

    def test_delivers_one_line_to_hub_terminal_and_marks_ledger(self):
        _, dv = self.go(msg("ui: 로그인 버튼\n색 바꿔줘", 1))
        self.assertEqual(dv["sent"], ["C0001"])
        self.assertEqual(self.sends(), [(HUB_H, "[접수 #C0001] 로그인 버튼 색 바꿔줘")])
        self.assertEqual(self.row()["state"], "진행")
        self.assertIn("ui 허브에 하달", self.row()["note"])

    def test_rerun_does_not_resend(self):
        self.go(msg("ui: a", 1))
        again = intake.deliver(self.conf, NOW)
        self.assertEqual((again["sent"], again["failed"]), ([], []))
        self.assertEqual(len(self.sends()), 1)

    def test_default_hub_main_is_not_delivered(self):
        _, dv = self.go(msg("머리말 없는 명령", 1))
        self.assertEqual((dv["sent"], dv["failed"]), ([], []))
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.row()["state"], "접수")

    def test_no_hub_terminal_marks_ledger_and_tells_main(self):
        self.terms()
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual((dv["sent"], dv["notified"]), ([], ["C0001"]))
        self.assertEqual(self.row()["state"], "보류")
        self.assertIn("전달 실패", self.row()["note"])
        (handle, text), = self.sends()
        self.assertEqual(handle, MAIN_H)
        self.assertIn("[접수 #C0001 하달 실패", text)
        self.assertIn("coord-ui-layer-hub 코디네이터 터미널 없음", text)
        self.assertIn("[접수 #C0001] a", text)

    def test_two_hub_terminals_is_not_guessed(self):
        self.terms(self.hub_term(), self.hub_term(handle="term_cccccccc-0000-4000-8000-000000000003"))
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["notified"], ["C0001"])
        self.assertEqual([h for h, _ in self.sends()], [MAIN_H])

    def test_dead_terminal_is_not_a_candidate(self):
        self.terms(self.hub_term(connected=False))
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["notified"], ["C0001"])

    def test_hub_without_row_in_hubs_table(self):
        _, dv = self.go(msg("vfx: 폭죽", 1))
        self.assertEqual(dv["notified"], ["C0001"])
        self.assertIn("허브 표에 vfx 행 없음", self.sends()[0][1])

    def test_list_failure_goes_to_main(self):
        self.mode("list.mode", "fail")
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["notified"], ["C0001"])
        self.assertIn("터미널 목록 조회 실패", self.sends()[0][1])
        self.assertEqual(self.row()["state"], "보류")

    def test_main_unreachable_keeps_failure_and_retries_only_notice(self):
        self.terms()
        self.mode(f"send.mode.{MAIN_H}", "fail")
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual((dv["failed"], dv["notified"]), (["C0001"], []))
        self.assertEqual(self.row()["state"], "보류")
        os.remove(os.path.join(self.fo, f"send.mode.{MAIN_H}"))
        self.terms(self.hub_term())  # 허브가 살아나도 이미 실패로 확정된 접수는 허브에 재전송하지 않음
        again = intake.deliver(self.conf, NOW)
        self.assertEqual((again["sent"], again["notified"]), ([], ["C0001"]))
        self.assertEqual([h for h, _ in self.sends()], [MAIN_H, MAIN_H])

    def test_ambiguous_send_is_not_resent(self):
        self.mode(f"send.mode.{HUB_H}", "ambig")
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["sent"], [])
        self.assertEqual(dv["notified"], ["C0001"])
        self.assertIn("도착 여부 불명", self.sends()[1][1])
        self.mode(f"send.mode.{HUB_H}", "ok")
        intake.deliver(self.conf, NOW)
        self.assertEqual([h for h, _ in self.sends()], [HUB_H, MAIN_H])

    def test_crash_between_marker_and_result_becomes_unknown_not_resent(self):
        self.put(msg("ui: a", 1))
        self.run_intake()
        with open(self.conf.sent, "w", encoding="utf-8") as f:
            json.dump({"C0001": {"st": "sending", "hub": "ui", "at": int(NOW)}}, f)
        dv = intake.deliver(self.conf, NOW)
        self.assertEqual(dv["notified"], ["C0001"])
        self.assertEqual([h for h, _ in self.sends()], [MAIN_H])
        self.assertIn("전송 중 중단", self.sends()[0][1])

    def test_old_entries_before_feature_are_legacy_on_first_run(self):
        self.put(msg("ui: 옛 명령", 1))
        intake.intake(self.conf, NOW - 7200)  # 두 시간 전에 접수된 기록
        dv = intake.deliver(self.conf, NOW)
        self.assertEqual((dv["sent"], dv["failed"]), ([], []))
        self.assertEqual(self.sends(), [])

    def test_unconfirmed_receipt_is_not_sent(self):
        """입력은 접수됐지만 turn_started 가 없는 영수증(시간 초과 관찰 포함)은 하달 성공이 아님: 보류 + 메인 알림, 재전송 없음"""
        self.mode(f"send.mode.{HUB_H}", "unsubmitted")
        self.screen(HUB_H, self.DRAFT)  # 화면에 입력 칸 글이 남아 제출 확인 불가
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual((dv["sent"], dv["notified"]), ([], ["C0001"]))
        self.assertEqual(self.row()["state"], "보류")
        self.assertIn("화면 제출을 확인하지 못함", self.sends()[1][1])
        intake.deliver(self.conf, NOW)
        self.assertEqual([h for h, _ in self.sends()], [HUB_H, MAIN_H])

    def test_screen_confirm_submitted_counts_as_sent(self):
        """turn_started 는 없고(Orca 가 Claude 턴 시작을 못 잡는 환경) 화면에서 입력 칸이 비어 제출이 확인되면 하달 성공"""
        self.mode(f"send.mode.{HUB_H}", "unsubmitted")
        self.screen(HUB_H, self.IDLE)
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual((dv["sent"], dv["notified"]), (["C0001"], []))
        self.assertEqual(self.row()["state"], "진행")
        self.assertEqual([h for h, _ in self.sends()], [HUB_H])

    def test_screen_confirm_draft_holds(self):
        self.mode(f"send.mode.{HUB_H}", "unsubmitted")
        self.screen(HUB_H, self.DRAFT)
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual((dv["sent"], dv["notified"]), ([], ["C0001"]))
        self.assertEqual(self.row()["state"], "보류")
        self.assertEqual([h for h, _ in self.sends()], [HUB_H, MAIN_H])

    def test_screen_confirm_read_failure_holds(self):
        self.mode(f"send.mode.{HUB_H}", "unsubmitted")
        self.screen(HUB_H, mode="unavail")
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual((dv["sent"], dv["notified"]), ([], ["C0001"]))
        self.assertEqual(self.row()["state"], "보류")

    def test_screen_confirm_other_terminal_holds(self):
        """확인 중 그 핸들이 다른 터미널(codex)로 바뀌었으면 제출 근거가 아님"""
        self.mode(f"send.mode.{HUB_H}", "unsubmitted")
        self.screen(HUB_H, mode="codex")
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["sent"], [])

    def test_receipt_without_input_accepted_is_not_screen_confirmed(self):
        self.mode(f"send.mode.{HUB_H}", "nostage")
        self.screen(HUB_H, self.IDLE)
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["sent"], [])

    def test_main_notice_screen_confirm_submitted(self):
        self.terms()
        self.mode(f"send.mode.{MAIN_H}", "unsubmitted")
        self.screen(MAIN_H, self.IDLE)
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["notified"], ["C0001"])
        self.assertEqual([h for h, _ in self.sends()], [MAIN_H])

    def test_confirm_off_keeps_receipt_only(self):
        self.mode(f"send.mode.{HUB_H}", "unsubmitted")
        self.screen(HUB_H, self.IDLE)
        old = intake.sp.CONFIRM
        intake.sp.CONFIRM = 0
        self.addCleanup(setattr, intake.sp, "CONFIRM", old)
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["sent"], [])

    def test_send_line_receipt_classes(self):
        def receipt(accepted=True, stages=("input_accepted",), obs="timed_out"):
            return lambda a, t: (0, {"ok": True, "result": {"send": {"accepted": accepted, "prompt": {"stages": list(stages), "observation": obs}}}})
        self.assertEqual(intake.send_line(receipt(), "h", "x"), "unconfirmed")
        self.assertEqual(intake.send_line(receipt(stages=("input_accepted", "turn_started"), obs="observed"), "h", "x"), "sent")
        self.assertEqual(intake.send_line(receipt(accepted=False), "h", "x"), "rejected")
        self.assertEqual(intake.send_line(lambda a, t: (0, {"ok": True, "result": {}}), "h", "x"), "unknown")
        self.assertEqual(intake.send_line(lambda a, t: (None, None), "h", "x"), "unknown")

    def test_unconfirmed_main_notice_is_not_retried(self):
        self.terms()
        self.mode(f"send.mode.{MAIN_H}", "unsubmitted")
        self.screen(MAIN_H, self.DRAFT)
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["notified"], ["C0001"])
        intake.deliver(self.conf, NOW)
        self.assertEqual([h for h, _ in self.sends()], [MAIN_H])

    def test_dry_run_never_calls_send(self):
        self.put(msg("ui: a", 1))
        self.run_intake()
        dv = intake.deliver(self.conf, NOW, dry=True)
        self.assertEqual((dv["planned"], dv["sent"]), (["C0001"], []))
        self.assertEqual(self.sends(), [])
        self.assertFalse(os.path.exists(self.conf.sent))
        self.assertEqual(self.row()["state"], "접수")
        real = intake.deliver(self.conf, NOW)  # dry 뒤 정상 실행은 한 번만 전송
        self.assertEqual(real["sent"], ["C0001"])
        self.assertEqual(len(self.sends()), 1)

    def test_dry_run_failure_path_does_not_notify_or_write(self):
        self.terms()
        self.put(msg("ui: a", 1))
        self.run_intake()
        dv = intake.deliver(self.conf, NOW, dry=True)
        self.assertEqual(dv["notified"], [])
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.row()["state"], "접수")

    def test_cli_dry_run_calls_no_send(self):
        self.conf.inbox = os.path.join(self.d, "slack-inbox.jsonl")
        self.conf.state, self.conf.routes_log = os.path.join(self.d, "intake-state.json"), os.path.join(self.d, "intake-routes.jsonl")  # CLI 기본 파일명
        self.put(msg("ui: a", 1))
        self.run_intake()
        env = {**os.environ, "LRM_HUB_DIR": self.d, "LRM_INTAKE_ROUTES": TABLE, "LRM_LEDGER": self.conf.ledger, "LRM_ORCA": FAKE_ORCA,
               "LRM_HUBS_TABLE": self.conf.hubs_table, "LRM_MAIN_ENV": self.conf.main_env, "LRM_HUB_WS_ROOT": "/ws"}
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "intake.py"), "--once", "--dry-run", "--json"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["deliver"]["planned"], ["C0001"])
        self.assertEqual(self.sends(), [])

    def test_worker_only_hub_is_not_delivered(self):
        """같은 워크트리에 붙은 리뷰 워커(제목·pane 제목에 worker)는 코디네이터가 아님: 하달 안 하고 보류 + 메인 알림"""
        self.terms(self.hub_term(title="review worker", paneTitle="WORKER"))
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual((dv["sent"], dv["notified"]), ([], ["C0001"]))
        self.assertEqual([h for h, _ in self.sends()], [MAIN_H])
        self.assertIn("워커 터미널만 있어", self.sends()[0][1])

    def test_coordinator_chosen_over_worker_in_same_worktree(self):
        worker = self.hub_term(handle="term_dddddddd-0000-4000-8000-000000000004", title="review worker", paneTitle="WORKER")
        self.terms(worker, self.hub_term())
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["sent"], ["C0001"])
        self.assertEqual([h for h, _ in self.sends()], [HUB_H])

    def test_same_suffix_other_root_is_not_the_hub(self):
        """폴더 접미사만 같은 다른 워크스페이스의 터미널은 후보가 아님(hubs.tsv 의 전체 경로 비교)"""
        self.terms(self.hub_term(worktreePath="/elsewhere/coord-ui-layer-hub"))
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["notified"], ["C0001"])
        self.assertEqual([h for h, _ in self.sends()], [MAIN_H])

    def test_hub_path_uses_ws_root_column(self):
        with open(self.conf.hubs_table, "w", encoding="utf-8") as f:
            f.write("config\t설정\t~/.config/lrm-hubs/roles/role-config.txt\t/custom/root\t-\n")
        self.assertEqual(intake.hub_path(self.conf, "config"), "/custom/root/coord-config-hub")
        self.assertIsNone(intake.hub_path(self.conf, "ui"))

    def test_main_handle_of_other_use_gets_no_send(self):
        """main.env 의 핸들이 메인 체크아웃 터미널이 아니면(다른 용도) 보내지 않고 재탐색. 후보가 없으면 실패로 남김"""
        self.terms()
        self.mode(f"show.mode.{MAIN_H}", "other_wt")
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual((dv["failed"], dv["notified"]), (["C0001"], []))
        self.assertEqual(self.sends(), [])
        self.assertIn(("show", MAIN_H), [(ln.split()[0], ln.split()[2]) for ln in open(os.path.join(self.fo, "calls.log")) if ln.startswith("show")])

    def test_main_handle_not_claude_gets_no_send(self):
        self.terms()
        self.mode(f"show.mode.{MAIN_H}", "codex")
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["notified"], [])
        self.assertEqual(self.sends(), [])

    def test_main_link_check_failure_does_not_send_or_rediscover(self):
        self.terms()
        self.mode(f"show.mode.{MAIN_H}", "unavail")
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["failed"], ["C0001"])
        self.assertEqual(self.sends(), [])
        self.assertFalse([ln for ln in open(os.path.join(self.fo, "calls.log")) if ln.startswith("list")][1:])  # 허브 탐색 list 1회뿐

    def test_main_handle_gone_rediscovers_and_sends(self):
        self.terms(self.hub_term(), {**self.hub_term(handle="term_eeeeeeee-0000-4000-8000-000000000005", worktreePath="/main/checkout"), "title": "MAIN-HANDOFF.md 메인"})
        self.mode(f"show.mode.{MAIN_H}", "gone")
        self.mode(f"send.mode.{HUB_H}", "unsubmitted")
        self.screen(HUB_H, self.DRAFT)
        _, dv = self.go(msg("ui: a", 1))
        self.assertEqual(dv["notified"], ["C0001"])
        self.assertEqual([h for h, _ in self.sends()], [HUB_H, "term_eeeeeeee-0000-4000-8000-000000000005"])

    def test_cli_reports_delivery(self):
        self.conf.inbox = os.path.join(self.d, "slack-inbox.jsonl")  # 프로세스 실행은 기본 파일명을 읽음
        self.put(msg("ui: a", 1))
        env = {**os.environ, "LRM_HUB_DIR": self.d, "LRM_INTAKE_ROUTES": TABLE, "LRM_LEDGER": self.conf.ledger, "LRM_ORCA": FAKE_ORCA,
               "LRM_HUBS_TABLE": self.conf.hubs_table, "LRM_MAIN_ENV": self.conf.main_env, "LRM_HUB_WS_ROOT": "/ws",
               "LRM_PUSH_MAIN": "/main/checkout", "LRM_PUSH_PANE": ""}
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "intake.py"), "--once", "--json"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["deliver"]["sent"], ["C0001"])


if __name__ == "__main__":
    unittest.main()
