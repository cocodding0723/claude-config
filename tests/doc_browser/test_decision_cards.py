"""확인 목록 '답하기' 열기 시험: 번호 없는 결정 4·답한 카드 3(그중 같은 번호 다른 카드 2장)을 라이브와 같은 형태로 만들어
목록 상태(카드 ts·완료 판정)와 /api/decision 열기 결과를 확인함. 판정은 answer_store 카드 단위 한 곳."""
import json
import os
import time
import unittest

import test_decide as td
from test_server import ServerBase

NO_DID = [("hub:data-design:t:3e529255", "LRM-547 체크포인트 카드 3개 결정"), ("hub:data-design:b:6654f8dd", "체크포인트 서버 정산 필드 결정"),
          ("hub:vfx:b:adad635c", "톤다운 적용 범위 결정"), ("hub:test-qa:b:0bba0c38", "오토마 기획자 질문 3개 결정")]
OPTS = {"1": "가", "2": "나"}


class CardsTest(ServerBase):
    prepare_hub = td.DecideTest.prepare
    fwrite = td.DecideTest.fwrite

    def prepare(self):
        self.prepare_hub()
        self.snap = os.path.join(self.env.hub, "progress.json")
        os.environ.update({"LRM_PROGRESS": self.snap, "LRM_DIGEST_REPOS": "Org/Repo", "LRM_DIGEST_GH": os.path.join(self.env.tmp.name, "no-gh"),
                           "LRM_LEDGER": os.path.join(self.env.hub, "no-ledger.tsv")})
        sent = [{"ts": "10.1", "channel": "C1", "title": "공주영 스파인 앞머리 분절 방식", "form_kind": "decide", "decision_id": "D-SPINE", "options": OPTS, "tag": "data-design"},
                {"ts": "20.2", "channel": "C1", "title": "스파인 설정 원본을 빌드 밖으로 옮길까?", "form_kind": "decide", "decision_id": "D-SPINE", "options": OPTS, "tag": "data-design"},
                {"ts": "30.3", "channel": "C1", "title": "서버 정산", "form_kind": "decide", "decision_id": "D-CKSERVER", "options": OPTS, "tag": "data-design"},
                {"ts": "40.4", "channel": "C1", "title": "열린 결정", "form_kind": "decide", "decision_id": "D-OPEN", "options": OPTS, "tag": "vfx"}]
        self.env.jsonl("slack-sent.jsonl", sent)
        self.env.jsonl("slack-inbox.jsonl", [
            {"type": "button", "parent_ts": "10.1", "kind": "decide", "decision_id": "D-SPINE", "action": "pick", "pick": 2, "source": "slack"},
            {"type": "button", "parent_ts": "30.3", "kind": "decide", "decision_id": "D-CKSERVER", "action": "pick", "pick": 1, "source": "slack"}])
        items = [{"key": k, "bucket": "block", "title": f"data-design: {t}", "since": 0} for k, t in NO_DID]
        items += [{"key": f"decision:{d}", "bucket": "wait", "title": f"결정 {d}", "since": 0} for d in ("D-SPINE", "D-CKSERVER", "D-OPEN")]
        with open(self.snap, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "updatedAt": time.time(), "items": items, "sources": {}, "notes": [], "counts": {}, "groups": {}}, f)

    def setUp(self):
        super().setUp()
        self.app.checklist.refresh(use_pr=False)

    def items(self):
        return {i["key"]: i for i in json.loads(self.req("GET", "/api/confirm")[2])["items"]}

    def test_cards_without_number_have_no_ts(self):
        by = self.items()
        for k, _ in NO_DID:
            self.assertEqual((by[k]["kind"], by[k]["ts"], by[k]["did"], by[k]["state"]), ("decision", "", "", "todo"), k)

    def test_same_number_cards_are_separate_items_with_own_state(self):
        by = self.items()
        old, new = by["decision:D-SPINE@10.1"], by["decision:D-SPINE@20.2"]
        self.assertEqual((old["state"], old["ts"], old["title"]), ("done", "10.1", "공주영 스파인 앞머리 분절 방식"))
        self.assertEqual((new["state"], new["ts"], new["title"]), ("todo", "20.2", "스파인 설정 원본을 빌드 밖으로 옮길까?"))
        self.assertIn("이미 Slack 에서 답함", old["basis"])

    def test_answered_card_is_done_in_list_like_api(self):
        by = self.items()
        self.assertEqual((by["decision:D-CKSERVER"]["state"], by["decision:D-CKSERVER"]["ts"]), ("done", "30.3"))
        self.assertEqual(by["decision:D-OPEN"]["state"], "todo")
        # 목록의 done 과 API 의 answered 가 같은 판정
        for it in by.values():
            if it["ts"]:
                v = json.loads(self.req("GET", f"/api/decision?id={it['did']}&ts={it['ts']}")[2])
                self.assertEqual(bool(v["answered"]), it["state"] == "done", it["key"])
                self.assertEqual(v["ts"], it["ts"])

    def test_open_by_ts_opens_that_card_not_latest(self):
        st, _, b = self.req("GET", "/api/decision?id=D-SPINE&ts=10.1")
        self.assertEqual((st, json.loads(b)["title"]), (200, "공주영 스파인 앞머리 분절 방식"))
        self.assertEqual(json.loads(self.req("GET", "/api/decision?id=D-SPINE")[2])["title"], "스파인 설정 원본을 빌드 밖으로 옮길까?")  # ts 없으면 최근 카드

    def test_open_failures_return_reason(self):
        st, _, b = self.req("GET", "/api/decision?id=")
        self.assertEqual((st, "없는 결정" in json.loads(b)["error"]), (404, True))
        self.assertEqual(self.req("GET", "/api/decision?id=D-SPINE&ts=9.9")[0], 404)

    def test_ui_has_no_card_label_and_failure_text(self):
        ui = open(os.path.join(os.path.dirname(self.db.__file__), "doc_browser_ui.html"), encoding="utf-8").read()
        for needle in ("카드 없음 — Slack/허브로 답", "불러오기 실패", 'id="dec-cancel"'):
            self.assertIn(needle, ui)


if __name__ == "__main__":
    unittest.main()
