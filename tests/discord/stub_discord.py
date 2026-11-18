"""시험용 가짜 Discord REST. 메시지·반응을 메모리에 두고 요청을 로그에 남김. 인자: 시드 JSON, 요청 로그, 포트 파일"""
import json, sys, re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit, parse_qs, unquote

seed = json.load(open(sys.argv[1])); LOG, PORTF = sys.argv[2], sys.argv[3]
BOT, CH = "900", "500"
msgs = {m["id"]: m for m in seed.get("messages", [])}
reactors = seed.get("reactors", {})  # "msgid|emoji" -> [user ids]
nid = [1000]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, obj=None):
        raw = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(raw)
    def handle_any(self, method):
        u = urlsplit(self.path); path = u.path.replace("/api/v10", "")
        n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n) if n else b""
        ctype = self.headers.get("Content-Type", "")
        rec = {"method": method, "path": unquote(path), "auth": self.headers.get("Authorization", "")}
        if body and ctype.startswith("application/json"): rec["body"] = json.loads(body)
        elif body: rec["multipart"] = True; rec["body_has_payload"] = b"payload_json" in body
        open(LOG, "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
        if seed.get("fail"): return self.send(500, {"message": "boom"})
        if path == "/users/@me/channels": return self.send(200, {"id": CH})
        m = re.fullmatch(r"/channels/(\d+)/messages", path)
        if m and method == "POST":
            nid[0] += 1; i = str(nid[0]); msgs[i] = {"id": i, "author": {"id": BOT}, "content": (rec.get("body") or {}).get("content", "")}
            return self.send(200, msgs[i])
        if m and method == "GET":
            after = int(parse_qs(u.query).get("after", ["0"])[0])
            return self.send(200, sorted([x for x in msgs.values() if int(x["id"]) > after], key=lambda x: -int(x["id"])))
        m = re.fullmatch(r"/channels/\d+/messages/(\d+)/reactions/([^/]+)(/@me)?", path)
        if m:
            if method == "PUT": return self.send(204)
            return self.send(200, [{"id": u} for u in reactors.get(m.group(1) + "|" + unquote(m.group(2)), [])])
        m = re.fullmatch(r"/channels/\d+/messages/(\d+)", path)
        if m and method == "DELETE": msgs.pop(m.group(1), None); return self.send(204)
        return self.send(404, {"message": "no route"})
    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")
    def do_PUT(self): self.handle_any("PUT")
    def do_DELETE(self): self.handle_any("DELETE")
    def do_PATCH(self): self.handle_any("PATCH")

s = HTTPServer(("127.0.0.1", 0), H); open(PORTF, "w").write(str(s.server_port)); s.serve_forever()
