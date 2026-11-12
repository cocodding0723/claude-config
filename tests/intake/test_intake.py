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
        e.update(LRM_HUB_DIR=self.d, LRM_INTAKE_ROUTES=TABLE)
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
        """복사 설치본: 설치 폴더에는 intake.py·ledger.py 만 있어도 -I 로 import 되어야 함(links.tsv 등록 누락 방지)"""
        inst = os.path.join(self.d, "installed")
        os.mkdir(inst)
        for n in ("intake.py", "ledger.py"):
            shutil.copy(os.path.join(SCRIPTS, n), os.path.join(inst, n))
        self.put(msg("a", 1), msg("ui: b", 2))
        r = subprocess.run([sys.executable, "-I", os.path.join(inst, "intake.py"), "--once", "--json"], cwd=self.d, env=self.env(), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([n["hub"] for n in json.loads(r.stdout)["new"]], ["main", "ui"])

    def test_help_and_exit_codes(self):
        script = os.path.join(SCRIPTS, "intake.py")
        self.assertEqual(subprocess.run([sys.executable, script, "--help"], capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run([sys.executable, script], capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
