import http.client
import json
import os
import socket
import threading
import unittest
from urllib.parse import quote

from common import SVG3, Env, page

SUMMARY = '<div data-check="summary"><b>3줄 요약</b><br>① 가.<br>② 나.<br>③ 다.</div>'


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class ServerBase(unittest.TestCase):
    """서버 띄우기·요청 도구. 시험 메서드 없음(ServerTest·DecideTest 가 상속)"""

    def setUp(self):
        self.env = Env()
        self.ui = os.path.join(self.env.hub, "ui.html")
        with open(self.ui, "w", encoding="utf-8") as f:
            f.write("<!doctype html><meta name=csrf content=__CSRF_TOKEN__>UI")
        os.environ["LRM_DOC_UI"] = self.ui
        notify = os.path.join(self.env.hub, "fake_notify.sh")
        with open(notify, "w") as f:
            f.write('#!/bin/bash\nprintf "%s\\n" "$@" > "$LRM_HUB_DIR/notify-args"\n[ "${FAKE_RC:-0}" = 0 ] || { echo "Slack 오류" >&2; exit 1; }\n')
        os.environ["LRM_DOC_NOTIFY"] = notify
        self.prepare()
        with open(os.path.join(self.env.hub, "doc-recipients.json"), "w", encoding="utf-8") as f:
            json.dump({"recipients": [{"name": "시험", "slack_id": "UREC0001"}]}, f)
        self.d1 = os.path.realpath(self.env.write(self.env.docs, "a.html", page("가 문서", SUMMARY + SVG3)))
        self.d2 = os.path.realpath(self.env.write(self.env.ws, "b/b.html", page("나 문서")))
        self.env.write(self.env.docs, ".env", "KEY=1")
        import doc_browser
        self.db = doc_browser
        self.port = free_port()
        self.app, self.srv = doc_browser.build_server(self.port)
        self.app.index.refresh()
        self.app.first_scan_done = True
        threading.Thread(target=lambda: self.srv.serve_forever(poll_interval=0.01), daemon=True).start()

    def prepare(self):
        """서버 모듈을 불러오기 전에 환경을 더 맞출 자리(하위 시험이 채움)"""

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.env.close()

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def token(self):
        _, _, b = self.req("GET", "/")
        return b.decode().split('content=')[1].split('>')[0]

    def docs(self, qs=""):
        return json.loads(self.req("GET", "/api/docs" + quote(qs, safe="?=&"))[2])

    def doc_id(self, title):
        return next(d["id"] for d in self.docs()["docs"] if d["title"] == title)

    def post(self, payload, origin="auto", token="auto", ctype="application/json", extra=None, path="/api/send"):
        h = {"Content-Type": ctype}
        if origin == "auto":
            origin = f"http://127.0.0.1:{self.port}"
        if origin:
            h["Origin"] = origin
        if token == "auto":
            token = self.token()
        if token:
            h["X-CSRF-Token"] = token
        h.update(extra or {})
        return self.req("POST", path, json.dumps(payload, ensure_ascii=False).encode(), h)

    def sent_called(self):
        return os.path.exists(os.path.join(self.env.hub, "notify-args"))


