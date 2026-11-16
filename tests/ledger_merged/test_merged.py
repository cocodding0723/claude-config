"""ledger_merged.py 시험: a/b 자동 완료, 애매 후보, 멱등, dry-run 무변경, 증거 형식, gh 실패. 가짜 gh·임시 장부."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hubs", "scripts"))
import ledger_merged as lm  # noqa: E402
import task_ledger as tl  # noqa: E402


def pr(n, title, branch="feat/x", body="", base="dev"):
    return {"number": n, "title": title, "body": body, "headRefName": branch, "mergedAt": "2026-11-16T02:00:00Z", "baseRefName": base}


def row(rid, text, state="진행", needs=(), evidence=(), refs=()):
    return {"id": rid, "text": text, "received_at": "2026-11-16T09:00:00+09:00", "hub": "", "state": state, "needs": list(needs), "done_when": "",
            "evidence": list(evidence), "sources": [{"kind": "manual", "ref": r} for r in refs], "updated_at": "2026-11-16T09:00:00+09:00",
            "history": [], "assigned_at": "", "started_at": "", "done_at": ""}


MERGED = [
    pr(801, "[LRM-701] feat: 단독 티켓"),
    pr(802, "[LRM-702] feat: 첫 PR"),
    pr(803, "[LRM-702] fix: 같은 키 둘째 PR"),
    pr(804, "[LRM-703] feat: 열린 PR 짝 있음"),
    pr(805, "제목에 키 없음", body="관련 LRM-704"),
    pr(806, "[LRM-705] feat: 후속 확인용"),
    pr(807, "base 다름", base="release/x"),
    pr(808, "[LRM-706] feat: 대기 행"),
    pr(809, "번호 연결 PR"),
]
OPEN = [{"number": 900, "title": "[LRM-703] wip 둘째", "headRefName": "feat/LRM-703-b", "baseRefName": "dev"}]
ROWS = [
    row("T0001", "#809 dev 병합"),                                      # a 확실
    row("T0002", "LRM-701 PR dev 병합"),                                # b 확실
    row("T0003", "LRM-702 병합", refs=["note"]),                        # 후보: 같은 키 병합 PR 여러 개
    row("T0004", "LRM-703 병합"),                                       # 후보: 열린 PR
    row("T0005", "LRM-704 병합"),                                       # 후보: 키가 본문에만
    row("T0006", "LRM-705 화면 확인"),                                  # 후보: 후속 작업
    row("T0007", "LRM-701 병합 관련 정리", state="미분류"),             # 후보: 미분류
    row("T0008", "LRM-706 병합", needs=["T0009"]),                      # 후보: needs 미완료
    row("T0009", "다른 일"),
    row("T0010", "#801·#999 병합"),                                     # 후보: 일부 병합(999 미확인)
    row("T0011", "#809 병합", state="완료", evidence=["PR #1"]),        # 건너뜀: 이미 완료
    row("T0012", "관련 없는 일"),                                       # 건너뜀: 연결 없음
    row("T0013", "#807 병합"),                                          # base 다름 → 연결 없음
]


class MergedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.gh = os.path.join(d, "gh")
        os.makedirs(self.gh)
        self.dump(os.path.join(self.gh, "merged.json"), MERGED)
        self.dump(os.path.join(self.gh, "open.json"), OPEN)
        self.tasks, self.state = os.path.join(d, "tasks.jsonl"), os.path.join(d, "state.json")
        with open(self.tasks, "w", encoding="utf-8") as f:
            f.write(tl.render([dict(r) for r in ROWS]))
        env = {"LRM_TASKS": self.tasks, "LRM_MERGED_STATE": self.state, "LRM_HUB_DIR": d, "FAKE_GH_DIR": self.gh,
               "LRM_GH": os.path.join(HERE, "fake_gh.sh")}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    @staticmethod
    def text(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def dump(path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = lm.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def js(self, *argv):
        rc, out, err = self.run_cli(*argv, "--json")
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def read(self):
        return {r["id"]: r for r in tl.read_rows(self.tasks)}

    def test_auto_a_and_b(self):
        res = self.js("--since", "2026-11-09")
        self.assertEqual({a["id"]: a["evidence"] for a in res["auto"]}, {"T0001": ["PR #809"], "T0002": ["PR #801"]})

    def test_ambiguous_are_candidates_only(self):
        cands = {c["id"]: c["reason_keys"] for c in self.js("--since", "2026-11-09")["candidates"]}
        self.assertIn("multi", cands["T0003"])
        self.assertIn("open_pr", cands["T0004"])
        self.assertIn("body_only", cands["T0005"])
        self.assertIn("followup", cands["T0006"])
        self.assertIn("unsorted", cands["T0007"])
        self.assertIn("needs", cands["T0008"])
        self.assertIn("partial", cands["T0010"])
        for rid in ("T0003", "T0004", "T0005", "T0006", "T0007", "T0008", "T0010"):
            self.assertIn(rid, cands)

    def test_skips_counted(self):
        sk = self.js("--since", "2026-11-09")["skipped"]
        self.assertEqual(sk["already_done"], 1)
        self.assertEqual(sk["base_other"], 1)
        self.assertGreaterEqual(sk["no_link"], 2)

    def test_dry_run_changes_nothing(self):
        before = self.text(self.tasks)
        rc, out, _ = self.run_cli("--since", "2026-11-09")
        self.assertEqual(rc, 0)
        self.assertIn("[dry-run]", out)
        self.assertEqual(self.text(self.tasks), before)
        self.assertFalse(os.path.exists(self.state))

    def test_apply_completes_with_valid_evidence(self):
        rc, _, err = self.run_cli("--since", "2026-11-09", "--apply")
        self.assertEqual(rc, 0, err)
        rows = self.read()
        for rid, ev in (("T0001", "PR #809"), ("T0002", "PR #801")):
            self.assertEqual(rows[rid]["state"], "완료")
            self.assertEqual(rows[rid]["evidence"], [ev])
            self.assertTrue(rows[rid]["done_at"])
        tl.check_rows(list(rows.values()))  # 증거 형식·불변식 통과
        for rid in ("T0003", "T0004", "T0005", "T0006", "T0007", "T0008", "T0010"):
            self.assertNotEqual(rows[rid]["state"], "완료")
        st = json.loads(self.text(self.state))
        self.assertEqual(st["processed"], [801, 809])
        self.assertIn("T0003", st["candidates"])

    def test_idempotent(self):
        self.run_cli("--since", "2026-11-09", "--apply")
        after1 = self.text(self.tasks)
        res = self.js("--apply")  # 기준일은 상태 파일에서
        self.assertEqual(res["auto"], [])
        self.assertEqual(self.text(self.tasks), after1)

    def test_no_since_is_usage_error(self):
        rc, _, err = self.run_cli()
        self.assertEqual(rc, 2)
        self.assertIn("--since", err)

    def test_gh_failure_exit_1_and_no_write(self):
        open(os.path.join(self.gh, "fail"), "w").close()
        before = self.text(self.tasks)
        rc, _, err = self.run_cli("--since", "2026-11-09", "--apply")
        self.assertEqual(rc, 1)
        self.assertIn("gh", err)
        self.assertEqual(self.text(self.tasks), before)
        self.assertFalse(os.path.exists(self.state))

    def test_all_prs_merged_row(self):
        """여러 PR 을 가리키는 병합 행은 전부 병합 확인(조회 밖 번호는 gh pr view)이 있어야 완료"""
        rows = [row("T0020", "#809·#700 dev 병합")]
        with open(self.tasks, "w", encoding="utf-8") as f:
            f.write(tl.render(rows))
        self.assertEqual(self.js("--since", "2026-11-09")["auto"], [])
        self.dump(os.path.join(self.gh, "view-700.json"), {"number": 700, "state": "MERGED", "baseRefName": "dev"})
        self.assertEqual([a["id"] for a in self.js("--since", "2026-11-09")["auto"]], ["T0020"])


if __name__ == "__main__":
    unittest.main()
