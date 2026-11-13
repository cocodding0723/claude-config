"""확인 목록 API 시험: GET /api/confirm 항목 집합, POST /api/confirm('했음') 보안·전달·중복 방지·감사 로그.
허브·메인 전송은 가짜 orca(tests/stall_watch/fake_orca.py). 실제 Orca·Slack·gh·~/.config 는 쓰지 않음(PR 조회 끔)."""
import json
import os
import time
import unittest

import test_decide as td
from test_server import ServerBase

KEY_CMD, KEY_WIN, KEY_SHEET = "hub:alpha:1", "hub:alpha:3", "hub:alpha:2"
SNAP_ITEMS = [
    {"key": "decision:D-0001", "bucket": "wait", "title": "결정 D-0001 시험 결정", "since": 1},
    {"key": KEY_CMD, "bucket": "wait", "title": "alpha: 직접 실행 `bash run.sh` 필요", "since": 0},
    {"key": KEY_SHEET, "bucket": "block", "title": "alpha: 구글 시트 직접 입력 대기", "since": 0},
    {"key": KEY_WIN, "bucket": "wait", "title": "alpha: 창 직접 승인 대기", "since": 0},
]


class ConfirmTest(ServerBase):
    prepare_hub = td.DecideTest.prepare
    fwrite, sends, hub_sends, main_sends = td.DecideTest.fwrite, td.DecideTest.sends, td.DecideTest.hub_sends, td.DecideTest.main_sends

    def prepare(self):
        self.prepare_hub()
        self.snap = os.path.join(self.env.hub, "progress.json")
        os.environ.update({"LRM_PROGRESS": self.snap, "LRM_DIGEST_REPOS": "Org/Repo", "LRM_DIGEST_GH": os.path.join(self.env.tmp.name, "no-gh"),
                           "LRM_LEDGER": os.path.join(self.env.hub, "no-ledger.tsv")})
        self.write_snap(SNAP_ITEMS)

    def write_snap(self, items):
        with open(self.snap, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "updatedAt": time.time(), "items": items, "sources": {}, "notes": [], "counts": {}, "groups": {}}, f)

    def setUp(self):
        super().setUp()
        self.app.checklist.refresh(use_pr=False)

    def items(self):
        return {i["key"]: i for i in json.loads(self.req("GET", "/api/confirm")[2])["items"]}

    def confirm(self, key, **kw):
        kw.setdefault("token", self.page_token())
        st, _, b = self.post({"id": key}, path="/api/confirm", **kw)
        return st, json.loads(b)

    def page_token(self):
        if not hasattr(self, "_t"):
            self._t = self.token()
        return self._t

    def audit(self):
        p = os.path.join(self.env.hub, "docsite-audit.jsonl")
        with open(p, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    # ---- 읽기 ----
    def test_get_lists_all_kinds_with_state_and_confirmable_flag(self):
        j = json.loads(self.req("GET", "/api/confirm")[2])
        by = {i["key"]: i for i in j["items"]}
        self.assertEqual({k: v["kind"] for k, v in by.items()}, {"decision:D-0001": "decision", KEY_CMD: "command", KEY_SHEET: "sheet", KEY_WIN: "window"})
        self.assertEqual((j["todo"], j["done"]), (4, 0))
        self.assertEqual(by[KEY_CMD]["command"], "bash run.sh")
        self.assertTrue(by[KEY_WIN]["confirmable"] and not by["decision:D-0001"]["confirmable"])
        self.assertEqual(j["items"][0]["kind"], "decision")  # 종류 순서

    def test_related_docs_link_by_decision_id(self):
        self.env.write(self.env.docs, "dec.html", "<!doctype html><title>결정 문서</title><h1>결정 문서</h1>D-0001")
        self.app.index.refresh()
        self.assertIsInstance(self.items()["decision:D-0001"]["docs"], list)

    # ---- 쓰기 ----
    def test_confirm_records_and_tells_hub_with_user_original_line(self):
        st, j = self.confirm(KEY_CMD)
        self.assertEqual((st, j["ok"], j["via"]), (200, True, "hub"))
        self.assertEqual(self.hub_sends(), ["사용자 원문: 확인 완료: " + self.items()[KEY_CMD]["title"] + " (직접 실행 명령) (출처: 문서 사이트)"])
        self.assertEqual(len(self.main_sends()), 1)
        it = self.items()[KEY_CMD]
        self.assertEqual(it["state"], "done")
        self.assertIn("사용자 확인 — 문서 사이트", it["basis"])
        with open(os.path.join(self.env.hub, "confirm-log.jsonl"), encoding="utf-8") as f:
            self.assertEqual([json.loads(x)["key"] for x in f], [KEY_CMD])

    def test_second_press_does_not_resend(self):
        self.confirm(KEY_CMD)
        st, j = self.confirm(KEY_CMD)
        self.assertEqual((st, j.get("already")), (200, True))
        self.assertEqual(len(self.hub_sends()), 1)

    def test_decision_and_unknown_keys_rejected_without_send(self):
        self.assertEqual(self.confirm("decision:D-0001")[0], 409)
        self.assertEqual(self.confirm("hub:alpha:999")[0], 404)
        self.assertEqual(self.sends(), [])
        self.assertFalse(os.path.exists(os.path.join(self.env.hub, "confirm-log.jsonl")))

    def test_client_cannot_supply_text_or_extra_keys(self):
        tok = self.page_token()
        for body in ({"id": KEY_CMD, "text": "x"}, {}, {"id": 5}, [KEY_CMD]):
            st, _, _ = self.post(body, path="/api/confirm", token=tok)
            self.assertEqual(st, 400, body)
        self.assertEqual(self.sends(), [])

    def test_hub_failure_falls_back_to_main_and_keeps_record(self):
        os.environ["FAKE_SEND_FAIL"] = f"{td.HUBH}:terminal_handle_stale"
        st, j = self.confirm(KEY_WIN)
        self.assertEqual((st, j["via"]), (200, "proxy"))
        self.assertEqual(self.items()[KEY_WIN]["state"], "done")

    def test_audit_has_key_and_result_without_text(self):
        self.confirm(KEY_CMD)
        rec = self.audit()[-1]
        self.assertEqual((rec["source"], rec["kind"], rec["key"], rec["result"]), ("docsite", "confirm", KEY_CMD, "hub:ok"))
        self.assertNotIn("사용자 원문", json.dumps(self.audit(), ensure_ascii=False))

    def test_csrf_origin_host_and_fetch_site_checks_apply(self):
        tok = self.page_token()
        self.assertEqual(self.post({"id": KEY_CMD}, path="/api/confirm", token="bad")[0], 403)
        self.assertEqual(self.post({"id": KEY_CMD}, path="/api/confirm", token=tok, origin="http://evil.example")[0], 403)
        self.assertEqual(self.post({"id": KEY_CMD}, path="/api/confirm", token=tok, extra={"Sec-Fetch-Site": "cross-site"})[0], 403)
        h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}", "X-CSRF-Token": tok}
        self.assertEqual(self.req("POST", "/api/confirm", json.dumps({"id": KEY_CMD}).encode(), h, host="evil.example")[0], 403)
        self.assertEqual(self.sends(), [])

    def test_confirm_handler_called_only_from_post_handler(self):
        src = open(os.path.join(os.path.dirname(self.db.__file__), "doc_browser.py"), encoding="utf-8").read()
        self.assertEqual(src.count("checklist.handle("), 1)


if __name__ == "__main__":
    unittest.main()
