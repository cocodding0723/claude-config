"""progress_digest.py 시험. 가짜 Slack API(127.0.0.1)·가짜 전송 스크립트·가짜 gh 만 씀. 외부 주소 요청은 막힘."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

COLLECT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts", "progress_collect.py")
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts", "progress_digest.py")
HEADER = "id\t접수\t요청(원문 요약)\t상태\t담당\t진행·증거\t갱신\n"


class FakeSlack(BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeSlack.calls.append((self.path.rsplit("/", 1)[-1], body))
        n = len(FakeSlack.calls)
        out = {"ok": True, "item": {"id": f"Rec{n:04d}"}} if self.path.endswith("create") else {"ok": True}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class DigestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), FakeSlack)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FakeSlack.calls = []
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        d = self.t.name
        self.hub, self.status = os.path.join(d, "hub"), os.path.join(d, "status")
        os.makedirs(self.hub)
        os.makedirs(self.status)
        self.ledger = os.path.join(self.hub, "ledger.tsv")
        self.dmlog = os.path.join(d, "dm.log")
        self.notify = self.script("notify", f'#!/bin/sh\nprintf "CALL %s\\n" "$*" >> "{self.dmlog}"\n')
        self.gh_out = os.path.join(d, "gh.json")
        self.gh = self.script("gh", f'#!/bin/sh\n[ -f "{d}/gh.fail" ] && {{ echo "api down" >&2; exit 1; }}\ncat "{self.gh_out}"\n')
        self.write_ledger([("F1", "진행", "로그인 개선", "구현 중"), ("F2", "진행", "패널 정리", "병합 대기"), ("F3", "보류", "CI 불안정", "외부 대기")])
        self.write_gh([])

    def script(self, name, text):
        p = os.path.join(self.t.name, name)
        with open(p, "w") as f:
            f.write(text)
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        return p

    def write_ledger(self, rows):
        with open(self.ledger, "w", encoding="utf-8") as f:
            f.write(HEADER + "".join(f"{i}\t11-12\t{req}\t{st}\t개인\t{note}\t2026-11-12 10:00\n" for i, st, req, note in rows))

    def write_gh(self, prs):
        with open(self.gh_out, "w") as f:
            json.dump(prs, f)

    def put_hub(self, slug, hours_old=1, tasks=(), blockers=()):
        from datetime import datetime, timedelta, timezone
        at = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat(timespec="seconds")
        with open(os.path.join(self.status, slug + ".json"), "w") as f:
            json.dump({"slug": slug, "updatedAt": at, "tasks": list(tasks), "blockers": list(blockers)}, f)

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SLACK_", "LRM_"))}
        env.update(HOME=self.t.name, LRM_HUB_DIR=self.hub, LRM_LEDGER=self.ledger, LRM_HUB_STATUS_DIR=self.status,
                   LRM_DIGEST_NOTIFY=self.notify, LRM_ORCA=os.path.join(self.t.name, "no-orca"), LRM_PROGRESS=os.path.join(self.hub, "progress.json"), LRM_PROGRESS_PR_INTERVAL="0", LRM_DIGEST_GH=self.gh, LRM_DIGEST_LOCAL_ONLY="1",
                   SLACK_API_BASE=f"http://127.0.0.1:{self.srv.server_port}/api", SLACK_BOT_TOKEN="fake-test-token")
        env.update(extra)
        return env

    def run_digest(self, *args, **extra):
        return subprocess.run([sys.executable, "-I", SCRIPT, *args], env=self.env(**extra), capture_output=True, text=True, timeout=60)

    def dms(self):
        return open(self.dmlog).read().split("CALL ")[1:] if os.path.exists(self.dmlog) else []

    def test_three_buckets(self):
        self.put_hub("ui", tasks=["버튼 구현", "카드 병합: 사용자 실행 대기"], blockers=["키 발급 막힘"])
        self.write_gh([{"number": 7, "title": "추가", "url": "u", "reviewDecision": "CHANGES_REQUESTED", "mergeable": "MERGEABLE"}])
        r = self.run_digest("--dry-run", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        by = {i["key"]: i["bucket"] for i in d["items"]}
        self.assertEqual(by["ledger:F1"], "doing")
        self.assertEqual(by["ledger:F2"], "wait")
        self.assertEqual(by["ledger:F3"], "block")
        self.assertEqual(sorted(v for k, v in by.items() if k.startswith("hub:ui")), ["block", "doing", "wait"])
        self.assertEqual(by["pr:#7"], "block")
        self.assertEqual(FakeSlack.calls, [])
        self.assertEqual(self.dms(), [])
        self.assertFalse(os.path.exists(os.path.join(self.hub, "progress-digest.state.json")))

    def test_unchanged_sends_nothing(self):
        self.assertEqual(self.run_digest().returncode, 0)
        n_calls, n_dm = len(FakeSlack.calls), len(self.dms())
        self.assertEqual(n_calls, 3)
        self.assertEqual(n_dm, 1)
        r = self.run_digest()
        self.assertEqual(r.returncode, 0)
        self.assertIn("변화 없음", r.stdout)
        self.assertEqual((len(FakeSlack.calls), len(self.dms())), (n_calls, n_dm))

    def test_change_updates_list_and_one_dm(self):
        self.run_digest()
        FakeSlack.calls.clear()
        os.remove(self.dmlog)
        self.write_ledger([("F1", "완료", "로그인 개선", "증거: x"), ("F2", "진행", "패널 정리", "병합 대기"), ("F3", "보류", "CI 불안정", "외부 대기"), ("F4", "진행", "새 일", "")])
        self.assertEqual(self.run_digest().returncode, 0)
        ops = [(m, json.dumps(b, ensure_ascii=False)) for m, b in FakeSlack.calls]
        self.assertEqual(len(ops), 2)
        self.assertTrue(any(m == "slackLists.items.create" and "새 일" in b for m, b in ops))
        self.assertTrue(any(m == "slackLists.items.update" and '"checkbox": true' in b for m, b in ops))
        self.assertEqual(len(self.dms()), 1)
        self.assertIn("진행 중 1 · 답 대기 1 · 막힘 1", self.dms()[0])

    def test_dm_names_capped_at_three(self):
        self.write_ledger([(f"F{i}", "진행", f"일{i}", "답 대기") for i in range(1, 6)])
        self.run_digest()
        line = [l for l in open(self.dmlog).read().split("\n") if "답 대기:" in l][0]
        self.assertIn("외 2", line)
        self.assertNotIn("일4", line)

    def test_pr_failure_continues_and_keeps_old_pr_rows(self):
        self.write_gh([{"number": 9, "title": "기능", "url": "u", "reviewDecision": "", "mergeable": "MERGEABLE"}])
        collect = lambda: subprocess.run([sys.executable, "-I", COLLECT, "--once"], env=self.env(), capture_output=True, text=True, timeout=60)  # noqa: E731
        self.assertEqual(collect().returncode, 0)
        self.run_digest()
        FakeSlack.calls.clear()
        open(os.path.join(self.t.name, "gh.fail"), "w").close()
        self.assertEqual(collect().returncode, 0)
        r = self.run_digest()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PR 조회 실패", r.stdout)
        self.assertEqual(FakeSlack.calls, [])  # 직전 PR 행을 완료로 오인하지 않음

    def test_reads_snapshot_without_recollecting(self):
        subprocess.run([sys.executable, "-I", COLLECT, "--once", "--no-pr"], env=self.env(), check=True, capture_output=True)
        self.write_ledger([])  # 스냅샷 뒤에 장부가 바뀌어도 스냅샷 기준
        d = json.loads(self.run_digest("--dry-run", "--json", "--no-pr").stdout)
        self.assertEqual(d["counts"], {"doing": 1, "wait": 1, "block": 1})

    def test_stale_hub_marked(self):
        self.put_hub("old", hours_old=30, tasks=["오래된 작업"])
        self.put_hub("new", hours_old=1, tasks=["새 작업"])
        d = json.loads(self.run_digest("--dry-run", "--json", "--no-pr").stdout)
        titles = {i["title"] for i in d["items"]}
        self.assertTrue(any("[낡음]" in t and "오래된" in t for t in titles))
        self.assertFalse(any("[낡음]" in t and "새 작업" in t for t in titles))

    def test_manual_rows_untouched(self):
        self.run_digest()
        st = json.load(open(os.path.join(self.hub, "progress-digest.state.json")))
        self.assertEqual(set(st["items"]), {"ledger:F1", "ledger:F2", "ledger:F3"})
        self.write_ledger([])  # 모두 끝남 → 이 도구가 만든 행만 완료 처리
        FakeSlack.calls.clear()
        self.run_digest()
        rows = {b["cells"][0]["row_id"] for m, b in FakeSlack.calls}
        self.assertEqual(rows, {v["record"] for v in st["items"].values()})
        self.assertFalse(any(m.endswith("delete") for m, _ in FakeSlack.calls))

    def test_external_api_blocked(self):
        r = self.run_digest(SLACK_API_BASE="https://slack.com/api")
        self.assertEqual(r.returncode, 2)
        self.assertIn("로컬이 아님", r.stderr)
        self.assertEqual(FakeSlack.calls, [])

    def test_dm_failure_retries_without_duplicating_list(self):
        self.notify = self.script("notify", "#!/bin/sh\nexit 1\n")
        self.assertEqual(self.run_digest().returncode, 1)
        n = len(FakeSlack.calls)
        self.notify = self.script("notify", f'#!/bin/sh\nprintf "CALL %s\\n" "$*" >> "{self.dmlog}"\n')
        self.assertEqual(self.run_digest().returncode, 0)
        self.assertEqual(len(FakeSlack.calls), n)
        self.assertEqual(len(self.dms()), 1)

    # --- Codex 리뷰 수정 회귀 ---
    def collect(self, *args):
        return subprocess.run([sys.executable, "-I", COLLECT, "--once", *args], env=self.env(), capture_output=True, text=True, timeout=60)

    def test_corrupt_hub_does_not_complete_its_rows(self):
        self.put_hub("ui", tasks=["버튼 구현"])
        self.collect("--no-pr")
        self.run_digest("--no-pr")
        FakeSlack.calls.clear()
        with open(os.path.join(self.status, "ui.json"), "w") as f:
            f.write("{broken")
        self.collect("--no-pr")
        r = self.run_digest("--no-pr")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse([c for c in FakeSlack.calls if c[1].get("cells") and "checkbox" in json.dumps(c[1])])

    def test_recollect_keeps_unresolved_decision_rows(self):
        with open(os.path.join(self.hub, "slack-sent.jsonl"), "w") as f:
            f.write(json.dumps({"ts": "1.1", "title": "결정 알림 시각", "at": 1}) + "\n")
        self.run_digest("--no-pr")  # 스냅샷 없음 → 직접 수집
        self.assertTrue(any("결정 알림 시각" in json.dumps(b, ensure_ascii=False) for _, b in FakeSlack.calls))
        FakeSlack.calls.clear()
        with open(os.path.join(self.hub, "progress.json"), "w") as f:  # 낡은 스냅샷(결정 원료 없음)
            json.dump({"version": 1, "updatedAt": 1, "items": [], "sources": {}}, f)
        self.assertEqual(self.run_digest("--no-pr").returncode, 0)
        self.assertEqual([c for c in FakeSlack.calls if "checkbox" in json.dumps(c[1])], [])  # 완료 처리 없음

    def test_link_only_change_updates_row(self):
        self.write_gh([{"number": 5, "title": "기능", "url": "https://x/1", "reviewDecision": "", "mergeable": "MERGEABLE"}])
        self.run_digest()
        FakeSlack.calls.clear()
        self.write_gh([{"number": 5, "title": "기능", "url": "https://x/2", "reviewDecision": "", "mergeable": "MERGEABLE"}])
        self.run_digest()
        self.assertEqual([m for m, _ in FakeSlack.calls], ["slackLists.items.update"])
        self.assertIn("https://x/2", json.dumps(FakeSlack.calls[0][1]))

    def test_rows_skipped_by_no_list_are_created_on_next_normal_run(self):
        self.assertEqual(self.run_digest("--no-list").returncode, 0)
        self.assertEqual(FakeSlack.calls, [])
        self.assertEqual(self.run_digest().returncode, 0)
        self.assertEqual(len(FakeSlack.calls), 3)  # 장부 3행 생성
        self.assertEqual(len(self.dms()), 1)

    def test_dm_shows_child_counts_and_hash_ignores_small_changes(self):
        def snapshot(children, active, warn):
            self.run_digest("--no-pr", "--dry-run")  # 환경 점검용(예외 없음)
            with open(os.path.join(self.hub, "progress.json"), "w") as f:
                json.dump({"version": 1, "updatedAt": __import__("time").time(), "counts": {}, "notes": [], "sources": {},
                           "groups": {"config": {"children": children, "active": active, "warn": warn}},
                           "items": [{"key": "ledger:F1", "bucket": "doing", "title": "F1 로그인", "group": "장부", "link": ""}]}, f)
        snapshot(3, 1, False)
        self.run_digest("--no-pr")
        self.assertIn("config 하위 3개(활동 1)", self.dms()[0])
        os.remove(self.dmlog)
        snapshot(4, 2, False)  # 경고 아님: 수만 변함 → 전송 없음
        self.run_digest("--no-pr")
        self.assertEqual(self.dms(), [])
        snapshot(12, 2, True)  # 경고 진입 → 전송
        self.run_digest("--no-pr")
        self.assertIn("config 하위 12개(활동 2) ⚠️", self.dms()[0])


if __name__ == "__main__":
    unittest.main()
