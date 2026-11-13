#!/usr/bin/env python3
"""로컬 문서 모아보기 서버. 127.0.0.1 에만 열리는 상시 페이지(launchd). 표준 라이브러리만 사용.

사용: doc_browser.py [--port N]   (주소는 http://127.0.0.1:<port>/, 기본 포트 DEFAULT_PORT)
화면: doc_browser_ui.html (같은 폴더, LRM_DOC_UI 로 재정의). 서버는 데이터 API 만 노출하고 화면은 /api/* 로만 데이터에 닿음.

보안(검사는 이 파일 한 곳):
  - 바인드 주소는 상수 HOST(127.0.0.1) 고정. 환경 변수·인자로 바꿀 수 없음.
  - 모든 요청의 Host 헤더가 127.0.0.1·localhost(+포트)가 아니면 403(DNS 리바인딩 차단).
  - 보내기는 POST /api/send 한 곳. Origin 이 이 서버 주소가 아니거나 없으면 403, Sec-Fetch-Site 가 same-origin 이 아니면 403,
    X-CSRF-Token 이 페이지 열 때 발급한 토큰과 다르면 403. JSON 본문만 받고 크기 상한(BODY_MAX).
  - 문서는 수집된 id 로만 엶. 경로를 받지 않음. 문서 본문은 CSP sandbox(스크립트·외부 요청 없음)로 내려줌.
  - 전송 함수(doc_send.send)를 부르는 곳은 이 파일의 POST 처리기뿐.
수집은 시작 직후 백그라운드에서 돌고 REFRESH_S 마다 갱신함. 첫 수집이 끝나기 전 목록은 scanning=true 로 알림.
"""
import argparse
import json
import os
import secrets
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import doc_index  # noqa: E402
import doc_send  # noqa: E402

HOST = "127.0.0.1"                 # 바인드 고정. 바꾸려면 코드를 고쳐야 함
DEFAULT_PORT = 17420
REFRESH_S = 60
BODY_MAX = 64 * 1024
TOKEN_TTL_S = 12 * 3600
TOKEN_MAX = 500
UI_PATH = os.environ.get("LRM_DOC_UI") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "doc_browser_ui.html")
TOKEN_SLOT = "__CSRF_TOKEN__"
UI_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; "
          "connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
DOC_CSP = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:"
THUMB_CSP = "default-src 'none'; style-src 'unsafe-inline'"


