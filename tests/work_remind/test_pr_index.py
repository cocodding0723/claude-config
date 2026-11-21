import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "hubs", "scripts"))
from pr_index import PrIndex
import work_remind as wr


class PrIndexTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = os.path.realpath(tmp.name)
        self.other = os.path.join(self.repo, "other")
        os.mkdir(self.other)
        self.calls = []
        self.rows = [
            dict(number=7, state="MERGED", title="[LRM-63] 우편함", headRefName="old"),
            dict(number=8, state="OPEN", title="[LRM-637] 다른 일", headRefName="feature/mail"),
        ]
        self.common = {self.repo: ".git", self.other: "../.git"}
        self.error = None
        self.index = PrIndex("fake-gh", run=self.run_fake, limit=400, timeout=20)

    def run_fake(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout=self.common[kwargs["cwd"]])
        if self.error:
            raise self.error
        return SimpleNamespace(returncode=0, stdout=json.dumps(self.rows))

    def card(self, repo=None, title="[LRM-63] 우편함", branch="feature/mail"):
        return dict(title=title, labels=["repo:" + (repo or self.repo), "branch:" + branch])

    def gh_calls(self):
        return [cmd for cmd, _ in self.calls if cmd[0] == "fake-gh"]

    def test_branch_priority(self):
        self.assertEqual(self.index.for_card(self.card()), [(8, "OPEN")])
        self.assertTrue(self.index.for_card(self.card()).branch_matched)
        self.assertEqual(self.gh_calls(), [["fake-gh", "pr", "list", "--state", "all", "--limit", "400", "--json", "number,state,title,headRefName"]])

    def test_branch_fallback_with_and_without_ticket(self):
        for title in ("[LRM-64] 우편함", "우편함"):
            self.assertEqual(self.index.for_card(self.card(title=title)), [(8, "OPEN")])
        self.assertEqual(self.index.for_card(self.card(title="없음", branch="missing")), [])
        self.assertEqual(len(self.gh_calls()), 1)

    def test_title_fallback_is_display_only_and_respects_ticket_boundary(self):
        prs = self.index.for_card(self.card(branch="missing"))
        self.assertEqual(prs, [(7, "MERGED")])
        self.assertEqual(prs.basis, "title")
        self.assertFalse(prs.branch_matched)

    def test_shared_ticket_keeps_other_features_in_progress(self):
        self.rows = [dict(number=1052, state="MERGED", title="[LRM-731] demo", headRefName="feature/lrm-0az")]
        cards = [dict(self.card(title="[LRM-731] " + cid, branch="feature/" + cid),
                      id=cid, issue_type="epic", status="open", labels=["repo:" + self.repo,
                      "feature:" + cid, "branch:feature/" + cid]) for cid in ("lrm-0az", "lrm-7eq", "lrm-ywd")]
        features = wr.features(cards, prs_of=self.index.for_card)
        self.assertEqual([f["name"] for f in features], ["lrm-7eq", "lrm-ywd"])
        self.assertTrue(all(not f["merged"] for f in features))
        self.assertTrue(all("#1052" in f["pr"]["note"] for f in features))

    def test_common_directory_shares_cache_between_worktrees(self):
        self.index.load(self.repo)
        self.index.for_card(self.card(repo=self.other))
        self.assertEqual(len(self.gh_calls()), 1)

    def test_one_call_per_distinct_repository(self):
        self.common[self.other] = ".git"
        for _ in range(30):
            self.index.for_card(self.card())
            self.index.for_card(self.card(repo=self.other))
        self.assertEqual(len(self.gh_calls()), 2)

    def test_failed_load_is_unknown_and_cached(self):
        self.error = subprocess.TimeoutExpired("fake-gh", 20)
        self.assertIsNone(self.index.for_card(self.card()))
        self.assertIsNone(self.index.for_card(self.card(repo=self.other)))
        self.assertEqual(len(self.gh_calls()), 1)

    def test_off_and_missing_repository_do_not_call_commands(self):
        self.assertIsNone(PrIndex("off", run=self.run_fake, limit=400, timeout=20).for_card(self.card()))
        self.assertIsNone(self.index.for_card(dict(title="x", labels=[])))
        self.assertIsNone(self.index.load(os.path.join(self.repo, "missing")))
        self.assertEqual(self.calls, [])

    def test_invalid_json_and_failed_git_are_unknown(self):
        for stdout in ("invalid", "{}", "[1]"):
            with self.subTest(stdout=stdout):
                def run(cmd, **kwargs):
                    return SimpleNamespace(returncode=0, stdout=".git" if cmd[0] == "git" else stdout)
                self.assertIsNone(PrIndex("gh", run=run, limit=400, timeout=20).load(self.repo))
        def failed(cmd, **kwargs):
            return SimpleNamespace(returncode=1, stdout="")
        self.assertIsNone(PrIndex("gh", run=failed, limit=400, timeout=20).load(self.repo))
