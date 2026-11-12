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


class Base(unittest.TestCase):
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

    def run_once(self, *args, interval="0", **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SLACK_", "LRM_", "PANEL_"))}
        env.update(HOME=self.t.name, LRM_HUB_DIR=self.t.name, LRM_PROGRESS=self.snap, LRM_LEDGER=self.ledger,
                   LRM_HUB_STATUS_DIR=self.status, LRM_DIGEST_GH=self.gh, LRM_PROGRESS_PR_INTERVAL=interval, LRM_ORCA=os.path.join(self.t.name, "no-orca"))
        env.update(extra)
        return subprocess.run([sys.executable, "-I", COLLECT, *args], env=env, capture_output=True, text=True, timeout=60)

    def load(self):
        with open(self.snap, encoding="utf-8") as f:
            return json.load(f)


class CollectTest(Base):
    def test_snapshot_written_with_sources_and_counts(self):
        self.hub("ui", 1, ["버튼 구현"])
        self.assertEqual(self.run_once("--once").returncode, 0)
        d = self.load()
        self.assertEqual(d["counts"], {"doing": 2, "wait": 1, "block": 0})
        for name in ("ledger", "hubs", "prs", "decisions", "workers"):
            self.assertTrue({"ok", "err", "at", "tried", "stale", "value"} <= set(d["sources"][name]), name)
        self.assertTrue(d["sources"]["decisions"]["ok"])  # 직접 조회(끄지 않음)
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

    def gh_args(self):
        """가짜 gh 가 받은 --repo 값 목록(호출 순서). 호출마다 인자를 기록하는 gh 로 바꿔 씀"""
        log = os.path.join(self.t.name, "gh.args")
        with open(self.gh, "w") as f:
            f.write(f'#!/bin/sh\necho "$@" >> "{log}"\ncat "{self.gh_out}"\n')
        return log

    def repos_called(self, log):
        with open(log, encoding="utf-8") as f:
            return [ln.split("--repo ")[1].split()[0] if "--repo " in ln else None for ln in f]

    def test_pr_repos_fall_back_to_panel_env(self):
        # launchd 처럼 저장소 밖에서 돌 때: LRM_DIGEST_REPOS 가 비면 작업 폴더 panel.env 의 PANEL_GH_REPOS 를 씀
        log = self.gh_args()
        with open(os.path.join(self.t.name, "panel.env"), "w") as f:
            f.write('PANEL_RECENT=5\nPANEL_GH_REPOS="Org/One, Org/Two"\n')
        self.assertEqual(self.run_once("--once").returncode, 0)
        self.assertEqual(self.repos_called(log), ["Org/One", "Org/Two"])
        self.assertIn("pr:Org/One#3", [i["key"] for i in self.load()["items"]])

    def test_pr_repos_env_wins_over_panel_env(self):
        log = self.gh_args()
        with open(os.path.join(self.t.name, "panel.env"), "w") as f:
            f.write("PANEL_GH_REPOS=Org/Panel\n")
        self.assertEqual(self.run_once("--once", LRM_DIGEST_REPOS="Org/Env").returncode, 0)
        self.assertEqual(self.repos_called(log), ["Org/Env"])
        os.remove(log)
        self.assertEqual(self.run_once("--once", PANEL_GH_REPOS="Org/PanelEnv").returncode, 0)  # 환경 변수 PANEL_GH_REPOS 도 파일보다 우선
        self.assertEqual(self.repos_called(log), ["Org/PanelEnv"])

    def test_broken_panel_env_does_not_stop_collection(self):
        # 디코딩 불가 panel.env: --once 는 경고 후 파일 무시, --no-pr·--repo 는 파일을 읽지도 않음
        log = self.gh_args()
        with open(os.path.join(self.t.name, "panel.env"), "wb") as f:
            f.write(b"PANEL_GH_REPOS=Org/One\n#\xff\n")
        r = self.run_once("--once")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("panel.env 읽기 실패", r.stderr)
        self.assertEqual(self.repos_called(log), [None])
        r = self.run_once("--dry-run", "--no-pr")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("panel.env", r.stderr)
        os.remove(log)
        r = self.run_once("--dry-run", "--repo", "Org/Explicit")
        self.assertEqual((r.returncode, self.repos_called(log)), (0, ["Org/Explicit"]))
        self.assertNotIn("panel.env", r.stderr)

    def test_pr_repos_default_to_current_repo_without_any_setting(self):
        log = self.gh_args()
        self.assertEqual(self.run_once("--once").returncode, 0)
        self.assertEqual(self.repos_called(log), [None])

    def test_stale_hub_tagged(self):
        self.hub("old", 30, ["오래된 작업"])
        d = json.loads(self.run_once("--dry-run", "--no-pr").stdout)
        self.assertTrue(any("[낡음]" in i["title"] for i in d["items"] if i["group"] == "old"))

    def test_corrupt_previous_snapshot_recovers(self):
        with open(self.snap, "w") as f:
            f.write("{broken")
        self.assertEqual(self.run_once("--once", "--no-pr").returncode, 0)
        self.assertEqual(self.load()["version"], 1)


class ReviewFixTest(Base):
    """Codex 리뷰 수정 회귀"""

    def test_corrupt_hub_file_keeps_previous_items(self):
        self.hub("ui", 1, ["버튼 구현"])
        self.hub("db", 1, ["스키마"])
        self.run_once("--once", "--no-pr")
        with open(os.path.join(self.status, "ui.json"), "w") as f:
            f.write("{broken")
        self.assertEqual(self.run_once("--once", "--no-pr").returncode, 0)
        d = self.load()
        self.assertEqual(sorted(i["title"] for i in d["items"] if i["group"] == "ui"), ["ui: 버튼 구현"])
        hubs = d["sources"]["hubs"]
        self.assertFalse(hubs["ok"])
        self.assertTrue(hubs["stale"])
        self.assertIn("ui", hubs["err"])
        self.assertTrue(any("db" in i["title"] for i in d["items"]))  # 다른 허브는 정상

    def test_structurally_broken_snapshot_recovers(self):
        for bad in ('{"items":[],"sources":[]}', '{"items":[1],"sources":{}}', '{"items":[],"sources":{"hubs":{"value":"x"}}}', '{"items":[{"key":"a","bucket":"zzz","title":"t"}]}'):
            with open(self.snap, "w") as f:
                f.write(bad)
            r = self.run_once("--once", "--no-pr")
            self.assertEqual(r.returncode, 0, bad + r.stderr)
            self.assertEqual(self.load()["version"], 1)

    def test_pr_total_budget(self):
        with open(self.gh, "w") as f:
            f.write("#!/bin/sh\nsleep 1.2\ncat " + self.gh_out + "\n")
        import time
        start = time.monotonic()
        r = self.run_once("--dry-run", "--repo", "a/a", "--repo", "b/b", "--repo", "c/c", "--repo", "d/d", LRM_PROGRESS_PR_BUDGET="3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.monotonic() - start, 6)
        prs = json.loads(r.stdout)["sources"]["prs"]
        self.assertFalse(prs["ok"])
        self.assertIn("시간 예산", prs["err"])

    def test_gh_resolved_from_candidates_when_path_is_short(self):
        code = ("import sys;sys.path.insert(0,%r);import progress_collect as p;p.GH_CANDIDATES=(%r,);print(p.gh_bin())" % (os.path.join(HERE, "..", "..", "hubs", "scripts"), self.gh))
        env = {"PATH": "/nonexistent", "HOME": self.t.name}
        r = subprocess.run([sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), self.gh)

    def test_plist_uses_python_placeholder_and_path(self):
        with open(os.path.join(HERE, "..", "..", "hubs", "launchd", "ai.lrm.progressdigest.plist"), encoding="utf-8") as f:
            x = f.read()
        self.assertIn("__PYTHON3__", x)
        self.assertNotIn("/usr/bin/python3", x)
        self.assertIn("/opt/homebrew/bin", x)


class WorkspaceCountTest(Base):
    def fake_orca(self, children, active_ids=()):
        hub = "9::/w/coord-config-hub"
        kids = [f"9::/w/config/k{i}" for i in range(children)]
        wts = [{"id": hub, "path": "/w/coord-config-hub", "childWorktreeIds": kids, "parentWorktreeId": None}]
        wts += [{"id": k, "path": k.split("::")[1], "parentWorktreeId": hub} for k in kids]
        wts.append({"id": "9::/w/other", "path": "/w/other", "childWorktreeIds": ["x"]})  # 허브 아님
        terms = [{"worktreeId": kids[i]} for i in active_ids]
        out = os.path.join(self.t.name, "orca")
        with open(out, "w") as f:
            f.write("#!/bin/sh\ncase \"$*\" in\n*worktree*) cat <<'J'\n" + json.dumps({"ok": True, "result": {"worktrees": wts}}) + "\nJ\n;;\n*terminal*) cat <<'J'\n" + json.dumps({"ok": True, "result": {"terminals": terms}}) + "\nJ\n;;\nesac\n")
        os.chmod(out, os.stat(out).st_mode | stat.S_IXUSR)
        return out

    def test_children_and_active_counted_per_hub(self):
        d = json.loads(self.run_once("--dry-run", "--no-pr", LRM_ORCA=self.fake_orca(3, (0, 2))).stdout)
        self.assertEqual(d["groups"], {"config": {"children": 3, "active": 2, "warn": False}})

    def test_more_than_ten_children_warns(self):
        d = json.loads(self.run_once("--dry-run", "--no-pr", LRM_ORCA=self.fake_orca(11)).stdout)
        self.assertTrue(d["groups"]["config"]["warn"])

    def test_orca_failure_keeps_previous_counts(self):
        self.run_once("--once", "--no-pr", LRM_ORCA=self.fake_orca(4))
        self.assertEqual(self.run_once("--once", "--no-pr").returncode, 0)  # orca 없음
        d = self.load()
        self.assertFalse(d["sources"]["workspaces"]["ok"])
        self.assertEqual(d["groups"]["config"]["children"], 4)


class OrcaLookupTest(Base):
    def test_orca_found_with_minimal_path(self):
        """launchd 같은 짧은 PATH 에서도 후보 경로로 orca 를 찾음(LRM_ORCA 없이)"""
        code = ("import sys;sys.path.insert(0,%r);import main_terminal as m,progress_collect as p;"
                "m.find_orca.__defaults__=(None,None,(%r,));print(p.orca_bin())" % (os.path.join(HERE, "..", "..", "hubs", "scripts"), self.gh))
        r = subprocess.run(["env", "-i", "PATH=/usr/bin:/bin", sys.executable, "-I", "-c", code], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), self.gh, r.stderr)

    def test_env_override_wins(self):
        code = ("import sys;sys.path.insert(0,%r);import progress_collect as p;print(p.orca_bin())" % os.path.join(HERE, "..", "..", "hubs", "scripts"))
        r = subprocess.run(["env", "-i", "PATH=/usr/bin:/bin", "LRM_ORCA=/x/orca", sys.executable, "-I", "-c", code], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "/x/orca")


if __name__ == "__main__":
    unittest.main()
