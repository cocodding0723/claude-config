"""slack_poll.sh 시험용 가짜 Slack API. 실제 Slack·토큰을 쓰지 않음.
사용: fake_slack.py <응답 폴더> <포트 기록 파일>
GET /<method> 에 <응답 폴더>/<method>.json 내용을 그대로 돌려줌(없으면 {"ok":false,"error":"no_fixture"}).
"""
import http.server
import os
import sys
import urllib.parse

FIX, PORT_FILE = sys.argv[1], sys.argv[2]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        method = os.path.basename(urllib.parse.urlsplit(self.path).path)
        path = os.path.join(FIX, method + ".json")
        body = open(path, "rb").read() if os.path.exists(path) else b'{"ok":false,"error":"no_fixture"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
with open(PORT_FILE, "w") as f:
    f.write(str(server.server_address[1]))
server.serve_forever()
