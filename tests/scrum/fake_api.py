#!/usr/bin/env python3
"""localhost 가짜 Anthropic API. 첫 요청엔 argv[2] 도구를 부르는 tool_use, tool_result 가 온 뒤엔 텍스트로 끝냄.
사용: fake_api.py <포트기록파일> <호출할 도구 이름>. 시험용(네트워크 밖으로 안 나감)"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

TOOL = sys.argv[2]


def sse(events):
    return "".join("event: %s\ndata: %s\n\n" % (e["type"], json.dumps(e)) for e in events).encode()


def message(content_block, stop):
    base = {"id": "msg_fake", "type": "message", "role": "assistant", "model": "test", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
    start = {"type": "message_start", "message": base}
    if content_block["type"] == "tool_use":
        delta = {"type": "input_json_delta", "partial_json": "{}"}
        block = dict(content_block, input={})
    else:
        delta = {"type": "text_delta", "text": content_block["text"]}
        block = {"type": "text", "text": ""}
    return sse([start, {"type": "content_block_start", "index": 0, "content_block": block},
                {"type": "content_block_delta", "index": 0, "delta": delta},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 1}},
                {"type": "message_stop"}])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)) or 0)
        try:
            msgs = json.loads(body).get("messages", [])
        except ValueError:
            msgs = []
        has_result = any(isinstance(m.get("content"), list) and any(b.get("type") == "tool_result" for b in m["content"])
                         for m in msgs)
        if has_result:
            out = message({"type": "text", "text": "done"}, "end_turn")
        else:
            out = message({"type": "tool_use", "id": "toolu_fake1", "name": TOOL}, "tool_use")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(srv.server_port))
srv.serve_forever()