class App:
    """서버 상태: 목록, 열 때마다 발급하는 CSRF 토큰, 포트"""

    def __init__(self, port: int):
        self.port = port
        self.index = doc_index.Index()
        self.first_scan_done = False
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    @property
    def origins(self) -> set[str]:
        return {f"http://{h}" for h in self.hosts}

    def issue_token(self) -> str:
        t, now = secrets.token_urlsafe(32), time.time()
        with self._lock:
            for k in [k for k, exp in self._tokens.items() if exp < now]:
                del self._tokens[k]
            if len(self._tokens) >= TOKEN_MAX:
                del self._tokens[min(self._tokens, key=self._tokens.get)]
            self._tokens[t] = now + TOKEN_TTL_S
        return t

    def token_ok(self, t: str) -> bool:
        with self._lock:
            exp = self._tokens.get(t or "")
        return exp is not None and exp >= time.time()

    def scan_loop(self):
        while True:
            try:
                self.index.refresh()
            except Exception as e:  # 수집 실패가 서버를 죽이지 않게. 다음 회차에 재시도
                print(f"수집 실패: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            self.first_scan_done = True
            time.sleep(REFRESH_S)

    def docs_payload(self, params: dict) -> dict:
        """목록 + 허브별 개수(필터 전 기준). 필터: hub, q(제목 부분 일치), diagram=none|has|unknown, answer=대기|답함|보류|충돌|없음, sent=1|0"""
        sent = doc_send.sent_by_path()
        recs = self.index.snapshot()
        hubs: dict[str, int] = {}
        for r in recs:
            hubs[r.hub] = hubs.get(r.hub, 0) + 1
        docs = []
        for r in recs:
            d = r.public()
            d["sent"] = sent.get(r.path, [])
            if matches(d, params):
                docs.append(d)
        return {"docs": docs, "total": len(recs), "hubs": hubs, "scanning": not self.first_scan_done,
                "scanned_at": self.index.scanned_at}


def matches(d: dict, p: dict) -> bool:
    """문서 하나가 필터 조건(모두 만족)에 맞는지"""
    if p.get("hub") and d["hub"] != p["hub"]:
        return False
    if p.get("q") and p["q"].lower() not in d["title"].lower():
        return False
    dg = p.get("diagram")
    if dg == "none" and not (d["diagrams"] == 0 and not d["no_diagram_reason"]):
        return False
    if dg == "has" and not (d["diagrams"] or 0) > 0:
        return False
    if dg == "unknown" and d["diagrams"] is not None:
        return False
    ans = p.get("answer")
    if ans == "없음" and d["decisions"]:
        return False
    if ans in ("대기", "답함", "보류", "충돌") and not any(x["state"] == ans for x in d["decisions"]):
        return False
    if p.get("sent") == "1" and not d["sent"]:
        return False
    if p.get("sent") == "0" and d["sent"]:
        return False
    return True


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "doc-browser"
        protocol_version = "HTTP/1.0"  # 응답마다 연결을 닫음. 거부한 POST 의 미읽은 본문이 다음 요청에 섞이지 않게

        def log_message(self, fmt, *args):  # 접속 기록에 본문·토큰을 남기지 않음. 경로(쿼리 제외)만
            sys.stderr.write("%s %s\n" % (self.command, urlparse(self.path).path))

        # ---- 응답 도구 ----
        def _send(self, code, body: bytes, ctype, csp=None, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if csp:
                self.send_header("Content-Security-Policy", csp)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _deny(self, why, code=403):
            self._json(code, {"ok": False, "error": why})

        def _host_ok(self) -> bool:
            if (self.headers.get("Host") or "") in app.hosts:
                return True
            self._deny("허용되지 않은 Host — http://127.0.0.1:%d 로 접속하세요" % app.port)
            return False

        # ---- GET ----
        def do_GET(self):
            if not self._host_ok():
                return
            u = urlparse(self.path)
            q = parse_qs(u.query)
            p = u.path
            if p == "/":
                return self._page()
            if p == "/api/docs":
                return self._json(200, app.docs_payload({k: v[0] for k, v in q.items()}))
            if p == "/api/recipients":
                return self._json(200, {"recipients": doc_send.public_recipients()})
            if p == "/api/draft":
                return self._draft((q.get("id") or [""])[0])
            if p.startswith("/doc/"):
                return self._doc(p[len("/doc/"):])
            if p.startswith("/thumb/") and p.endswith(".svg"):
                return self._thumb(p[len("/thumb/"):-len(".svg")])
            self._deny("없는 주소", 404)

        def _page(self):
            try:
                with open(UI_PATH, encoding="utf-8") as f:
                    html = f.read()
            except OSError:
                return self._deny("화면 파일 없음: %s — install.sh 로 설치 상태를 확인하세요" % UI_PATH, 500)
            html = html.replace(TOKEN_SLOT, app.issue_token())
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", UI_CSP, {"X-Frame-Options": "DENY"})

        def _draft(self, did):
            path = app.index.path_of(did)
            if not path:
                return self._deny("없는 문서", 404)
            title = next((r.title for r in app.index.snapshot() if r.id == did), os.path.basename(path))
            self._json(200, doc_send.draft(path, title))

        def _doc(self, did):
            path = app.index.path_of(did)
            if not path:
                return self._deny("없는 문서", 404)
            try:
                with open(path, "rb") as f:
                    body = f.read()
            except OSError as e:
                return self._deny("문서 읽기 실패: %s — 파일 접근 권한을 확인하세요" % (e.strerror or "오류"), 500)
            self._send(200, body, "text/html; charset=utf-8", DOC_CSP)

        def _thumb(self, did):
            svg = app.index.thumb_of(did)
            if not svg:
                return self._deny("도식 없음", 404)
            self._send(200, svg.encode("utf-8"), "image/svg+xml", THUMB_CSP)

        # ---- POST: 보내기 한 곳뿐 ----
        def do_POST(self):
            if not self._host_ok():
                return
            if urlparse(self.path).path != "/api/send":
                return self._deny("없는 주소", 404)
            origin = self.headers.get("Origin")
            if origin not in app.origins:
                return self._deny("요청 출처 불일치 — 문서 페이지 안에서 누른 요청만 받음")
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
                return self._deny("다른 사이트에서 온 요청 — 문서 페이지 안에서 누른 요청만 받음")
            if not app.token_ok(self.headers.get("X-CSRF-Token") or ""):
                return self._deny("보안 토큰 없음·만료 — 페이지를 새로 고친 뒤 다시 누르세요")
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._deny("JSON 요청만 받음", 415)
            try:
                n = int(self.headers.get("Content-Length") or "-1")
            except ValueError:
                n = -1
            if n < 0 or n > BODY_MAX:
                return self._deny("요청 크기 초과·누락", 413)
            try:
                req = json.loads(self.rfile.read(n))
                did, who, msg = req["id"], req["recipient"], req["message"]
                if not all(isinstance(x, str) for x in (did, who, msg)):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._deny("요청 형식 오류 — id·recipient·message 문자열 필요", 400)
            path = app.index.path_of(did)
            if not path:
                return self._deny("없는 문서 — 목록을 새로 고치세요", 404)
            title = next((r.title for r in app.index.snapshot() if r.id == did), os.path.basename(path))
            try:
                res = doc_send.send(path, title, who, msg)
            except doc_send.SendError as e:
                return self._deny(str(e), 400)
            except Exception as e:
                print(f"보내기 예외: {type(e).__name__}", file=sys.stderr, flush=True)
                return self._deny("보내기 중 알 수 없는 오류 — 로그를 확인하세요", 500)
            self._json(200, {"ok": True, **res})

        def do_PUT(self):
            self._deny("허용되지 않은 방식", 405)

        do_DELETE = do_PATCH = do_PUT

    return Handler


def build_server(port: int):
    app = App(port)
    return app, ThreadingHTTPServer((HOST, port), make_handler(app))


def main(argv=None):
    ap = argparse.ArgumentParser(description="로컬 문서 모아보기 서버(127.0.0.1 전용)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("LRM_DOC_PORT") or DEFAULT_PORT))
    args = ap.parse_args(argv)
    app, srv = build_server(args.port)
    threading.Thread(target=app.scan_loop, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    print(f"문서 모아보기: http://{HOST}:{args.port}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    srv.server_close()


if __name__ == "__main__":
    main()
