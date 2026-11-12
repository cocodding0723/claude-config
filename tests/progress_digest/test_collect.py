"""progress_collect.py 시험. 임시 폴더·가짜 gh 만 씀(Slack·라이브 파일 없음)."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECT = os.path.join(HERE, "..", "..", "hubs", "scripts", "progress_collect.py")
HEADER = "id\t접수\t요청(원문 요약)\t상태\t담당\t진행·증거\t갱신\n"


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        d = self.t.name
        self.status = os.path.join(d, "status")
        os.makedirs(self.status)
        self.snap = os.path.join(d, "progress.json")
        self.ledger = os.path.join(d, "ledger.tsv")
        with open(self.ledger, "w", encoding="utf-8") as f:
            f.write(HEADER + "F1\t11-12\t로그인\t진행\t개인\t구현 중\t2026-11-12 10:00\n")
        self.gh_out = os.path.join(d, "gh.json")
        with open(self.gh_out, "w") as f:
            json.dump([{"number": 3, "title": "기능", "url": "u", "reviewDecision": "APPROVED", "mergeable": "MERGEABLE"}], f)
        self.gh = os.path.join(d, "gh")
        with open(self.gh, "w") as f:
            f.write(f'#!/bin/sh\necho x >> "{d}/gh.calls"\n[ -f "{d}/gh.fail" ] && exit 1\ncat "{self.gh_out}"\n')
        os.chmod(self.gh, os.stat(self.gh).st_mode | stat.S_IXUSR)

    def hub(self, slug, hours_old, tasks):
        at = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat(timespec="seconds")
        with open(os.path.join(self.status, slug + ".json"), "w") as f:
            json.dump({"slug": slug, "updatedAt": at, "tasks": tasks, "blockers": []}, f)

    def run_once(self, *args, interval="0"):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SLACK_", "LRM_"))}
        env.update(HOME=self.t.name, LRM_HUB_DIR=self.t.name, LRM_PROGRESS=self.snap, LRM_LEDGER=self.ledger,
                   LRM_HUB_STATUS_DIR=self.status, LRM_DIGEST_GH=self.gh, LRM_PROGRESS_PR_INTERVAL=interval)
        return subprocess.run([sys.executable, "-I", COLLECT, *args], env=env, capture_output=True, text=True, timeout=60)

    def load(self):
        with open(self.snap, encoding="utf-8") as f:
            return json.load(f)

    def test_snapshot_written_with_sources_and_counts(self):
        self.hub("ui", 1, ["버튼 구현"])
        self.assertEqual(self.run_once("--once").returncode, 0)
        d = self.load()
        self.assertEqual(d["counts"], {"doing": 2, "wait": 1, "block": 0})
        for name in ("ledger", "hubs", "prs", "decisions", "workers"):
            self.assertTrue({"ok", "err", "at", "tried", "stale", "value"} <= set(d["sources"][name]), name)
        self.assertTrue(d["sources"]["decisions"]["off"])
        self.assertEqual({i["group"] for i in d["items"]}, {"장부", "ui", "PR"})
        self.assertEqual([f for f in os.listdir(self.t.name) if f.startswith(".progress-")], [])  # 임시 파일 남지 않음

    def test_dry_run_writes_nothing(self):
        r = self.run_once("--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.snap))
        self.assertEqual(json.loads(r.stdout)["counts"]["doing"], 1)

    def test_one_source_fails_others_kept(self):
        self.assertEqual(self.run_once("--once").returncode, 0)
        open(os.path.join(self.t.name, "gh.fail"), "w").close()
        self.hub("ui", 1, ["새 작업"])
        self.assertEqual(self.run_once("--once").returncode, 0)
        d = self.load()
        prs = d["sources"]["prs"]
        self.assertFalse(prs["ok"])
        self.assertTrue(prs["stale"])
        self.assertEqual(len(prs["value"]), 1)  # 이전 PR 값 유지
        self.assertTrue(any(i["key"].startswith("pr:") for i in d["items"]))
        self.assertTrue(any("ui" in i["key"] for i in d["items"]))  # 나머지 원료는 새 값
        self.assertTrue(any(n.startswith("PR 조회 실패") for n in d["notes"]))

    def test_pr_not_requeried_within_interval(self):
        self.run_once("--once", interval="300")
        self.run_once("--once", interval="300")
        with open(os.path.join(self.t.name, "gh.calls")) as f:
            self.assertEqual(len(f.read().split()), 1)

    def test_stale_hub_tagged(self):
        self.hub("old", 30, ["오래된 작업"])
        d = json.loads(self.run_once("--dry-run", "--no-pr").stdout)
        self.assertTrue(any("[낡음]" in i["title"] for i in d["items"] if i["group"] == "old"))

    def test_corrupt_previous_snapshot_recovers(self):
        with open(self.snap, "w") as f:
            f.write("{broken")
        self.assertEqual(self.run_once("--once", "--no-pr").returncode, 0)
        self.assertEqual(self.load()["version"], 1)


if __name__ == "__main__":
    unittest.main()
