import json
import os

from fixture import Base, FakeOrca, hs


def term(path, handle):
    return {"handle": handle, "worktreePath": path, "agentIdentity": "claude", "connected": True, "writable": True, "title": "hub"}


class Run(Base):
    def setUp(self):
        super().setUp()
        self.routes = os.path.join(self.d, "intake-routes.tsv")
        open(self.routes, "w").write("# 머리말\t대상\nui\tui\nvfx\tvfx\n*\tmain\n")
        with open(os.path.join(self.d, "intake-routes.jsonl"), "w") as f:
            f.write(json.dumps({"id": "C0012", "hub": "ui", "text": "팝업 닫기 버튼 정렬", "at": 1}) + "\n")
        open(os.environ["LRM_LEDGER"], "w").write("id\t접수\t요청(원문 요약)\t상태\t담당\t진행·증거\t갱신\n"
                                                  "C0012\t11-12\t팝업 정렬\t진행\tui\t\t2026-11-12 11:00\n"
                                                  "C0013\t11-12\t팝업 색\t진행\tui-popup\t\t2026-11-12 11:00\n")
        self.role = os.path.join(self.d, "role-ui-popup.txt")
        open(self.role, "w").write("팝업 역할")
        self.create = os.path.join(self.d, "create_hub.sh")
        open(self.create, "w").write('#!/bin/bash\necho "$@" >> "$(dirname "$0")/create.calls"\nexit "${FAKE_CREATE_RC:-0}"\n')
        os.environ["LRM_CREATE_HUB"] = self.create
        os.environ["LRM_HUB_STATUS_DIR"] = os.path.join(self.d, "status")
        self.run = FakeOrca(self.root, {"ui-layer": 10, "vfx": 1})
        self.sent = []
        hubp = lambda s: os.path.join(self.root, f"coord-{s}-hub")  # noqa: E731
        self.run.replies["terminal list"] = (0, {"ok": True, "result": {"terminals": [term(hubp("ui-layer"), "term_a1"), term(hubp("ui-popup"), "term_b2")]}})

        def send(args):
            self.sent.append((args[args.index("--terminal") + 1], args[args.index("--text") + 1]))
            return 0, {"ok": True, "result": {"send": {"accepted": True, "prompt": {"stages": ["input_accepted", "turn_started"]}}}}
        self.run.replies["terminal send"] = send
        self.hubs_before = open(hs.hubs_tsv()).read()
        self.routes_before = open(self.routes).read()

    def tearDown(self):
        for k in ("LRM_CREATE_HUB", "LRM_HUB_STATUS_DIR", "FAKE_CREATE_RC"):
            os.environ.pop(k, None)
        super().tearDown()

    def propose(self):
        rc, _, err = self.cli(["propose", "--kind", "split", "--hub", "ui-layer", "--child", "ui-popup", "--child-name", "UI 팝업",
                               "--prefix", "ui팝업", "--decision-id", "D-S1", "--no-doc", "--record"], self.run)
        self.assertEqual(rc, 0, err)

    def apply(self, *extra):
        return self.cli(["run", "--decision-id", "D-S1", "--move", "C0012", "--role-file", self.role, "--apply", "--json", *extra], self.run)

    def test_dry_run_default_changes_nothing(self):
        rc, out, err = self.cli(["run", "--decision-id", "D-T1", "--kind", "split", "--hub", "ui-layer", "--child", "ui-popup",
                                 "--child-name", "UI 팝업", "--prefix", "ui팝업", "--move", "C0012", "--json"], self.run)
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        self.assertEqual(d["mode"], "dry-run")
        self.assertEqual([s["status"] for s in d["steps"]], ["plan"] * 10)
        self.assertEqual(open(hs.hubs_tsv()).read(), self.hubs_before)
        self.assertEqual(open(self.routes).read(), self.routes_before)
        self.assertFalse(os.path.exists(os.environ["LRM_REPORT_OUTBOX"]))
        self.assertFalse(os.path.exists(os.path.join(self.d, "hub-split-runs")))
        self.assertEqual(self.sent, [])
        self.assertFalse(any(c[:2] in (["worktree", "set"], ["terminal", "close"]) for c in self.run.calls))

    def test_apply_requires_recorded_proposal(self):
        rc, _, err = self.apply()
        self.assertEqual(rc, 4)
        self.assertIn("propose --record", err)
        self.assertEqual(open(hs.hubs_tsv()).read(), self.hubs_before)

    def test_split_apply_full_order(self):
        self.propose()
        rc, out, err = self.apply()
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        self.assertEqual([s["status"] for s in d["steps"]], ["done"] * 10)
        hubs = {h.slug: h for h in hs.read_hubs()}
        self.assertEqual(hubs["ui-popup"].parent, "ui-layer")
        routes = open(self.routes).read().splitlines()
        self.assertLess(routes.index("ui팝업\tui-popup"), routes.index("*\tmain"))
        self.assertIn("ui\tui", routes)  # 기존 머리말은 부모에 둠
        self.assertEqual(self.sent[0], ("term_b2", "[C0012] 팝업 닫기 버튼 정렬"))
        self.assertEqual(self.sent[1][0], "term_a1")
        ledger = open(os.environ["LRM_LEDGER"]).read()
        self.assertIn("C0012\t11-12\t팝업 정렬\t진행\tui-popup", ledger)
        info = [json.loads(x) for x in open(os.environ["LRM_REPORT_OUTBOX"]).read().splitlines() if '"info"' in x]
        self.assertEqual(len(info), 2)
        self.assertEqual(len(os.listdir(os.path.join(self.d, "hub-split-runs", "D-S1", "backup"))), 2)
        st = json.load(open(hs.state_path()))
        self.assertIn("split_at", st["events"]["ui-popup"])

    def test_failure_records_progress_and_rerun_resumes(self):
        self.propose()
        os.environ["FAKE_CREATE_RC"] = "1"
        rc, out, err = self.apply()
        self.assertEqual(rc, 4)
        self.assertIn("backup 으로 되돌림", err)
        prog = json.load(open(os.path.join(self.d, "hub-split-runs", "D-S1", "progress.json")))
        self.assertEqual(prog["done"], ["backup", "report_before"])
        os.environ["FAKE_CREATE_RC"] = "0"
        rc, out, err = self.apply()
        self.assertEqual(rc, 0, err)
        steps = json.loads(out)["steps"]
        self.assertEqual([s["status"] for s in steps[:2]], ["skip", "skip"])
        self.assertEqual(open(self.routes).read().count("ui팝업"), 1)
        rows = [ln for ln in open(hs.hubs_tsv()).read().splitlines() if ln.startswith("ui-popup\t")]
        self.assertEqual(len(rows), 1)

    def test_merge_refuses_with_live_workers_then_reverses(self):
        self.write_hubs(self.HUBS + [("ui-popup", "팝업", "~/r/role-ui-popup.txt", "-", "-", "ui-layer")])
        open(self.routes, "a").write("ui팝업\tui-popup\n")
        self.run = FakeOrca(self.root, {"ui-layer": 2, "ui-popup": 2})
        self.run.replies["terminal list"] = (0, {"ok": True, "result": {"terminals": [term(os.path.join(self.root, "coord-ui-layer-hub"), "term_a1"), term(os.path.join(self.root, "coord-ui-popup-hub"), "term_b2")]}})
        self.run.replies["terminal send"] = (0, {"ok": True, "result": {"send": {"accepted": True, "prompt": {"stages": ["turn_started"]}}}})
        moved = []

        def wset(args):
            moved.append(args[3][5:])
            for w in self.run.worktrees:
                if w["id"] == "ui-popup":
                    w["childWorktreeIds"] = [i for i in w["childWorktreeIds"] if os.path.join(self.root, i) not in moved]
            return 0, {"ok": True}
        self.run.replies["worktree set"] = wset
        rc, _, err = self.cli(["propose", "--kind", "merge", "--child", "ui-popup", "--decision-id", "D-M1", "--no-doc", "--record"], self.run)
        self.assertEqual(rc, 0, err)
        os.makedirs(os.environ["LRM_HUB_STATUS_DIR"])
        sp = os.path.join(os.environ["LRM_HUB_STATUS_DIR"], "ui-popup.json")
        json.dump({"slug": "ui-popup", "updatedAt": "2026-11-12T10:00:00+09:00", "workers": ["ctx_1 /ws/a"], "tasks": [], "branches": [], "blockers": []}, open(sp, "w"))
        rc, _, err = self.cli(["run", "--decision-id", "D-M1", "--apply"], self.run)
        self.assertEqual(rc, 4)
        self.assertIn("진행 워커 1명", err)
        self.assertIn("ui-popup", open(hs.hubs_tsv()).read())
        json.dump({"slug": "ui-popup", "updatedAt": "2026-11-12T10:00:00+09:00", "workers": [], "tasks": [], "branches": [], "blockers": []}, open(sp, "w"))
        rc, out, err = self.cli(["run", "--decision-id", "D-M1", "--apply", "--json"], self.run)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("ui-popup\t", open(hs.hubs_tsv()).read())
        self.assertIn("ui팝업\tui", open(self.routes).read())
        self.assertIn("C0013\t11-12\t팝업 색\t진행\tui-layer", open(os.environ["LRM_LEDGER"]).read())
        self.assertEqual(len(moved), 2)
        self.assertIn(["terminal", "close", "--terminal", "term_b2"], self.run.calls)