class ServerTest(ServerBase):

    # ---- 바인드·Host ----
    def test_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        os.environ["LRM_DOC_HOST"] = "0.0.0.0"
        self.assertEqual(self.db.HOST, "127.0.0.1")

    def test_foreign_host_header_rejected_everywhere(self):
        for path in ("/", "/api/docs", "/api/recipients"):
            self.assertEqual(self.req("GET", path, host="evil.example:%d" % self.port)[0], 403, path)
        self.assertEqual(self.req("GET", "/api/docs", host="127.0.0.1:1")[0], 403)
        self.assertEqual(self.req("GET", "/api/docs", host=f"localhost:{self.port}")[0], 200)

    # ---- 수집·필터·답 상태 ----
    def test_list_newest_first_with_hub_filter_and_counts(self):
        os.utime(self.d1, (1000, 1000))
        os.utime(self.d2, (2000, 2000))
        self.app.index.refresh()
        j = self.docs()
        self.assertEqual([d["title"] for d in j["docs"]], ["나 문서", "가 문서"])
        self.assertEqual(j["hubs"], {"vfx": 1, "정리기록": 1})
        self.assertEqual([d["title"] for d in self.docs("?hub=vfx")["docs"]], ["나 문서"])
        self.assertEqual(self.docs("?hub=vfx")["total"], 2)  # 필터 전 전체 수
        self.assertEqual([d["title"] for d in self.docs("?q=가")["docs"]], ["가 문서"])

    def test_diagram_filters(self):
        self.assertEqual([d["title"] for d in self.docs("?diagram=none")["docs"]], ["나 문서"])
        self.assertEqual([d["title"] for d in self.docs("?diagram=has")["docs"]], ["가 문서"])

    def test_answer_state_filter(self):
        self.env.jsonl("slack-sent.jsonl", [
            {"ts": "1.1", "form_kind": "decide", "decision_id": "D-AAA", "options": {"1": "a"}},
            {"ts": "", "thread_ts": "1.1", "kind": "file", "path": self.d1},
        ])
        self.app.index.refresh()
        self.assertEqual([d["title"] for d in self.docs("?answer=대기")["docs"]], ["가 문서"])
        self.assertEqual([d["title"] for d in self.docs("?answer=없음")["docs"]], ["나 문서"])
        self.assertEqual(self.docs("?answer=답함")["docs"], [])

    def test_no_local_path_in_list(self):
        self.assertNotIn(self.env.tmp.name, json.dumps(self.docs(), ensure_ascii=False))

    # ---- 열람·썸네일 ----
    def test_doc_served_sandboxed_by_id_only(self):
        did = self.doc_id("가 문서")
        st, h, b = self.req("GET", "/doc/" + did)
        self.assertEqual(st, 200)
        self.assertIn("sandbox", h["Content-Security-Policy"])
        self.assertIn("가 문서".encode(), b)
        for bad in ("../../etc/passwd", "zzz", "%2e%2e%2f%2e%2e%2fetc%2fpasswd"):
            self.assertEqual(self.req("GET", "/doc/" + bad)[0], 404, bad)

    def test_secret_file_never_listed_or_served(self):
        self.assertNotIn(".env", json.dumps(self.docs()))
        self.assertEqual(self.req("GET", "/doc/" + self.db.doc_index.doc_id(os.path.join(self.env.docs, ".env")))[0], 404)

    def test_thumb_and_missing_thumb(self):
        st, h, b = self.req("GET", "/thumb/%s.svg" % self.doc_id("가 문서"))
        self.assertEqual((st, h["Content-Type"]), (200, "image/svg+xml"))
        self.assertEqual(self.req("GET", "/thumb/%s.svg" % self.doc_id("나 문서"))[0], 404)

    def test_draft_and_recipients_hide_slack_id(self):
        d = json.loads(self.req("GET", "/api/draft?id=" + self.doc_id("가 문서"))[2])
        self.assertEqual(d["message"], "가 문서\n\n① 가.\n② 나.\n③ 다.")
        r = self.req("GET", "/api/recipients")[2].decode()
        self.assertIn("시험", r)
        self.assertNotIn("UREC0001", r)

    # ---- 보내기: 정상 ----
    def test_send_ok_records_and_marks_sent(self):
        did = self.doc_id("가 문서")
        st, _, b = self.post({"id": did, "recipient": "시험", "message": "안녕"})
        self.assertEqual((st, json.loads(b)["ok"]), (200, True))
        args = open(os.path.join(self.env.hub, "notify-args"), encoding="utf-8").read().splitlines()
        self.assertEqual((args[1], args[-2], args[-1]), ("UREC0001", "안녕", self.d1))
        d = next(x for x in self.docs()["docs"] if x["id"] == did)
        self.assertEqual(d["sent"][0]["to"], "시험")
        self.assertEqual(len(self.docs("?sent=1")["docs"]), 1)

    def test_send_failure_returns_cause_and_no_mark(self):
        os.environ["FAKE_RC"] = "1"
        st, _, b = self.post({"id": self.doc_id("가 문서"), "recipient": "시험", "message": "x"})
        self.assertEqual(st, 400)
        self.assertIn("전송 실패", json.loads(b)["error"])
        self.assertEqual(self.docs("?sent=1")["docs"], [])

    # ---- 보내기: 위조 요청 거부 ----
    def test_csrf_token_required(self):
        did = self.doc_id("가 문서")
        self.assertEqual(self.post({"id": did, "recipient": "시험", "message": "x"}, token=None)[0], 403)
        self.assertEqual(self.post({"id": did, "recipient": "시험", "message": "x"}, token="forged")[0], 403)
        self.assertFalse(self.sent_called())

    def test_foreign_or_missing_origin_rejected(self):
        did = self.doc_id("가 문서")
        for origin in ("http://evil.example", "https://127.0.0.1:%d" % self.port, "http://127.0.0.1:1", "null", None):
            self.assertEqual(self.post({"id": did, "recipient": "시험", "message": "x"}, origin=origin)[0], 403, origin)
        self.assertFalse(self.sent_called())

    def test_cross_site_fetch_metadata_rejected(self):
        did = self.doc_id("가 문서")
        self.assertEqual(self.post({"id": did, "recipient": "시험", "message": "x"}, extra={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertFalse(self.sent_called())

    def test_form_post_and_get_cannot_send(self):
        did = self.doc_id("가 문서")
        self.assertEqual(self.post({"id": did, "recipient": "시험", "message": "x"}, ctype="application/x-www-form-urlencoded")[0], 415)
        self.assertEqual(self.req("GET", "/api/send")[0], 404)
        self.assertEqual(self.req("PUT", "/api/send")[0], 405)
        self.assertFalse(self.sent_called())

    def test_send_rejects_unknown_doc_path_and_oversize(self):
        for bad in ("../x", self.d1, "zzz"):
            self.assertEqual(self.post({"id": bad, "recipient": "시험", "message": "x"})[0], 404, bad)
        big = {"id": self.doc_id("가 문서"), "recipient": "시험", "message": "가" * 70000}
        self.assertEqual(self.post(big)[0], 413)
        self.assertEqual(self.post({"id": 1, "recipient": "시험", "message": "x"})[0], 400)
        self.assertFalse(self.sent_called())

    def test_token_from_other_page_load_still_valid_but_expired_rejected(self):
        t = self.token()
        did = self.doc_id("가 문서")
        self.app._tokens[t] = 0  # 만료
        self.assertEqual(self.post({"id": did, "recipient": "시험", "message": "x"}, token=t)[0], 403)

    def test_send_is_called_only_from_post_handler(self):
        src = open(os.path.join(os.path.dirname(self.db.__file__), "doc_browser.py"), encoding="utf-8").read()
        self.assertEqual(src.count("doc_send.send("), 1)

    def test_ui_file_static_rules(self):
        ui = open(os.path.join(os.path.dirname(self.db.__file__), "doc_browser_ui.html"), encoding="utf-8").read()
        self.assertIn(self.db.TOKEN_SLOT, ui)
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "http://", "https://"):
            self.assertNotIn(bad, ui, bad)
        self.assertIn("X-CSRF-Token", ui)
        self.assertIn('sandbox=""', ui)  # 열람 iframe 은 모든 권한 제거
        for term in ("localStorage",):
            self.assertIn(term, ui)


if __name__ == "__main__":
    unittest.main()
