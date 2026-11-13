"""막힘 근거: 근거 표기 파싱(hub_status.py) + 수집기 자동 제외(progress_collect.py). 가짜 gh·임시 폴더만 씀(라이브 Slack·상태 파일 없음)."""
import json
import os
import stat
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

from test_collect import Base

HS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts", "hub_status.py")
PR = "Basakansoft/LoveRevolutionMaker#746"


class ParseTest(unittest.TestCase):
    def write(self, *blockers):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            args = [a for b in blockers for a in ("--blocker", b)]
            r = subprocess.run([sys.executable, "-I", HS, "write", "--slug", "ui", *args], env={**os.environ, "LRM_HUB_STATUS_DIR": d}, capture_output=True, text=True)
            data = None
            if r.returncode == 0:
                with open(os.path.join(d, "ui.json"), encoding="utf-8") as f:
                    data = json.load(f)
            return r, data

    def test_parse_refs(self):
        r, d = self.write(f"서버 병합 대기 [pr:{PR}] [decision:D-CFGAPPLY]", "티켓 대기 [ticket:LRM-436]", "[pr:#12] 단축형")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(d["blockers"][0], {"text": "서버 병합 대기", "refs": [{"kind": "pr", "id": PR}, {"kind": "decision", "id": "D-CFGAPPLY"}]})
        self.assertEqual(d["blockers"][1]["refs"], [{"kind": "ticket", "id": "LRM-436"}])
        self.assertEqual(d["blockers"][2], {"text": "단축형", "refs": [{"kind": "pr", "id": "#12"}]})

    def test_plain_blocker_stays_string(self):
        r, d = self.write("키 발급 막힘")
        self.assertEqual(d["blockers"], ["키 발급 막힘"])

    def test_bad_format_rejected(self):
        for bad in ("x [pr:746]", "x [decision:cfg-apply]", "x [ticket:lrm-1]", "x [foo:bar]", "[pr:#1]"):
            r, d = self.write(bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIsNone(d)
            self.assertIn("ERROR", r.stderr)


class EvidenceTest(Base):
    def setUp(self):
        super().setUp()
        d = self.t.name
        self.gh = os.path.join(d, "gh")
        with open(self.gh, "w") as f:  # pr view N → gh-pr-N.json (없으면 실패). 그 밖(pr list)은 빈 목록
            f.write(f'#!/bin/sh\necho "$@" >> "{d}/gh.calls"\nif [ "$1 $2" = "pr view" ]; then cat "{d}/gh-pr-$3.json" 2>/dev/null || exit 1; exit 0; fi\necho "[]"\n')
        os.chmod(self.gh, os.stat(self.gh).st_mode | stat.S_IXUSR)

    def pr_state(self, num, state):
        with open(os.path.join(self.t.name, f"gh-pr-{num}.json"), "w") as f:
            json.dump({"state": state, "headRefName": "fix/x"}, f)

    def put(self, blockers, hours_old=1):
        at = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat(timespec="seconds")
        with open(os.path.join(self.status, "ui.json"), "w", encoding="utf-8") as f:
            json.dump({"slug": "ui", "updatedAt": at, "tasks": [], "blockers": blockers}, f)

    def collect(self, **extra):
        r = self.run_once("--once", "--repo", "Basakansoft/LoveRevolutionMaker", **extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.load()
        return d, [i for i in d["items"] if i["key"].startswith("hub:ui:b:")]

    def blocker(self, text, *refs):
        return {"text": text, "refs": [{"kind": k, "id": i} for k, i in refs]}

    def test_merged_pr_excluded_and_recorded(self):
        self.pr_state(746, "MERGED")
        self.put([self.blocker("병합 대기", ("pr", PR))])
        d, items = self.collect()
        self.assertEqual(items, [])
        self.assertEqual(d["resolved"][0]["text"], "병합 대기")
        self.assertEqual(d["resolved"][0]["refs"], [{"kind": "pr", "id": PR}])

    def test_open_pr_kept(self):
        self.pr_state(746, "OPEN")
        self.put([self.blocker("병합 대기", ("pr", PR))])
        d, items = self.collect()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["evidence"], "open")
        self.assertEqual(d["resolved"], [])

    def test_gh_failure_keeps_blocker(self):
        self.put([self.blocker("병합 대기", ("pr", PR))])  # gh-pr-746.json 없음 → gh 실패
        d, items = self.collect()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["evidence"], "unknown")

    def test_pr_result_cached(self):
        self.pr_state(746, "OPEN")
        self.put([self.blocker("병합 대기", ("pr", PR))])
        self.collect()
        def calls():
            with open(os.path.join(self.t.name, "gh.calls")) as f:
                return sum(1 for ln in f if ln.startswith("pr view"))
        n = calls()
        self.collect()
        self.assertEqual(calls(), n)  # 10분 캐시: 두 번째 회차는 gh 를 다시 부르지 않음

    def test_answered_decision_excluded(self):
        with open(os.path.join(self.t.name, "slack-sent.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "1.1", "decision_id": "D-CFGAPPLY", "form_kind": "decide", "options": ["A", "B"], "tag": "ui"}) + "\n")
        with open(os.path.join(self.t.name, "slack-inbox.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "reaction", "parent_ts": "1.1", "kind": "decide", "decision_id": "D-CFGAPPLY", "action": "approve", "pick": 1}) + "\n")
        self.put([self.blocker("설정 답 대기", ("decision", "D-CFGAPPLY")), self.blocker("다른 답 대기", ("decision", "D-OTHER"))])
        d, items = self.collect()
        self.assertEqual([i["title"] for i in items], ["ui: 다른 답 대기"])  # 기록 없는 결정은 막힘 유지
        self.assertEqual(d["resolved"][0]["refs"], [{"kind": "decision", "id": "D-CFGAPPLY"}])

    def test_legacy_string_blocker(self):
        self.put(["키 발급 막힘"])
        d, items = self.collect()
        self.assertEqual([i["title"] for i in items], ["ui: 키 발급 막힘"])
        self.assertNotIn("evidence", items[0])

    def test_no_evidence_old_marked_not_deleted(self):
        self.put(["키 발급 막힘"], hours_old=25)
        d, items = self.collect()
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["title"].startswith("[근거 없음·오래됨] "))

    def test_fresh_no_evidence_unmarked(self):
        self.put(["키 발급 막힘"], hours_old=23)
        d, items = self.collect()
        self.assertNotIn("근거 없음", items[0]["title"])

    def test_ticket_only_kept(self):
        self.put([self.blocker("티켓 대기", ("ticket", "LRM-436"))])
        d, items = self.collect()
        self.assertEqual(len(items), 1)
        self.assertNotIn("evidence", items[0])


if __name__ == "__main__":
    unittest.main()
