"""slack_poll.sh 시험용 가짜 Slack API. 실제 Slack·토큰을 쓰지 않음.
사용: fake_slack.py <응답 폴더> <포트 기록 파일> [<호출 기록 파일>]
GET /<method> 에 <응답 폴더>/<method>.json 내용을 그대로 돌려줌(없으면 {"ok":false,"error":"no_fixture"}).
reactions.add·reactions.remove 는 <method>.<timestamp>.json(메시지별) → <method>.json 순으로 찾고, 둘 다 없으면 {"ok":true}.
conversations.replies 는 conversations.replies.<ts>.json(스레드별)이 있으면 그것을 씀.
chat.postMessage 는 <응답 폴더>/chat.postMessage.json 이 없으면 {"ok":true} (호출 기록의 쿼리에 channel·thread_ts·text 가 남음).
<응답 폴더>/hang.<method> 가 있으면 그 안의 초만큼 응답을 늦춤(호출 중 프로세스가 죽는 상황 시험용).
호출 기록 파일을 주면 요청마다 "<method>\\t<쿼리>" 한 줄을 덧붙임(토큰은 헤더라 기록되지 않음).
"""
import http.server
import os
import sys
import time
import urllib.parse

FIX, PORT_FILE = sys.argv[1], sys.argv[2]
CALL_LOG = sys.argv[3] if len(sys.argv) > 3 else None
REACTION_METHODS = ("reactions.add", "reactions.remove")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        """form 본문 POST(chat.update). 본문을 쿼리처럼 기록·해석하고 호출 기록의 메서드 앞에 POST: 를 붙임"""
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        self.handle_call(urllib.parse.urlsplit(self.path).path, body, "POST:")

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        self.handle_call(parts.path, parts.query, "")

    def handle_call(self, path, query, prefix):
        parts = urllib.parse.SplitResult("", "", path, query, "")
        method = os.path.basename(parts.path)
        if CALL_LOG:
            with open(CALL_LOG, "a", encoding="utf-8") as f:
                f.write(f"{prefix}{method}\t{parts.query}\n")
        hang = os.path.join(FIX, f"hang.{method}")
        if os.path.exists(hang):
            time.sleep(float(open(hang).read().strip() or "3"))
        ts = urllib.parse.parse_qs(parts.query).get("timestamp", [""])[0]
        path = os.path.join(FIX, method + ".json")
        if method in REACTION_METHODS:
            per_msg = os.path.join(FIX, f"{method}.{ts}.json")
            path = per_msg if os.path.exists(per_msg) else path
            default = b'{"ok":true}'
        elif method == "conversations.replies":
            per_thread = os.path.join(FIX, f"{method}.{urllib.parse.parse_qs(parts.query).get('ts', [''])[0]}.json")
            path = per_thread if os.path.exists(per_thread) else path
            default = b'{"ok":false,"error":"no_fixture"}'
        elif method == "chat.postMessage":
            # ts 는 호출 순번으로 만든 가짜 값. 응답 폴더에 chat.postMessage.json 이 있으면 그대로 씀
            n = sum(1 for _ in open(CALL_LOG, encoding="utf-8") if _.startswith("chat.postMessage")) if CALL_LOG else 0
            default = ('{"ok":true,"channel":"Dfake","ts":"9999.%06d"}' % n).encode()
        elif method == "chat.update":
            default = b'{"ok":true}'
        else:
            default = b'{"ok":false,"error":"no_fixture"}'
        body = open(path, "rb").read() if os.path.exists(path) else default
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):  # 응답 대기 중 클라이언트가 죽은 경우(hang 시험)
            pass

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
with open(PORT_FILE, "w") as f:
    f.write(str(server.server_address[1]))
server.serve_forever()
