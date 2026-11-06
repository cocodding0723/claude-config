#!/usr/bin/env python3
"""Slack API 대체 스텁. 로컬 포트만 열고 고정 JSON 응답을 순서대로 돌려줌.

사용: stub_slack.py <script.json> <request-log.jsonl> <port-file>
script.json: {"메서드": [응답, ...]}. 응답은 JSON 객체 또는 {"_status": 429, "_headers": {...}}.
_chmod 키가 있으면 응답 직전 그 파일을 읽기 전용으로 바꿈(전송 직후 이력 쓰기 실패 재현).
마지막 응답은 소진 후에도 반복. 요청은 로그에 한 줄씩 기록(메서드·쿼리·본문).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

SCRIPT, LOG, PORT_FILE = sys.argv[1:4]
QUEUES = json.load(open(SCRIPT, encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    def _serve(self, body):
        u = urlparse(self.path)
        method = u.path.rsplit("/", 1)[-1]
        rec = {"method": method, "query": {k: v[0] for k, v in parse_qs(u.query).items()}, "body": body}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        q = QUEUES.get(method, [{"ok": False, "error": "unscripted"}])
        resp = q.pop(0) if len(q) > 1 else q[0]
        if resp.get("_chmod"):
            os.chmod(resp["_chmod"], 0o444)
        status = resp.get("_status", 200)
        data = json.dumps({k: v for k, v in resp.items() if not k.startswith("_")}).encode()
        self.send_response(status)
        for k, v in resp.get("_headers", {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._serve(None)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n).decode() if n else ""
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw
        self._serve(body)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Handler)
open(PORT_FILE, "w").write(str(srv.server_port))
srv.serve_forever()
