"""todo_facts.py 시험. 가짜 gh 스크립트·가짜 작업 공간 목록·임시 기록 파일만 씀."""
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts"))
import panel_collect  # noqa: E402
import todo_facts  # noqa: E402

NOW = 2_000_000_000


class CardsTest(unittest.TestCase):
    def card(self, did, ts, tag="data-design"):
        return {"ts": ts, "decision_id": did, "form_kind": "decide", "options": ["A", "B"], "tag": tag}

    def store(self, sent, inbox, done=()):
        """임시 허브 폴더에 기록을 써서 answer_store 의 카드 단위 판정을 읽음(결정 완료 판정은 그 한 곳)"""
        import answer_store
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        for name, rows in (("slack-sent.jsonl", sent), ("slack-inbox.jsonl", inbox)):
            write_text(os.path.join(t.name, name), "".join(json.dumps(r) + "\n" for r in rows))
        write_text(os.path.join(t.name, "slack-done.txt"), "".join(x + "\n" for x in done))
        return answer_store.AnswerStore(t.name)

    def test_closed_by_done_file_or_reaction_not_by_hold(self):
        sent = [self.card("D-A", "1.1"), self.card("D-B", "2.1"), self.card("D-C", "3.1"), self.card("D-D", "4.1")]
        inbox = [{"type": "reaction", "parent_ts": "2.1", "kind": "decide", "decision_id": "D-B", "action": "approve", "pick": 1},
                 {"type": "reaction", "parent_ts": "3.1", "kind": "decide", "decision_id": "D-C", "action": "hold"}]
        self.assertEqual(self.store(sent, inbox, {"1.1"}).closed_by_did(), {"D-A": True, "D-B": True, "D-C": False, "D-D": False})

    def test_resent_card_stays_open_until_all_closed(self):
        sent = [self.card("D-A", "1.1"), self.card("D-A", "2.1")]
        self.assertFalse(self.store(sent, [], {"1.1"}).closed_by_did()["D-A"])
        self.assertTrue(self.store(sent, [], {"1.1", "2.1"}).closed_by_did()["D-A"])

    def test_decision_hub_prefers_reports_and_ignores_non_hub_tag(self):
        sent = [self.card("D-A", "1.1", "ci-watch"), self.card("D-B", "2.1", "server"), self.card("D-C", "3.1", "")]
        reports = [{"decision_id": "D-B", "hub": "config"}]
        got = todo_facts.decision_hubs(reports, sent, {"server", "config", "data-design"})
        self.assertEqual(got, {"D-B": "config"})


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class PrTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.calls = os.path.join(self.t.name, "calls.log")
        self.gh = os.path.join(self.t.name, "gh")
        write_text(self.gh, f'''#!/bin/sh
echo "$*" >> "{self.calls}"
case "$3" in
  746) echo '{{"state":"OPEN","headRefName":"feat/server-x"}}';;
  731) echo '{{"state":"MERGED","headRefName":"feat/data"}}';;
  *) echo "not found" >&2; exit 1;;
esac
''')
        os.chmod(self.gh, os.stat(self.gh).st_mode | stat.S_IXUSR)
        hub = {"id": "R::/w/coord-ui-layer-hub", "path": "/w/coord-ui-layer-hub", "projectId": "github:Org/Repo", "branch": "refs/heads/main"}
        child = {"id": "R::/w/x", "path": "/w/ws/feat-server-x", "projectId": "github:Org/Repo", "branch": "refs/heads/feat/server-x", "parentWorktreeId": hub["id"]}
        self.wts = [hub, child]
        self.wt_calls = []

    def call_count(self):
        with open(self.calls, encoding="utf-8") as f:
            return len(f.read().splitlines())

    def facts(self, cache=None, wts="default"):
        def worktrees(repo):
            self.wt_calls.append(repo)
            return self.wts if wts == "default" else wts
        return todo_facts.Facts(self.gh, ["Org/Repo"], worktrees, {"ui-layer", "coord-server-hub"}, cache, NOW, 30)

    def test_merged_pr_closes_and_open_does_not_unknown_stays_open(self):
        f = self.facts()
        self.assertEqual(f.closed(["pr:731", "pr:746", "pr:999"]), {"pr:731"})

    def test_merged_cached_forever_open_ttl(self):
        cache = {}
        self.facts(cache).closed(["pr:731", "pr:746"])
        n = self.call_count()
        self.facts(cache).closed(["pr:731", "pr:746"])  # 같은 시각: 전부 캐시
        self.assertEqual(self.call_count(), n)
        later = todo_facts.Facts(self.gh, ["Org/Repo"], lambda r: [], set(), cache, NOW + todo_facts.OPEN_TTL_S + 1, 30)
        later.closed(["pr:731", "pr:746"])
        self.assertEqual(self.call_count(), n + 1)  # 열림만 다시 물음

    def test_pr_hub_is_parent_hub_of_head_branch_workspace(self):
        f = self.facts()
        self.assertEqual(f.hub(["pr:746"]), "ui-layer")
        self.assertEqual(self.wt_calls, ["Org/Repo"])
        f.hub(["pr:746"])
        self.assertEqual(self.wt_calls, ["Org/Repo"])  # 목록 조회는 회차당 한 번

    def test_no_evidence_gives_none(self):
        self.assertIsNone(self.facts(wts=[]).hub(["pr:746"]))
        self.assertIsNone(self.facts().hub(["pr:999"]))  # gh 가 모르는 PR
        self.assertIsNone(self.facts(wts=None).hub(["pr:746"]))  # 작업 공간 조회 불완전

    def test_gh_failure_is_unknown_not_closed(self):
        os.chmod(self.gh, 0o644)
        self.assertEqual(self.facts().closed(["pr:731"]), set())

    def test_decision_and_ledger_targets(self):
        f = self.facts()
        f.cards, f.dec_hubs, f.ledger = {"D-A": True, "D-B": False}, {"D-B": "server"}, {"F9"}
        self.assertEqual(f.closed(["decision:D-A", "decision:D-B", "decision:D-Z", "ledger:F9", "ledger:F1"]), {"decision:D-A", "ledger:F9"})
        self.assertEqual(f.hub(["decision:D-B"]), "server")
        self.assertIsNone(f.hub(["decision:D-Z"]))


class LoadLocalTest(unittest.TestCase):
    def test_reads_records_and_tolerates_missing_files(self):
        with tempfile.TemporaryDirectory() as d:
            w = lambda n, text: write_text(os.path.join(d, n), text)  # noqa: E731
            w("hubs.tsv", "# slug\tname\nconfig\t개인 설정\ncoord-server-hub\t서버\n")
            w("slack-sent.jsonl", json.dumps({"ts": "1.1", "decision_id": "D-A", "form_kind": "decide", "options": ["A"], "tag": "server"}) + "\n")
            w("slack-done.txt", "1.1\n")
            w("hub-reports.jsonl", json.dumps({"decision_id": "D-A", "hub": "config"}) + "\n")
            f = todo_facts.load_local(todo_facts.Facts("gh", [], lambda r: [], []), d, os.path.join(d, "none.tsv"))
            self.assertEqual((f.cards, f.dec_hubs, f.ledger, f.errors), ({"D-A": True}, {"D-A": "config"}, set(), []))
            self.assertEqual(f.hub_slugs, {"config", "coord-server-hub"})


if __name__ == "__main__":
    unittest.main()
