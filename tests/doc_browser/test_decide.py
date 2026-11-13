"""POST /api/decide 시험: 선택 번호만 받는 확인(preview) → 전달(commit) 흐름, 위조 요청 거부, 이미 답한 결정 거부, 감사 로그.
허브·메인 전송은 가짜 orca(tests/stall_watch/fake_orca.py)이며 실제 Orca·Slack·터미널·~/.config 는 쓰지 않음."""
import json
import os
import shutil
import sys
import threading
import unittest

from test_server import ServerBase

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_ORCA = os.path.join(HERE, "..", "stall_watch", "fake_orca.py")
MAINH = "term_0000aaaa-0000-4000-8000-000000000001"
HUBH = "term_0000bbbb-0000-4000-8000-000000000002"
DID = "D-0001"
SAY = f"사용자 원문: 결정 {DID} = 선택 2(B 안) (버튼) (출처: 문서 사이트)"


class DecideTest(ServerBase):
    def prepare(self):
        self.fake = os.path.join(self.env.tmp.name, "fake")
        bin_dir = os.path.join(self.env.tmp.name, "bin")
        os.makedirs(self.fake)
        os.makedirs(bin_dir)
        shutil.copy(FAKE_ORCA, os.path.join(bin_dir, "orca"))
        os.chmod(os.path.join(bin_dir, "orca"), 0o755)
        os.environ.update({"LRM_ORCA": os.path.join(bin_dir, "orca"), "FAKE_DIR": self.fake, "LRM_MAIN_ENV": os.path.join(self.env.hub, "main.env"),
                           "HUB_PROC_TABLE": os.path.join(self.env.tmp.name, "no-proc-table")})
        for name in ("workers_retained", "workers_active"):
            self.fwrite(name + ".json", [])
        self.fwrite("terminals.json", [{"handle": HUBH, "title": "알파", "worktreePath": "/x/coord-alpha-hub", "agentIdentity": "claude",
                                        "connected": True, "writable": True, "orphaned": False, "lastOutputAt": 1}])
        with open(os.path.join(self.env.hub, "main.env"), "w", encoding="utf-8") as f:
            f.write(f"MAIN_TERMINAL={MAINH}\n")
        with open(os.path.join(self.env.hub, "hubs.tsv"), "w", encoding="utf-8") as f:
            f.write("alpha\t알파 허브\t-\t-\t-\n")
        self.env.jsonl("hub-reports.jsonl", [{"decision_id": DID, "hub": "alpha"}])
        self.env.jsonl("slack-sent.jsonl", [{"ts": "1.1", "channel": "C1", "title": "시험 결정", "form_kind": "decide", "decision_id": DID,
                                             "options": {"1": "A 안", "2": "B 안"}, "recommended": 1, "tag": "alpha"}])

    def fwrite(self, name, obj):
        with open(os.path.join(self.fake, name), "w", encoding="utf-8") as f:
            json.dump(obj, f)

    # ---- 도구 ----
    def decide(self, payload, **kw):
        kw.setdefault("token", self.page_token())  # 페이지 하나 = 토큰 하나. 확인 단계와 전달 단계가 같은 페이지에서 옴
        st, _, b = self.post(payload, path="/api/decide", **kw)
        return st, json.loads(b)

    def page_token(self):
        if not hasattr(self, "_page_token"):
            self._page_token = self.token()
        return self._page_token

    def preview(self, pick=2, did=DID, token=None):
        return self.decide({"id": did, "pick": pick}, **({"token": token} if token else {}))

    def sends(self):
        p = os.path.join(self.fake, "sends.log")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [ln.rstrip("\n").split("\t", 1) for ln in f if ln.strip()]

    def hub_sends(self):
        return [t for h, t in self.sends() if h == HUBH]

    def main_sends(self):
        return [t for h, t in self.sends() if h == MAINH]

    def inbox(self):
        p = os.path.join(self.env.hub, "slack-inbox.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def audit(self):
        p = os.path.join(self.env.hub, "docsite-audit.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def untouched(self):
        self.assertEqual((self.sends(), self.inbox()), ([], []))

    # ---- 정상 흐름 ----
    def test_view_lists_options_from_card_record(self):
        j = json.loads(self.req("GET", f"/api/decision?id={DID}")[2])
        self.assertEqual(j["options"], [{"n": 1, "text": "A 안"}, {"n": 2, "text": "B 안"}])
        self.assertEqual((j["title"], j["actionable"], j["answered"]), ("시험 결정", True, ""))

    def test_preview_assembles_text_on_server_and_sends_nothing(self):
        st, j = self.preview()
        self.assertEqual((st, j["ok"], j["phase"], j["target"], j["text"]), (200, True, "preview", "alpha 허브", SAY))
        self.untouched()

    def test_confirmed_answer_goes_to_hub_with_source_and_records_like_slack(self):
        intent = self.preview()[1]["intent"]
        st, j = self.decide({"id": DID, "pick": 2, "intent": intent})
        self.assertEqual((st, j["ok"], j["via"], j["hub"]), (200, True, "hub", "alpha"))
        self.assertEqual(self.hub_sends(), [SAY])
        self.assertEqual(len(self.main_sends()), 1)
        self.assertIn("참고", self.main_sends()[0])
        rec = self.inbox()[0]
        self.assertEqual((rec["type"], rec["decision_id"], rec["pick"], rec["action"], rec["source"], rec["parent_ts"], rec["kind"]),
                         ("button", DID, 2, "pick", "docsite", "1.1", "decide"))
        with open(os.path.join(self.env.hub, "slack-done.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read().split(), ["1.1"])
        self.env.write(self.env.docs, "x.html", "")  # 기존 목록 규칙(decision_map)이 닫힘으로 읽는지: 답 상태 계산에 같은 기록 사용
        import panel_collect
        self.assertEqual(panel_collect.card_state({"decision_id": DID, "ts": "1.1"}, self.inbox()), "closed")

    def test_audit_log_has_fields_and_no_secrets(self):
        token = self.token()
        intent = self.preview(token=token)[1]["intent"]
        self.decide({"id": DID, "pick": 2, "intent": intent}, token=token)
        raw = open(os.path.join(self.env.hub, "docsite-audit.jsonl"), encoding="utf-8").read()
        rec = json.loads(raw)
        self.assertEqual((rec["source"], rec["did"], rec["pick"], rec["result"]), ("docsite", DID, 2, "hub:ok"))
        self.assertRegex(rec["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}$")
        for secret in (token, intent, "사용자 원문", "B 안"):
            self.assertNotIn(secret, raw)

    def test_intent_is_single_use_and_second_answer_rejected_as_already_answered(self):
        intent = self.preview()[1]["intent"]
        self.assertEqual(self.decide({"id": DID, "pick": 2, "intent": intent})[0], 200)
        st, j = self.decide({"id": DID, "pick": 2, "intent": intent})
        self.assertEqual(st, 409)
        st, j = self.preview(1)
        self.assertEqual((st, j["error"]), (409, "이미 문서 사이트에서 답함"))
        self.assertEqual((len(self.hub_sends()), len(self.inbox())), (1, 1))

    def test_already_answered_in_slack_is_rejected_without_sending(self):
        self.env.jsonl("slack-inbox.jsonl", [{"type": "button", "decision_id": DID, "kind": "decide", "action": "pick", "pick": 1, "parent_ts": "1.1"}])
        st, j = self.preview()
        self.assertEqual((st, j["error"]), (409, "이미 Slack 에서 답함"))
        self.assertEqual(self.sends(), [])
        self.assertFalse(json.loads(self.req("GET", f"/api/decision?id={DID}")[2])["actionable"])

    def test_docsite_answer_stuck_pending_is_redelivered_once_via_commit(self):
        self.env.jsonl("slack-inbox.jsonl", [{"type": "button", "decision_id": DID, "kind": "decide", "action": "pick", "pick": 1, "option_text": "1. A 안",
                                             "parent_ts": "1.1", "source": "docsite"}])
        self.env.jsonl("answer-delivery.jsonl", [{"decision_id": DID, "parent_ts": "1.1", "state": "pending", "ok": None, "at": 1}])
        intent = self.preview(2)[1]["intent"]  # 오래된 pending 은 preview 가 막지 않음
        st, j = self.decide({"id": DID, "pick": 2, "intent": intent})
        self.assertEqual((st, j["via"]), (200, "hub"))
        self.assertEqual(self.hub_sends(), [f"사용자 원문: 결정 {DID} = 선택 1(A 안) (버튼) (출처: 문서 사이트)"])  # 먼저 답한 원문, 이번 선택(2) 아님
        self.assertEqual(self.preview(2)[0], 409)  # 재전달은 1회뿐: 이후엔 이미 답함

    def test_two_confirmed_clicks_at_once_only_one_wins(self):
        i1, i2 = self.preview()[1]["intent"], self.preview(1)[1]["intent"]
        res = []
        t1 = threading.Thread(target=lambda: res.append(self.decide({"id": DID, "pick": 2, "intent": i1})[0]))
        t2 = threading.Thread(target=lambda: res.append(self.decide({"id": DID, "pick": 1, "intent": i2})[0]))
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(sorted(res), [200, 409])
        self.assertEqual((len(self.inbox()), len(self.hub_sends())), (1, 1))

    # ---- 전달 경로 ----
    def test_hub_send_failure_goes_to_main_proxy(self):
        os.environ["FAKE_SEND_FAIL"] = f"{HUBH}:terminal_handle_stale"
        intent = self.preview()[1]["intent"]
        st, j = self.decide({"id": DID, "pick": 2, "intent": intent})
        self.assertEqual((st, j["via"]), (200, "proxy"))
        self.assertEqual(self.hub_sends(), [])
        self.assertIn(SAY, self.main_sends()[0])
        self.assertEqual(len(self.inbox()), 1)  # 답은 기록됨

    def test_timeout_is_not_resent_and_reports_unknown(self):
        os.environ["FAKE_SEND_HANG"] = HUBH
        sys.modules["card_actions"].ORCA_TIMEOUT_S = 1
        intent = self.preview()[1]["intent"]
        st, j = self.decide({"id": DID, "pick": 2, "intent": intent})
        self.assertEqual(st, 502)
        self.assertIn("확실하지 않음", j["error"])
        self.assertEqual(self.hub_sends(), [SAY])  # 한 번만 보냄(재전송 없음)
        self.assertEqual(len(self.main_sends()), 1)
        self.assertNotIn("사용자 원문", self.main_sends()[0])
        self.assertEqual(self.audit()[-1]["result"], "unknown:fail")

    def test_no_hub_mapping_goes_to_main_only(self):
        os.remove(os.path.join(self.env.hub, "hub-reports.jsonl"))
        self.env.jsonl("slack-sent.jsonl", [{"ts": "1.1", "title": "시험 결정", "form_kind": "decide", "decision_id": DID, "options": {"1": "A 안", "2": "B 안"}}])
        st, j = self.preview()
        self.assertEqual(j["target"], "메인 터미널")
        st, j = self.decide({"id": DID, "pick": 2, "intent": j["intent"]})
        self.assertEqual((st, j["via"]), (200, "main"))
        self.assertEqual(self.hub_sends(), [])
        self.assertIn("[문서 사이트→메인] 결정 D-0001 = 선택 2(B 안)", self.main_sends()[0])

    def test_watcher_card_with_registered_action_must_be_answered_in_slack(self):
        sys.modules["card_actions"].register(DID, "resume", {"slugs": ["alpha"]})
        st, j = self.preview()
        self.assertEqual((st, j["error"]), (409, "감시기 카드는 Slack 에서 답하세요"))
        self.assertIn(DID, sys.modules["card_actions"].load())  # 등록을 꺼내지 않음

    # ---- 위조·잘못된 요청 ----
    def test_client_supplied_text_is_never_accepted(self):
        for extra in ({"message": "rm -rf /"}, {"text": "x"}, {"label": "x"}, {"recipient": "x"}):
            self.assertEqual(self.decide({"id": DID, "pick": 2, **extra})[0], 400, extra)
        intent = self.preview()[1]["intent"]
        self.assertEqual(self.decide({"id": DID, "pick": 2, "intent": intent, "message": "대신 이 문구"})[0], 400)
        self.untouched()

    def test_commit_without_preview_or_with_forged_intent_rejected(self):
        for intent in ("", "forged", "A" * 40):
            self.assertEqual(self.decide({"id": DID, "pick": 2, "intent": intent})[0], 409, intent)
        self.untouched()
        self.assertTrue(all(a["result"] == "rejected:no_confirm" for a in self.audit()))

    def test_intent_bound_to_same_decision_pick_and_page_token(self):
        t1, t2 = self.token(), self.token()
        intent = self.preview(token=t1)[1]["intent"]
        self.assertEqual(self.decide({"id": DID, "pick": 2, "intent": intent}, token=t2)[0], 409)  # 다른 페이지 토큰
        intent = self.preview(token=t1)[1]["intent"]
        self.assertEqual(self.decide({"id": DID, "pick": 1, "intent": intent}, token=t1)[0], 409)  # 다른 선택 번호
        self.untouched()

    def test_expired_intent_rejected(self):
        intent = self.preview()[1]["intent"]
        for it in self.app.decider._intents.values():
            self.app.decider._intents[intent] = it.__class__(it.did, it.pick, it.who, 0)
        self.assertEqual(self.decide({"id": DID, "pick": 2, "intent": intent})[0], 409)
        self.untouched()

    def test_bad_ids_and_picks(self):
        self.assertEqual(self.preview(9)[0], 400)
        self.assertEqual(self.preview(0)[0], 400)
        self.assertEqual(self.preview(did="D-9999")[0], 404)
        self.assertEqual(self.preview(did="../x")[0], 404)
        for pick in ("2", True, None, 2.5, [2]):
            self.assertEqual(self.decide({"id": DID, "pick": pick})[0], 400, pick)
        self.assertEqual(self.decide({"id": 1, "pick": 2})[0], 400)
        self.assertEqual(self.decide([1])[0], 400)
        self.untouched()

    def test_csrf_origin_host_and_fetch_site_checks_apply(self):
        body = {"id": DID, "pick": 2}
        self.assertEqual(self.post(body, path="/api/decide", token=None)[0], 403)
        self.assertEqual(self.post(body, path="/api/decide", token="forged")[0], 403)
        for origin in ("http://evil.example", "null", None):
            self.assertEqual(self.post(body, path="/api/decide", origin=origin)[0], 403, origin)
        self.assertEqual(self.post(body, path="/api/decide", extra={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.post(body, path="/api/decide", extra={"Sec-Fetch-Site": "same-site"})[0], 403)
        self.assertEqual(self.post(body, path="/api/decide", extra={"Host": "evil.example:%d" % self.port})[0], 403)
        self.assertEqual(self.post(body, path="/api/decide", ctype="application/x-www-form-urlencoded")[0], 415)
        self.assertEqual(self.post({"id": DID, "pick": 2, "pad": "가" * 70000}, path="/api/decide")[0], 413)
        self.assertEqual(self.req("GET", "/api/decide")[0], 404)
        self.assertEqual(self.req("PUT", "/api/decide")[0], 405)
        self.assertEqual(self.req("GET", f"/api/decision?id={DID}", host="evil.example:%d" % self.port)[0], 403)
        self.untouched()

    def test_forged_commit_with_valid_headers_but_no_card_is_rejected(self):
        self.env.jsonl("slack-sent.jsonl", [])
        self.assertEqual(self.preview()[0], 404)
        self.untouched()

    # ---- 구조 ----
    def test_decide_handler_called_only_from_post_handler(self):
        src = open(os.path.join(os.path.dirname(self.db.__file__), "doc_browser.py"), encoding="utf-8").read()
        self.assertEqual(src.count("decider.handle("), 1)
        self.assertEqual(src.count("doc_send.send("), 1)

    def test_ui_has_confirm_step_and_no_client_text(self):
        ui = open(os.path.join(os.path.dirname(self.db.__file__), "doc_browser_ui.html"), encoding="utf-8").read()
        self.assertIn("/api/decide", ui)
        self.assertIn("이 답으로 전달", ui)
        self.assertIn("intent", ui)
        self.assertNotIn("message:", ui.split("decPost(")[1].split("async function")[0] if "decPost(" in ui else "")


if __name__ == "__main__":
    unittest.main()
