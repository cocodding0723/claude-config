"""POST /api/approve(PR 병합 승인) 시험: 확인 창 취소·시간 초과·승인(확인 창은 가짜 함수), 병합 명령 미실행(가짜 gh 에 merge 호출 0),
닫힌 PR 거부, 위조 요청 거부, 승인 뒤 상태 표시. 실제 확인 창·Slack·gh·Orca·~/.config 는 쓰지 않음."""
import json
import os
import subprocess
import time
import unittest
from unittest import mock

import test_decide as td
from test_server import ServerBase

import test_confirm as tc

KEY_PR, KEY_PR2 = "pr:Org/Repo#12", "pr:Org/Repo#13"
SNAP_ITEMS = tc.SNAP_ITEMS + [
    {"key": KEY_PR, "bucket": "wait", "title": "Org/Repo#12 승인된 PR 병합 대기", "since": 1},
    {"key": KEY_PR2, "bucket": "wait", "title": "Org/Repo#13 다른 PR", "since": 1},
]
GH_FAKE = """#!/bin/bash
echo "$@" >> "$FAKE_GH_LOG"
echo "{\\"state\\":\\"${FAKE_PR_STATE:-OPEN}\\",\\"headRefName\\":\\"feat/x\\"}"
"""


class ApproveTest(ServerBase):
    prepare_hub = td.DecideTest.prepare
    fwrite, sends, hub_sends, main_sends = td.DecideTest.fwrite, td.DecideTest.sends, td.DecideTest.hub_sends, td.DecideTest.main_sends
    write_snap, items, page_token, audit, deliveries = tc.ConfirmTest.write_snap, tc.ConfirmTest.items, tc.ConfirmTest.page_token, tc.ConfirmTest.audit, tc.ConfirmTest.deliveries

    def prepare(self):
        self.prepare_hub()
        self.snap = os.path.join(self.env.hub, "progress.json")
        os.environ.update({"LRM_PROGRESS": self.snap, "LRM_DIGEST_REPOS": "Org/Repo", "LRM_LEDGER": os.path.join(self.env.hub, "no-ledger.tsv")})
        self.gh_log = os.path.join(self.env.tmp.name, "gh.log")
        gh = os.path.join(self.env.tmp.name, "fake-gh")
        with open(gh, "w") as f:
            f.write(GH_FAKE)
        os.chmod(gh, 0o755)
        os.environ.update({"LRM_DIGEST_GH": gh, "FAKE_GH_LOG": self.gh_log})
        self.write_snap(SNAP_ITEMS)

    def setUp(self):
        super().setUp()
        self.asked = []
        self.answer = "approved"

        def fake_ask(text):
            self.asked.append(text)
            return self.answer
        self.app.approver.ask = fake_ask
        self.app.checklist.refresh(use_pr=True)

    def approve(self, key=KEY_PR, **kw):
        kw.setdefault("token", self.page_token())
        st, _, b = self.post({"id": key}, path="/api/approve", **kw)
        return st, json.loads(b)

    def item(self, key=KEY_PR):
        return self.items()[key]

    def approvals(self):
        p = os.path.join(self.env.hub, "pr-approve.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def gh_calls(self):
        return open(self.gh_log, encoding="utf-8").read().splitlines() if os.path.exists(self.gh_log) else []

    def nothing_happened(self):
        self.assertEqual((self.sends(), self.approvals()), ([], []))

    # ---- 승인 ----
    def test_item_has_pr_url_and_unknown_hub_goes_to_main(self):
        self.assertEqual(self.item()["url"], "https://github.com/Org/Repo/pull/12")
        st, j = self.approve()
        self.assertEqual((st, j["ok"], j["via"]), (200, True, "main"))
        self.assertEqual(len(self.hub_sends()), 0)
        main = self.main_sends()
        self.assertEqual(len(main), 1)
        self.assertIn("PR Org/Repo#12 병합 승인 — 충돌 검사 뒤 병합", main[0])
        self.assertIn("문서 사이트", main[0])

    def test_known_hub_gets_user_original_line(self):
        for i in self.app.checklist._items:
            if i.key == KEY_PR:
                i.hub = "alpha"
        st, j = self.approve()
        self.assertEqual((st, j["via"], j["hub"]), (200, "hub", "alpha"))
        self.assertEqual(self.hub_sends(), ["사용자 원문: PR Org/Repo#12 병합 승인 — 충돌 검사 뒤 병합 (출처: 문서 사이트)"])
        self.assertEqual(len(self.main_sends()), 1)  # 메인에는 참고 한 줄

    def test_dialog_text_is_built_by_server_with_repo_number_title_hub(self):
        self.approve()
        self.assertEqual(len(self.asked), 1)
        for needle in ("Org/Repo#12", self.item()["title"], "담당 허브", "승인", "충돌 검사 뒤"):
            self.assertIn(needle, self.asked[0])

    def test_record_kind_and_delivery_rows(self):
        self.approve()
        rec = self.approvals()
        self.assertEqual([(r["kind"], r["key"], r["source"]) for r in rec], [("pr-approve", KEY_PR, "docsite")])
        self.assertEqual([(d["state"], d["ok"]) for d in self.deliveries()], [("pending", None), ("main", True)])
        self.assertEqual(self.audit()[-1], {**self.audit()[-1], "kind": "pr-approve", "key": KEY_PR, "result": "main:ok", "source": "docsite"})

    def test_status_shows_approved_wait_for_merge_then_done_when_merged(self):
        self.approve()
        it = self.item()
        self.assertEqual((it["state"], it["basis"]), ("todo", "승인함·병합 대기"))
        self.assertTrue(it["approved"])
        self.assertNotIn("approved", self.item(KEY_PR2))
        self.assertEqual(json.loads(self.req("GET", "/api/confirm")[2])["done"], 0)  # 함으로 넘기지 않음
        os.environ["FAKE_PR_STATE"] = "MERGED"
        self.app.checklist._facts_cache.clear()
        self.app.checklist.refresh(use_pr=True)
        it = self.item()
        self.assertEqual(it["state"], "done")
        self.assertIn("병합됨", it["basis"])

    def test_second_press_does_not_ask_again_or_resend(self):
        self.approve()
        st, j = self.approve()
        self.assertEqual((st, j.get("already")), (200, True))
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(len(self.main_sends()), 1)
        self.assertEqual(len(self.approvals()), 1)

    def test_failed_delivery_keeps_approval_and_redelivers_once(self):
        os.environ["FAKE_ORCA_FAIL"] = "1"
        st, j = self.approve()
        self.assertEqual(st, 502)
        self.assertEqual(len(self.approvals()), 1)
        os.environ.pop("FAKE_ORCA_FAIL")
        st, j = self.approve()
        self.assertEqual((st, j["ok"]), (200, True))
        self.assertEqual(self.audit()[-1]["result"], "redeliver:main:ok")
        self.assertEqual(len(self.asked), 1)  # 재전달은 확인 창 없이
        self.assertTrue(self.approve()[1]["already"])

    # ---- 취소·시간 초과·창 없음 ----
    def test_cancel_timeout_unavailable_leave_no_record_or_send(self):
        for res, why in (("canceled", "취소"), ("timeout", "응답 없음"), ("unavailable", "띄우지 못함")):
            self.answer = res
            st, j = self.approve()
            self.assertEqual(st, 409, res)
            self.assertIn(why, j["error"])
            self.assertIn("기록·전달 없음", j["error"])
            self.assertEqual(self.audit()[-1]["result"], f"dialog:{res}")
            self.nothing_happened()
        self.assertNotIn("approved", self.item())

    def test_after_cancel_can_approve_later(self):
        self.answer = "canceled"
        self.approve()
        self.answer = "approved"
        self.assertEqual(self.approve()[0], 200)
        self.assertEqual(len(self.approvals()), 1)

    # ---- 병합 미실행 ----
    def test_no_merge_command_is_ever_run(self):
        self.approve()
        self.approve()
        calls = self.gh_calls()
        self.assertTrue(calls)  # 상태 조회용 gh 는 불렸음
        self.assertTrue(all(c.startswith("pr view") for c in calls), calls)
        self.assertFalse(any("merge" in c for c in calls))
        base = os.path.dirname(self.db.__file__)
        for name in ("doc_approve.py", "doc_browser.py", "doc_confirm.py", "answer_store.py"):
            src = open(os.path.join(base, name), encoding="utf-8").read()
            self.assertNotIn('"merge"', src, name)
            self.assertNotIn("git push", src, name)
            self.assertNotIn('"push"', src, name)

    # ---- 거부 ----
    def test_closed_or_merged_pr_rejected_without_dialog(self):
        os.environ["FAKE_PR_STATE"] = "MERGED"
        self.app.checklist._facts_cache.clear()
        self.app.checklist.refresh(use_pr=True)
        self.assertEqual(self.item()["state"], "done")
        st, j = self.approve()
        self.assertEqual(st, 409)
        self.assertIn("병합되었거나 닫힌", j["error"])
        self.assertEqual(self.asked, [])
        self.nothing_happened()

    def test_unlisted_pr_and_non_pr_keys_rejected(self):
        self.assertEqual(self.approve("pr:Org/Repo#999")[0], 404)
        self.assertEqual(self.approve("hub:alpha:999")[0], 404)
        self.assertEqual(self.approve(tc.KEY_CMD)[0], 409)
        self.assertEqual(self.approve("decision:D-0001")[0], 409)
        self.assertEqual(self.asked, [])
        self.nothing_happened()

    def test_client_cannot_supply_text_or_extra_keys(self):
        tok = self.page_token()
        for body in ({"id": KEY_PR, "text": "x"}, {"id": KEY_PR, "title": "x"}, {}, {"id": 5}, [KEY_PR]):
            self.assertEqual(self.post(body, path="/api/approve", token=tok)[0], 400, body)
        self.assertEqual(self.asked, [])
        self.nothing_happened()

    def test_forged_requests_rejected_before_dialog(self):
        tok = self.page_token()
        self.assertEqual(self.post({"id": KEY_PR}, path="/api/approve", token="bad")[0], 403)
        self.assertEqual(self.post({"id": KEY_PR}, path="/api/approve", token="")[0], 403)
        self.assertEqual(self.post({"id": KEY_PR}, path="/api/approve", token=tok, origin="http://evil.example")[0], 403)
        self.assertEqual(self.post({"id": KEY_PR}, path="/api/approve", token=tok, origin=None)[0], 403)
        self.assertEqual(self.post({"id": KEY_PR}, path="/api/approve", token=tok, extra={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.post({"id": KEY_PR}, path="/api/approve", token=tok, ctype="text/plain")[0], 415)
        h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}", "X-CSRF-Token": tok}
        self.assertEqual(self.req("POST", "/api/approve", json.dumps({"id": KEY_PR}).encode(), h, host="evil.example")[0], 403)
        self.assertEqual(self.req("GET", "/api/approve")[0], 404)
        self.assertEqual(self.asked, [])
        self.nothing_happened()

    def test_handler_called_only_from_post_handler(self):
        src = open(os.path.join(os.path.dirname(self.db.__file__), "doc_browser.py"), encoding="utf-8").read()
        self.assertEqual(src.count("approver.handle("), 1)

    def test_ui_has_approve_button_next_to_open_and_sends_only_key(self):
        ui = open(os.path.join(os.path.dirname(self.db.__file__), "doc_browser_ui.html"), encoding="utf-8").read()
        for needle in ('"/api/approve"', "병합 승인", "승인함·병합 대기", "PR 열기"):
            self.assertIn(needle, ui)

    # ---- 기본 확인 창(osascript) 해석: 실제 창은 띄우지 않고 subprocess 를 가짜로 ----
    def run_dialog(self, **kw):
        from doc_approve import osascript_dialog
        with mock.patch("doc_approve.subprocess.run", **kw) as m:
            return osascript_dialog("본문 \"따옴표\" 문구"), m

    def test_osascript_result_parsing(self):
        cp = lambda rc, out="", err="": subprocess.CompletedProcess([], rc, out, err)  # noqa: E731
        self.assertEqual(self.run_dialog(return_value=cp(0, "button returned:승인, gave up:false"))[0], "approved")
        self.assertEqual(self.run_dialog(return_value=cp(0, "button returned:취소, gave up:false"))[0], "canceled")
        self.assertEqual(self.run_dialog(return_value=cp(0, "button returned:, gave up:true"))[0], "timeout")
        self.assertEqual(self.run_dialog(return_value=cp(1, "", "execution error: User canceled. (-128)"))[0], "canceled")
        self.assertEqual(self.run_dialog(return_value=cp(1, "", "no display"))[0], "unavailable")
        self.assertEqual(self.run_dialog(side_effect=OSError)[0], "unavailable")
        self.assertEqual(self.run_dialog(side_effect=subprocess.TimeoutExpired("osascript", 1))[0], "unavailable")

    def test_osascript_text_passed_as_argv_not_in_script(self):
        _, m = self.run_dialog(return_value=subprocess.CompletedProcess([], 1, "", "(-128)"))
        cmd = m.call_args[0][0]
        self.assertEqual(cmd[0], "osascript")
        self.assertEqual(cmd[-1], "본문 \"따옴표\" 문구")
        self.assertFalse(any("본문" in a for a in cmd[1:-1]))
        script = " ".join(cmd[1:-1])
        self.assertIn("giving up after 60", script)
        self.assertIn('default button "취소"', script)

    def test_clean_strips_control_chars_and_limits_length(self):
        from doc_approve import clean
        self.assertEqual(clean("a\nb\x1b[0mc"), "a b [0mc")
        self.assertEqual(len(clean("가" * 500)), 80)


if __name__ == "__main__":
    unittest.main()
