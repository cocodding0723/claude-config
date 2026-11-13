"""할 일 목록 자동 정리 시험: 자동 완료·중복 합침·정렬 순서·허브 칸. 가짜 Slack·gh·orca·임시 기록 파일만 씀."""
import json
import os
import stat
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_digest  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts"))
import progress_collect as pc  # noqa: E402

FakeSlack = test_digest.FakeSlack

COLS = {"title": "C_title", "kind": "C_kind", "urgency": "C_urg", "hub": "C_hub", "answer": "C_ans", "done": "C_done", "link": "C_link", "archive": "C_arch", "order": "C_order"}
REPO = "Org/Repo"
STATE_NAME = "progress-digest.state.json"


def text_of(cell):
    return cell["rich_text"][0]["elements"][0]["elements"][0]["text"]


class AutoSortTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_digest.DigestTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_digest.DigestTest.tearDownClass()

    def setUp(self):
        self.h = test_digest.DigestTest("run_digest")
        self.h.setUp()
        self.addCleanup(self.h.t.cleanup)
        h, root = self.h, self.h.t.name
        h.write_ledger([])  # 장부 행 없음: 허브 줄·PR 만 시험
        with open(os.path.join(h.hub, "bot-list.json"), "w") as f:
            json.dump({"lists": {"t": {"id": "F0CH5KARYBB", "columns": COLS, "options": {"kind": {k: k for k in ("decision", "merge", "input", "review")}}}}}, f)
        self.pr_states = os.path.join(root, "pr_states.json")
        self.set_prs({})
        self.gh = h.script("gh2", f'''#!/bin/sh
if [ "$1 $2" = "pr view" ]; then
  python3 -I - "$3" <<'PY'
import json, sys
d = json.load(open("{self.pr_states}")).get(sys.argv[1])
if d is None:
    sys.exit(1)
print(json.dumps(d))
PY
else
  cat "{h.gh_out}"
fi
''')
        self.worktrees = []
        self.orca = h.script("orca", f'''#!/bin/sh
case "$1 $2" in
  "repo list") echo '{{"result":{{"repos":[{{"displayName":"Repo","path":"/r/Repo"}}]}}}}';;
  "worktree list") cat "{root}/wts.json";;
  *) exit 1;;
esac
''')
        self.set_worktrees([])

    def set_prs(self, states):
        with open(self.pr_states, "w") as f:
            json.dump({str(n): {"state": s, "headRefName": b} for n, (s, b) in states.items()}, f)

    def set_worktrees(self, wts):
        with open(os.path.join(self.h.t.name, "wts.json"), "w") as f:
            json.dump({"result": {"worktrees": wts}}, f)

    def run_digest(self, *args, **extra):
        env = dict(LRM_DIGEST_GH=self.gh, LRM_ORCA=self.orca, LRM_BOTLIST_STATE=os.path.join(self.h.hub, "bot-list.json"), PANEL_GH_REPOS=REPO)
        env.update(extra)
        return self.h.run_digest(*args, **env)

    def state(self):
        with open(os.path.join(self.h.hub, STATE_NAME)) as f:
            return json.load(f)

    def age_cache(self):
        st = self.state()
        for v in st.get("facts", {}).values():
            v["at"] -= 10_000
        with open(os.path.join(self.h.hub, STATE_NAME), "w") as f:
            json.dump(st, f)

    def creates(self):
        out = []
        for m, b in FakeSlack.calls:
            if m == "slackLists.items.create":
                cells = {k: next(f for f in b["initial_fields"] if f["column_id"] == c) for k, c in COLS.items() if any(f["column_id"] == c for f in b["initial_fields"])}
                out.append(cells)
        return out

    def updates(self):
        return [b for m, b in FakeSlack.calls if m == "slackLists.items.update"]

    def put_cards(self, *cards, done=()):
        with open(os.path.join(self.h.hub, "slack-sent.jsonl"), "w") as f:
            for did, ts, tag in cards:
                f.write(json.dumps({"ts": ts, "decision_id": did, "form_kind": "decide", "options": ["A", "B"], "tag": tag, "title": f"결정 :비서-선택해주세요: {did} 카드", "channel": "D1", "at": float(ts)}) + "\n")
        with open(os.path.join(self.h.hub, "slack-done.txt"), "w") as f:
            f.write("\n".join(done))

    def put_hubs_table(self, *slugs):
        with open(os.path.join(self.h.hub, "hubs.tsv"), "w") as f:
            f.write("# slug\tname\n" + "".join(f"{s}\t{s} 이름\n" for s in slugs))

    # ---------- 자동 완료 ----------
    def test_merged_pr_row_completed_open_pr_row_kept(self):
        self.h.put_hub("ui", blockers=["LRM-511: #731 병합 대기"])
        self.h.put_hub("server", blockers=["#746 병합 대기"])
        self.set_prs({731: ("OPEN", "feat/a"), 746: ("OPEN", "feat/b")})
        self.assertEqual(self.run_digest().returncode, 0)
        self.assertEqual(len(self.creates()), 2)
        FakeSlack.calls.clear()
        self.set_prs({731: ("MERGED", "feat/a"), 746: ("OPEN", "feat/b")})
        self.age_cache()
        self.assertEqual(self.run_digest().returncode, 0)
        ups = self.updates()
        self.assertEqual(len(ups), 1, ups)
        self.assertEqual([c["column_id"] for c in ups[0]["cells"]], [COLS["done"]])
        self.assertTrue(ups[0]["cells"][0]["checkbox"])
        self.assertEqual(len([k for k in self.state()["items"] if k.startswith("done:")]), 1)

    def test_answered_decision_completes_card_row_and_hub_line_row_together(self):
        now = int(time.time())
        self.h.put_hub("data-design", blockers=["LRM-395 D-JSID 답 대기"])
        self.put_cards(("D-JSID", str(now - 3600) + ".1", "data-design"))
        self.assertEqual(self.run_digest().returncode, 0)
        made = self.creates()
        self.assertEqual(len(made), 1, "카드 행과 허브 줄 행은 같은 결정이라 1행")
        FakeSlack.calls.clear()
        self.put_cards(("D-JSID", str(now - 3600) + ".1", "data-design"), done=[str(now - 3600) + ".1"])  # 사용자가 버튼으로 답함
        self.assertEqual(self.run_digest().returncode, 0)
        ups = self.updates()
        self.assertEqual((len(ups), ups[0]["cells"][0]["column_id"], ups[0]["cells"][0]["checkbox"]), (1, COLS["done"], True))
        self.assertEqual(self.creates(), [])

    def test_open_card_not_completed(self):
        self.h.put_hub("data-design", blockers=["LRM-395 D-JSID 답 대기"])
        self.put_cards(("D-JSID", str(int(time.time()) - 60) + ".1", "data-design"))
        self.run_digest()
        FakeSlack.calls.clear()
        self.run_digest("--force")
        self.assertEqual(self.updates(), [])

    def test_gh_failure_keeps_rows(self):
        self.h.put_hub("ui", blockers=["#731 병합 대기"])
        self.set_prs({})  # gh pr view 가 실패(모르는 PR)
        self.run_digest()
        FakeSlack.calls.clear()
        self.run_digest("--force")
        self.assertEqual(self.updates(), [])
        self.assertEqual(len(self.state()["items"]), 1)

    # ---------- 중복 방지 ----------
    def test_existing_duplicates_collapse_to_one_row_and_rest_done_archived(self):
        self.h.put_hub("ui", blockers=["LRM-511: #731 병합 대기"])
        self.h.put_hub("data-design", blockers=["시트 탭(#731 병합 뒤): #731 머지"])
        self.set_prs({731: ("OPEN", "feat/a")})
        keys = [f"hub:ui:b:{pc.short_hash('LRM-511: #731 병합 대기')}", f"hub:data-design:b:{pc.short_hash('시트 탭(#731 병합 뒤): #731 머지')}"]
        with open(os.path.join(self.h.hub, STATE_NAME), "w") as f:  # 옛 상태: 같은 #731 이 3행으로 갈라져 있음
            json.dump({"hash": "", "dm_hash": "", "list_id": "F0CH5KARYBB", "links": {}, "items": {
                keys[0]: {"record": "R1", "title": "x", "bucket": "block", "link": "", "sig": ["#731 머지", "block", "", "merge", 2, "ui", "#731 머지 버튼"]},
                keys[1]: {"record": "R2", "title": "x", "bucket": "block", "link": "", "sig": ["#731 머지", "block", "", "merge", 2, "data-design", "#731 머지 버튼"]},
                "hub:x:b:old3": {"record": "R3", "title": "x", "bucket": "block", "link": "", "sig": ["#731 머지", "block", "", "merge", 2, "x", "#731 머지 버튼"]}}}, f)
        self.assertEqual(self.run_digest().returncode, 0)
        st = self.state()
        self.assertEqual(self.creates(), [])  # 새 행은 만들지 않음. 기존 행 재사용
        marks = [(c["row_id"], c["column_id"]) for u in self.updates() for c in u["cells"] if c.get("checkbox")]
        done_rows = {r for r, c in marks if c == COLS["done"]}
        arch_rows = {r for r, c in marks if c == COLS["archive"]}
        self.assertEqual(done_rows, {"R2", "R3"})  # R1 은 남는 행, R2 는 합쳐진 중복, R3 은 수집에서 사라진 행
        self.assertEqual(arch_rows, {"R2"})  # 합쳐진 중복은 완료와 보관을 한 번에(삭제 아님)
        self.assertEqual(set(st["items"]), {keys[0], "done:R3"})  # 남는 행 1개 + 사라진 행의 완료 기록

    def test_same_pr_from_open_pr_list_and_hub_line_is_one_row(self):
        self.h.put_hub("ui", blockers=["#731 병합 대기"])
        self.h.write_gh([{"number": 731, "title": "기능", "url": "u", "reviewDecision": "APPROVED", "mergeable": "MERGEABLE"}])
        self.set_prs({731: ("OPEN", "feat/a")})
        self.run_digest("--repo", REPO)
        self.assertEqual(len(self.creates()), 1)

    # ---------- 자동 정렬 ----------
    def test_new_rows_created_in_sort_order_with_order_column(self):
        now = int(time.time())
        self.h.put_hub("ui", blockers=["#742 병합 대기", "라이브 규칙 창 직접 승인"])
        self.put_cards(("D-OLD", f"{now - 30 * 3600}.1", "ui"), ("D-NEW", f"{now - 600}.1", "ui"))
        self.set_prs({742: ("OPEN", "feat/z")})
        self.assertEqual(self.run_digest().returncode, 0)
        made = self.creates()
        urg = [c["urgency"]["number"][0] for c in made]
        kinds = [c["kind"]["select"][0] for c in made]
        self.assertEqual(urg, [3, 2, 1, 1])  # 급함이 먼저, 같은 급함 안에서 결정 → 머지
        self.assertEqual(kinds, ["decision", "input", "decision", "merge"])
        orders = [c["order"]["number"][0] for c in made]
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(len(set(orders)), 4)

    # ---------- 허브 칸 ----------
    def test_pr_hub_from_head_branch_workspace_parent_not_status_file_author(self):
        self.put_hubs_table("ui-layer", "server")
        self.h.put_hub("server", blockers=["QA 끝 신호·#746 병합"])  # server 허브 줄이 언급했을 뿐 #746 은 ui-layer 작업
        self.h.put_hub("vfx", blockers=["#750 병합 대기"])  # 작업 공간을 못 찾는 PR
        self.set_prs({746: ("OPEN", "feat/b"), 750: ("OPEN", "feat/none")})
        hub = {"id": "R::/w/coord-ui-layer-hub", "path": "/w/coord-ui-layer-hub", "projectId": f"github:{REPO}", "branch": "refs/heads/main"}
        child = {"id": "R::/w/ws-b", "path": "/w/ws-b", "projectId": f"github:{REPO}", "branch": "refs/heads/feat/b", "parentWorktreeId": hub["id"]}
        self.set_worktrees([hub, child])
        self.assertEqual(self.run_digest().returncode, 0)
        hubs = {text_of(c["title"]): text_of(c["hub"]) for c in self.creates()}
        self.assertEqual(hubs, {"#746 머지": "ui-layer", "#750 머지": "미상"})

    def test_decision_hub_from_hub_reports_then_unknown(self):
        now = int(time.time())
        self.put_hubs_table("data-design", "config")
        self.put_cards(("D-A", f"{now - 60}.1", ""), ("D-B", f"{now - 50}.1", ""))
        with open(os.path.join(self.h.hub, "hub-reports.jsonl"), "w") as f:
            f.write(json.dumps({"id": "r1", "hub": "data-design", "decision_id": "D-A", "kind": "decide", "text": "t", "at": now}) + "\n")
        self.assertEqual(self.run_digest().returncode, 0)
        got = {text_of(c["title"]): text_of(c["hub"]) for c in self.creates()}
        self.assertEqual(sorted(got.values()), ["data-design", "미상"])

    def test_dry_run_prints_change_table_and_writes_nothing(self):
        self.h.put_hub("ui", blockers=["#731 병합 대기"])
        self.set_prs({731: ("MERGED", "feat/a")})
        r = self.run_digest("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(FakeSlack.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.h.hub, STATE_NAME)))
        d = json.loads(self.run_digest("--dry-run", "--json").stdout)
        self.assertEqual(d["changes"], [])  # 이미 끝난 PR 의 행은 목록에 만들 이유가 없음


if __name__ == "__main__":
    unittest.main()
