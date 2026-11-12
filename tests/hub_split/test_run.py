import json
import os
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

from fixture import Base, FakeOrca, hs


def term(path, handle):
    return {"handle": handle, "worktreePath": path, "agentIdentity": "claude", "connected": True, "writable": True, "title": "hub"}


def ok_send(stage="turn_started"):
    return 0, {"ok": True, "result": {"send": {"accepted": True, "prompt": {"stages": ["input_accepted", stage]}}}}


def iso(hours_ago=0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def wr(p, text, mode="w"):
    with open(p, mode, encoding="utf-8") as f:
        f.write(text)


class RunBase(Base):
    def setUp(self):
        super().setUp()
        self.routes = os.path.join(self.d, "intake-routes.tsv")
        wr(self.routes, "# 머리말\t대상\nui\tui\nvfx\tvfx\n*\tmain\n")
        wr(os.path.join(self.d, "intake-routes.jsonl"), "".join(json.dumps({"id": i, "hub": "ui", "text": t, "at": 1}) + "\n"
                                                              for i, t in (("C0012", "팝업 닫기 버튼 정렬"), ("C0014", "팝업 그림자"))))
        self.ledger = os.environ["LRM_LEDGER"]
        wr(self.ledger, "id\t접수\t요청(원문 요약)\t상태\t담당\t진행·증거\t갱신\n"
                        "C0012\t11-12\t팝업 정렬\t진행\tui\t\t2026-11-12 11:00\n"
                        "C0014\t11-12\t팝업 그림자\t진행\tui\t\t2026-11-12 11:00\n"
                        "C0013\t11-12\t팝업 색\t진행\tui-popup\t\t2026-11-12 11:00\n")
        self.role = os.path.join(self.d, "role-ui-popup.txt")
        wr(self.role, "팝업 역할")
        self.create = os.path.join(self.d, "create_hub.sh")
        wr(self.create, '#!/bin/bash\necho "$@" >> "$(dirname "$0")/create.calls"\nexit "${FAKE_CREATE_RC:-0}"\n')
        os.environ["LRM_CREATE_HUB"] = self.create
        os.environ["LRM_HUB_STATUS_DIR"] = os.path.join(self.d, "status")
        os.makedirs(os.environ["LRM_HUB_STATUS_DIR"])
        self.sent, self.send_results = [], []
        self.make_orca({"ui-layer": 10, "vfx": 1})
        self.hubs_before = rd(hs.hubs_tsv())
        self.routes_before = rd(self.routes)

    def tearDown(self):
        for k in ("LRM_CREATE_HUB", "LRM_HUB_STATUS_DIR", "FAKE_CREATE_RC"):
            os.environ.pop(k, None)
        super().tearDown()

    def make_orca(self, kids):
        self.run = FakeOrca(self.root, kids)
        hubp = lambda s: os.path.join(self.root, f"coord-{s}-hub")  # noqa: E731
        self.run.replies["terminal list"] = (0, {"ok": True, "result": {"terminals": [term(hubp("ui-layer"), "term_a1"), term(hubp("ui-popup"), "term_b2")]}})

        def send(args):
            self.sent.append((args[args.index("--terminal") + 1], args[args.index("--text") + 1]))
            return self.send_results.pop(0) if self.send_results else ok_send()
        self.run.replies["terminal send"] = send
        self.run.replies["terminal show"] = (1, None)  # 화면 확인 불가 → 불확실로 남김

    def approve(self, did, pick=1, at=None, type_="button", action="pick", **extra):
        wr(os.environ["LRM_SLACK_INBOX"], json.dumps({"type": type_, "decision_id": did, "action": action, "pick": pick,
                                                      "received_at": at if at is not None else time.time() + 1, **extra}) + "\n", "a")

    def status(self, slug, workers, hours_ago=0, **extra):
        data = {"slug": slug, "updatedAt": iso(hours_ago), "tasks": [], "branches": [], "blockers": [], "workers": workers, **extra}
        if workers is None:
            data.pop("workers")
        wr(os.path.join(os.environ["LRM_HUB_STATUS_DIR"], f"{slug}.json"), json.dumps(data))


class Split(RunBase):
    MOVES = ["--move", "C0012", "--move", "C0014"]

    def propose(self, approve=True):
        rc, _, err = self.cli(["propose", "--kind", "split", "--hub", "ui-layer", "--child", "ui-popup", "--child-name", "UI 팝업",
                               "--prefix", "ui팝업", "--decision-id", "D-S1", "--no-doc", "--record"], self.run)
        self.assertEqual(rc, 0, err)
        if approve:
            self.approve("D-S1")

    def go(self, *extra, apply=True, moves=None):
        args = ["run", "--decision-id", "D-S1", *(self.MOVES if moves is None else moves), "--role-file", self.role, "--json", *extra]
        return self.cli(args + (["--apply"] if apply else []), self.run)

    def ready(self):
        self.propose()
        rc, _, err = self.go(apply=False)
        self.assertEqual(rc, 0, err)
        self.sent.clear()

    def test_dry_run_default_changes_nothing(self):
        rc, out, err = self.cli(["run", "--decision-id", "D-T1", "--kind", "split", "--hub", "ui-layer", "--child", "ui-popup",
                                 "--child-name", "UI 팝업", "--prefix", "ui팝업", "--move", "C0012", "--json"], self.run)
        self.assertEqual(rc, 0, err)
        self.assertEqual([s["status"] for s in json.loads(out)["steps"]], ["plan"] * 10)
        self.assertEqual((rd(hs.hubs_tsv()), rd(self.routes)), (self.hubs_before, self.routes_before))
        self.assertFalse(os.path.exists(os.environ["LRM_REPORT_OUTBOX"]))
        self.assertEqual(self.sent, [])
        self.assertFalse(any(c[:2] in (["worktree", "set"], ["terminal", "close"]) for c in self.run.calls))

    # 1) 승인
    def test_apply_refused_without_card_approval(self):
        self.propose(approve=False)
        self.go(apply=False)
        for pick in (None, 2):
            if pick:
                self.approve("D-S1", pick)
            rc, _, err = self.go()
            self.assertEqual(rc, 4, err)
            self.assertIn("승인 확인 실패", err)
        self.assertEqual(rd(hs.hubs_tsv()), self.hubs_before)

    def test_answer_before_proposal_ignored(self):
        self.propose(approve=False)
        self.approve("D-S1", 1, at=1)
        self.go(apply=False)
        self.assertEqual(self.go()[0], 4)

    def test_apply_needs_same_input_dry_run(self):
        self.propose()
        rc, _, err = self.go()
        self.assertEqual(rc, 4)
        self.assertIn("시험 실행 기록 없음", err)
        self.go(apply=False, moves=["--move", "C0012"])
        rc, _, err = self.go()  # 시험과 --move 가 다름
        self.assertEqual(rc, 4)

    def test_changed_plan_after_proposal_refused(self):
        self.ready()
        st = json.loads(rd(hs.state_path()))
        st["proposals"]["D-S1"]["prefix"] = "바뀜"
        wr(hs.state_path(), json.dumps(st))
        rc, _, err = self.go()
        self.assertEqual(rc, 4)
        self.assertIn("해시 불일치", err)

    def test_split_apply_full_order(self):
        self.ready()
        rc, out, err = self.go()
        self.assertEqual(rc, 0, err)
        self.assertEqual([s["status"] for s in json.loads(out)["steps"]], ["done"] * 10)
        self.assertEqual({h.slug: h for h in hs.read_hubs()}["ui-popup"].parent, "ui-layer")
        routes = rd(self.routes).splitlines()
        self.assertLess(routes.index("ui팝업\tui-popup"), routes.index("*\tmain"))
        self.assertEqual(self.sent[:2], [("term_b2", "[C0012] 팝업 닫기 버튼 정렬"), ("term_b2", "[C0014] 팝업 그림자")])
        self.assertIn("C0012\t11-12\t팝업 정렬\t진행\tui-popup", rd(self.ledger))

    # 4) 보상
    def test_create_failure_restores_hubs_table(self):
        self.ready()
        os.environ["FAKE_CREATE_RC"] = "1"
        rc, out, err = self.go()
        self.assertEqual(rc, 4)
        self.assertIn("hubs: ui-popup 행만 되돌림(대조 일치)", err)
        self.assertEqual(rd(hs.hubs_tsv()), self.hubs_before)
        os.environ["FAKE_CREATE_RC"] = "0"
        self.assertEqual(self.go()[0], 0)

    def test_ledger_failure_reverts_owner_closes_child_and_restores_table(self):
        self.ready()
        real = hs.py_tool

        def flaky(var, name, *args):
            if args[:2] == ("owner", "C0014") and args[-1] == "ui-popup":
                return real(var, name, "owner", "NOPE", "--to", "x")
            return real(var, name, *args)
        with mock.patch.object(hs, "py_tool", flaky):
            rc, out, err = self.go()
        self.assertEqual(rc, 1, err)
        self.assertIn("장부 담당 1건 되돌림(대조 일치)", err)
        self.assertIn("C0012\t11-12\t팝업 정렬\t진행\tui\t", rd(self.ledger))
        self.assertEqual(rd(hs.hubs_tsv()), self.hubs_before)
        self.assertIn(["terminal", "close", "--terminal", "term_b2"], self.run.calls)
        prog = json.loads(rd(os.path.join(self.d, "hub-split-runs", "D-S1", "progress.json")))
        self.assertEqual((prog["done"], prog["ledger"], prog["sent"]), (["backup", "report_before", "pending"], [], {}))

    def test_hubs_undo_keeps_rows_added_between_runs(self):
        self.ready()
        os.environ["FAKE_CREATE_RC"] = "1"
        self.assertEqual(self.go()[0], 4)
        wr(hs.hubs_tsv(), "audio\t오디오\t~/r/role-audio.txt\t-\t-\n", "a")
        self.assertEqual(self.go()[0], 4)
        slugs = [h.slug for h in hs.read_hubs()]
        self.assertIn("audio", slugs)
        self.assertNotIn("ui-popup", slugs)

    def test_routes_undo_keeps_other_routes(self):
        self.ready()
        orig = hs.Runner.routes

        def broken(self_):
            orig(self_)
            wr(self.routes, "sfx\taudio\n", "a")
            raise hs.SplitError("주입", 1)
        with mock.patch.object(hs.Runner, "routes", broken):
            self.assertEqual(self.go()[0], 1)
        text = rd(self.routes)
        self.assertIn("sfx\taudio", text)
        self.assertNotIn("ui팝업", text)

    # 4) 버튼·반응·글 답 해석 재사용
    def test_reaction_and_reply_answers(self):
        self.propose(approve=False)
        self.go(apply=False)
        cases = [({"type_": "thread_reply", "action": "free", "pick": None}, 4),
                 ({"type_": "reaction", "action": "hold", "pick": None}, 4),
                 ({"type_": "thread_reply", "action": "approve", "pick": 1, "conflict": True, "choices": ["1", "hold"]}, 4),
                 ({"type_": "reaction", "action": "approve", "pick": 2}, 4),
                 ({"type_": "thread_reply", "action": "approve", "pick": 1}, 0)]
        for kw, want in cases:
            self.approve("D-S1", **kw)
            rc, _, err = self.go()
            self.assertEqual(rc, want, (kw, err))

    def test_custom_input_after_approval_refused(self):
        self.propose()  # 버튼 승인
        self.go(apply=False)
        self.approve("D-S1", pick=None, action="custom", text="보류")  # 뒤이어 '직접 입력' 버튼으로 보류
        rc, _, err = self.go()
        self.assertEqual(rc, 4, err)
        self.assertIn("직접 입력", err)
        self.assertEqual(rd(hs.hubs_tsv()), self.hubs_before)

    def test_routes_failure_restores_routes(self):
        self.ready()
        orig = hs.Runner.routes

        def broken(self_):
            orig(self_)
            raise hs.SplitError("주입 실패", 1)
        with mock.patch.object(hs.Runner, "routes", broken):
            rc, _, err = self.go()
        self.assertEqual(rc, 1)
        self.assertEqual(rd(self.routes), self.routes_before)

    # 5) 재하달 중복 방지
    def test_redeliver_records_each_task_and_never_resends(self):
        self.ready()
        self.send_results = [ok_send(), ok_send("input_accepted")]  # 둘째는 제출 미확인
        rc, _, err = self.go()
        self.assertEqual(rc, 4)
        self.assertIn("--mark-sent C0014", err)
        rc, _, err = self.go()  # 불확실 건은 사람 확인 전 멈춤, 첫 건 재전송 없음
        self.assertEqual(rc, 4)
        rc, _, err = self.go("--mark-sent", "C0014")
        self.assertEqual(rc, 0, err)
        task_sends = [t for _, t in self.sent if t.startswith("[C00")]
        self.assertEqual(task_sends, ["[C0012] 팝업 닫기 버튼 정렬", "[C0014] 팝업 그림자"])

    def test_resend_after_screen_check(self):
        self.ready()
        self.send_results = [ok_send(), ok_send("input_accepted")]
        self.go()
        self.assertEqual(self.go("--resend", "C0014")[0], 0)
        self.assertEqual([t for _, t in self.sent if t.startswith("[C0014]")], ["[C0014] 팝업 그림자"] * 2)
        self.assertEqual([t for _, t in self.sent if t.startswith("[C0012]")], ["[C0012] 팝업 닫기 버튼 정렬"])

    def test_rerun_with_different_input_refused(self):
        self.ready()
        os.environ["FAKE_CREATE_RC"] = "1"
        self.go()
        os.environ["FAKE_CREATE_RC"] = "0"
        rc, _, err = self.go(moves=["--move", "C0012"])
        self.assertEqual(rc, 4)
        self.assertIn("첫 실행과 다름", err)


class Merge(RunBase):
    def setUp(self):
        super().setUp()
        self.write_hubs(self.HUBS + [("ui-popup", "팝업", "~/r/role-ui-popup.txt", "-", "-", "ui-layer")])
        wr(self.routes, "ui팝업\tui-popup\n", "a")
        self.hubs_before = rd(hs.hubs_tsv())
        self.make_orca({"ui-layer": 2, "ui-popup": 2})
        self.moved = []

        def wset(args):
            self.moved.append(args[3][5:])
            for w in self.run.worktrees:
                if w["id"] == "ui-popup":
                    w["childWorktreeIds"] = [i for i in w["childWorktreeIds"] if os.path.join(self.root, i) not in self.moved]
            return 0, {"ok": True}
        self.run.replies["worktree set"] = wset
        self.run.replies["orchestration worker-list"] = (0, {"ok": True, "result": {"workers": []}})
        self.close_rc = 0

        def close(args):
            if self.close_rc:
                return self.close_rc, None
            terms = self.run.replies["terminal list"][1]["result"]["terminals"]
            terms[:] = [t for t in terms if t["handle"] != args[3]]
            return 0, {"ok": True}
        self.run.replies["terminal close"] = close
        rc, _, err = self.cli(["propose", "--kind", "merge", "--child", "ui-popup", "--decision-id", "D-M1", "--no-doc", "--record"], self.run)
        self.assertEqual(rc, 0, err)
        self.approve("D-M1")
        self.status("ui-popup", [])
        self.assertEqual(self.go(apply=False)[0], 0)

    def go(self, apply=True):
        return self.cli(["run", "--decision-id", "D-M1", "--json"] + (["--apply"] if apply else []), self.run)

    # 2) 진행 워커 판정
    def test_empty_stale_or_busy_status_refused(self):
        for kw in ({"workers": None}, {"workers": [], "hours_ago": 30}, {"workers": ["ctx_1 /ws/a"]}):
            self.status("ui-popup", kw["workers"], kw.get("hours_ago", 0))
            rc, _, err = self.go()
            self.assertEqual(rc, 4, err)
        wr(os.path.join(os.environ["LRM_HUB_STATUS_DIR"], "ui-popup.json"), "{}")
        self.assertEqual(self.go()[0], 4)
        self.assertIn("ui-popup", rd(hs.hubs_tsv()))

    def test_idle_rechecked_on_rerun_and_before_close(self):
        self.close_rc = 1
        rc, _, err = self.go()
        self.assertEqual(rc, 3, err)  # 종료 실패
        self.status("ui-popup", ["ctx_9 /ws/b"])  # 그사이 워커가 생김
        rc, out, err = self.go()
        self.assertEqual(rc, 4)
        self.assertEqual(json.loads(out)["steps"][2]["status"], "failed")  # 재실행에도 idle_check 다시 함

    # 6) 불변 계획으로 재개
    def test_resume_after_row_removed_and_close_failed(self):
        self.close_rc = 1
        rc, _, err = self.go()
        self.assertEqual(rc, 3, err)
        self.assertNotIn("ui-popup\t", rd(hs.hubs_tsv()))
        self.close_rc = 0
        rc, out, err = self.go()
        self.assertEqual(rc, 0, err)
        st = {s["name"]: s["status"] for s in json.loads(out)["steps"]}
        self.assertEqual((st["remove_row"], st["relink_all"], st["close_child"]), ("skip", "skip", "done"))
        self.assertIn("ui팝업\tui", rd(self.routes))
        self.assertIn("C0013\t11-12\t팝업 색\t진행\tui-layer", rd(self.ledger))
        self.assertEqual(len(self.moved), 2)

    def test_status_says_idle_but_orca_has_live_worker(self):
        kid = os.path.join(self.root, "ui-popup-k0")
        self.run.replies["orchestration worker-list"] = (0, {"ok": True, "result": {"workers": [
            {"terminalState": "running", "resource": {"worktreeId": f"repo::{kid}"}}]}})
        rc, _, err = self.go()
        self.assertEqual(rc, 4)
        self.assertIn("살아 있는 터미널·워커", err)
        self.run.replies["orchestration worker-list"] = (1, None)
        self.assertEqual(self.go()[0], 4)  # 조회 실패도 거부

    def test_resume_after_close_with_stale_status(self):
        self.run.replies["terminal send"] = (1, None)  # 부모 전송 실패 → parent_status 에서 멈춤
        rc, out, err = self.go()
        self.assertEqual(rc, 4, err)
        self.assertEqual({s["name"]: s["status"] for s in json.loads(out)["steps"]}["close_child"], "done")
        self.status("ui-popup", [], hours_ago=30)  # 다음 날: 하위 허브가 없어 상태 파일이 낡음
        self.run.replies["terminal send"] = lambda a: ok_send()
        rc, out, err = self.go()
        self.assertEqual(rc, 0, err)
        self.assertIn("하위 허브 종료 뒤 Orca 실제 상태", out)

    def test_remove_row_failure_keeps_other_rows(self):
        orig = hs.Runner.remove_row

        def broken(self_):
            orig(self_)
            wr(hs.hubs_tsv(), "audio\t오디오\t~/r/role-audio.txt\t-\t-\n", "a")  # 그사이 다른 허브 추가
            raise hs.SplitError("주입", 1)
        with mock.patch.object(hs.Runner, "remove_row", broken):
            self.assertEqual(self.go()[0], 1)
        slugs = [h.slug for h in hs.read_hubs()]
        self.assertIn("ui-popup", slugs)
        self.assertIn("audio", slugs)

    def test_remove_row_failure_restores_table(self):
        orig = hs.Runner.remove_row

        def broken(self_):
            orig(self_)
            raise hs.SplitError("주입", 1)
        with mock.patch.object(hs.Runner, "remove_row", broken):
            self.assertEqual(self.go()[0], 1)
        self.assertEqual(rd(hs.hubs_tsv()), self.hubs_before)


class Relink(RunBase):
    # 3) PR 상태와 별개로 진행 작업자 확인
    def setUp(self):
        super().setUp()
        self.write_hubs(self.HUBS + [("ui-popup", "팝업", "~/r/role-ui-popup.txt", "-", "-", "ui-layer")])
        st = {"proposals": {"D-S1": {"kind": "split", "parent": "ui-layer", "child": "ui-popup", "child_name": "", "prefix": "", "at": 0}}}
        wr(hs.state_path(), json.dumps(st))
        self.ws = os.path.join(self.root, "ui-layer-k0")

    def relink(self):
        rc, out, err = self.cli(["relink", "--decision-id", "D-S1", "--ws", self.ws, "--apply"], self.run)
        self.assertEqual(rc, 0, err)
        return json.loads(out)["items"][0]

    def test_live_terminal_keeps_workspace(self):
        self.run.replies["terminal list"] = (0, {"ok": True, "result": {"terminals": [term(self.ws, "term_w")]}})
        self.assertEqual(self.relink()["action"], "keep")

    def test_live_dispatch_keeps_workspace(self):
        self.run.replies["terminal list"] = (0, {"ok": True, "result": {"terminals": []}})
        self.run.replies["orchestration worker-list"] = (0, {"ok": True, "result": {"workers": [
            {"terminalState": "running", "resource": {"worktreeId": f"repo::{self.ws}"}}]}})
        self.assertEqual(self.relink()["action"], "keep")

    def test_lookup_failure_keeps_workspace(self):
        self.run.replies["terminal list"] = (1, None)
        r = self.relink()
        self.assertEqual(r["action"], "keep")
        self.assertIn("판정 불가", r["why"])
        self.assertFalse(any(c[:2] == ["worktree", "set"] for c in self.run.calls))
