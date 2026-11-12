"""slack_poll.sh 의 is_slack_url·curl_conf 시험(토큰이 slack.com 밖이나 주입된 설정으로 가지 않는지).
사용: url_guard.py <slack_poll.sh 경로> <offline|curl>
  offline  파이썬 판정만 시험(항상 실행)
  curl     실제 curl 과 로컬 HTTP 서버 2개(127.0.0.1)로 설정 전달·주입 차단까지 시험(run.sh 에서 REAL_CURL=1 일 때)
가짜 토큰만 쓰고 임시 폴더는 TemporaryDirectory 가 정리함.
출력: 'PASS  이름' / 'FAIL  이름 — 사유' 줄. 실패가 있으면 종료 코드 1.
"""
import http.server
import os
import subprocess
import sys
import tempfile
import threading

FAKE = "fake-token-urlguard-0123456789"
os.environ["SLACK_BOT_TOKEN"] = FAKE
os.environ["SLACK_USER_ID"] = "Ume"
os.environ["LRM_PY_DIR"] = os.path.dirname(os.path.abspath(sys.argv[1]))  # 인라인 파이썬이 공용 모듈을 찾는 폴더
SRC, WITH_CURL = sys.argv[1], sys.argv[2] == "curl"
text = open(SRC, encoding="utf-8").read()
py = text.split("<<'PY' || RC=$?\n", 1)[1].split("\nPY\n", 1)[0]
defs = py.split("\ntry:\n    n = main()", 1)[0]
LOG = []
FAILS = []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        auth = self.headers.get("Authorization")
        LOG.append((self.server.server_address[1], self.path, "TOKEN" if auth == f"Bearer {FAKE}" else "no-auth"))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def serve():
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def check(name, cond, why=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f" — {why}"))
    if not cond:
        FAILS.append(name)


def conf_or_none(curl_conf, url):
    try:
        return curl_conf(url)
    except ValueError:
        return None


with tempfile.TemporaryDirectory(prefix="cc-urlguard.") as tmpd:
    sys.argv = ["x", tmpd, "7", "0"]
    g = {"__name__": "x"}
    exec(compile(defs, "slack_poll_py", "exec"), g)
    curl_conf, is_slack_url, slack_url = g["curl_conf"], g["is_slack_url"], g["slack_url"]
    SEPS = (("개행", "\n"), ("CR", "\r"), ("탭", "\t"), ("널", "\x00"))

    check("is_slack_url: 정상 파일 주소 허용", is_slack_url("https://files.slack.com/files-pri/T1-F1/a.pdf?x=1"))
    check("is_slack_url: 대문자 호스트 허용", is_slack_url("HTTPS://FILES.SLACK.COM/x"))
    for label, sep in SEPS:
        check(f"is_slack_url 이 {label} 든 주소를 거부",
              not is_slack_url(f"https://files.slack.com/a{sep}url = https://evil.example/x"))
        check(f"curl_conf 가 {label} 든 주소를 거부(이중 방어)", conf_or_none(curl_conf, f"https://files.slack.com/a{sep}url = x") is None)
    ko_url = "https://files.slack.com/files-pri/T1-F1/download/보고서.pdf"
    check("is_slack_url: 한글 경로 허용", is_slack_url(ko_url))
    check("slack_url: 한글만 퍼센트 인코딩해 반환",
          slack_url(ko_url) == "https://files.slack.com/files-pri/T1-F1/download/%EB%B3%B4%EA%B3%A0%EC%84%9C.pdf")
    check("slack_url: ASCII 주소는 그대로 반환", slack_url("https://files.slack.com/a/b.pdf?x=1") == "https://files.slack.com/a/b.pdf?x=1")
    check("slack_url: 인코딩 결과에 제어 문자·공백·따옴표·역슬래시 없음", all(c not in slack_url(ko_url) for c in ' "\\\n\r\t\x00'))
    for label, ch in (("U+2028", "\u2028"), ("U+2029", "\u2029"), ("U+0085", "\x85"), ("NBSP", "\xa0")):
        check(f"is_slack_url 이 {label} 든 주소를 거부", not is_slack_url(f"https://files.slack.com/a{ch}url = x"))
    check("is_slack_url: 한글 호스트 거부", not is_slack_url("https://보고서.slack.com/x"))
    check("is_slack_url: 짝 없는 서로게이트 거부", not is_slack_url("https://files.slack.com/a\ud800"))
    for bad in ("https://files.slack.com/a b", 'https://files.slack.com/a"b', "https://files.slack.com/a\\b",
                "https://files.slack.com@evil.example/", "https://evil.example#@files.slack.com/",
                "https://evil.example\\@files.slack.com/", "https://xslack.com/",
                "https://files.slack.com.evil.example/", "http://files.slack.com/a", "https://ſlack.com/",
                "https://files.slack.com:8443/x", None, 5):
        check(f"is_slack_url 거부: {bad!r}", not is_slack_url(bad))

    if WITH_CURL:
        s1, s2 = serve(), serve()
        p1, p2 = s1.server_address[1], s2.server_address[1]

        def run(conf):
            LOG.clear()
            p = subprocess.run(["curl", "-q", "-sS", "--max-time", "5", "-o", os.path.join(tmpd, "o"), "-K", "-"],
                               input=conf, capture_output=True, text=True, timeout=15)
            return p.returncode, list(LOG)

        rc, log = run(curl_conf(f"http://127.0.0.1:{p1}/ok"))
        check("실제 curl: -K - 설정의 헤더가 전달됨(대조군)", rc == 0 and log == [(p1, "/ok", "TOKEN")], f"rc={rc} {log}")
        rc, log = run(curl_conf(f'http://127.0.0.1:{p1}/q"x\\y'))
        check("실제 curl: \" 와 \\ 이스케이프 왕복", rc == 0 and log == [(p1, '/q"x\\y', "TOKEN")], f"rc={rc} {log}")
        for label, sep in SEPS[:3]:
            conf = conf_or_none(curl_conf, f"http://127.0.0.1:{p1}/a{sep}url = http://127.0.0.1:{p2}/steal")
            check(f"실제 curl: {label} 주입 주소는 설정이 만들어지지 않아 두 번째 서버에 토큰이 안 감", conf is None)
            if conf is not None:
                rc, log = run(conf)
                check(f"실제 curl: {label} 주입이 다른 서버로 토큰을 보내지 않음", all(e[0] != p2 for e in log), f"{log}")
        rc, log = run(curl_conf(slack_url("https://files.slack.com/보고서.pdf").replace("https://files.slack.com", f"http://127.0.0.1:{p1}")))
        check("실제 curl: 인코딩된 한글 경로가 그대로 전달됨", rc == 0 and log == [(p1, "/%EB%B3%B4%EA%B3%A0%EC%84%9C.pdf", "TOKEN")], f"rc={rc} {log}")
        s1.shutdown()
        s2.shutdown()

sys.exit(1 if FAILS else 0)
