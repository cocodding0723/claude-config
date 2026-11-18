"""격리 시험 실행기(test-box) 시험. 경우는 표 한 줄씩. 실행기가 상자 밖 쓰기·바깥 네트워크·바깥 비밀 파일 읽기를 막는지 실제로 돌려 봄."""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.environ.get("LRM_SCRIPT_DIR") or os.path.join(HERE, "..", "..", "hubs", "scripts")
BOX = os.path.join(SCRIPTS, "test-box")
OUTSIDE = "/tmp"  # 사용자 임시 폴더(/var/folders/…)가 아닌 곳. 실행기가 쓰기를 막아야 하는 자리

NET = """import socket, sys
s = socket.socket(); s.settimeout(2)
try: s.connect(("192.0.2.1", 9))
except OSError as e: sys.exit(0 if e.errno == 1 else 5)
sys.exit(6)
"""
LOCAL = """import http.server, sys, threading, urllib.error, urllib.request
srv = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
threading.Thread(target=srv.handle_request, daemon=True).start()
try: urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/", timeout=3)
except urllib.error.HTTPError: sys.exit(0)
sys.exit(7)
"""

# (설명, 파일 이름, 내용, 넘길 인자, 기대 종료 코드). {out} = 임시 폴더 밖의 폴더(운영 폴더 자리)
RUN_CASES = [
    ("상자 안 쓰기는 됨", "t.sh", 'echo a > "$TMPDIR/x" && [ -s "$TMPDIR/x" ]', [], 0),
    ("heredoc 을 쓸 수 있음(셸이 TMPDIR 과 무관하게 /var/tmp 에 임시 파일을 만듦)", "t.sh", "cat <<J >/dev/null\nx\nJ", [], 0),
    ("heredoc 자리(/var/tmp)의 다른 파일은 못 씀", "t.sh", 'echo a > /private/var/tmp/cc-box-probe 2>/dev/null; [ ! -e /private/var/tmp/cc-box-probe ]', [], 0),
    ("mktemp 로 만든 폴더에도 쓸 수 있음", "t.sh", 'd=$(mktemp -d) && echo a > "$d/x" && rm -rf "$d"', [], 0),
    ("상자 밖 쓰기는 막힘", "t.sh", 'echo a > "{out}/canary" 2>/dev/null; [ ! -e "{out}/canary" ]', [], 0),
    ("상자 밖 파일 지우기도 막힘", "t.sh", 'rm -f "{out}/keep" 2>/dev/null; [ -e "{out}/keep" ]', [], 0),
    ("시험이 띄운 자식 프로세스도 같은 상자", "t.sh", '/bin/sh -c \'echo a > "{out}/child"\' 2>/dev/null; [ ! -e "{out}/child" ]', [], 0),
    ("종료 코드를 그대로 돌려줌", "t.sh", "exit 3", [], 3),
    ("인자를 그대로 넘김", "t.sh", '[ "$1" = a ] && [ "$2" = "b c" ]', ["a", "b c"], 0),
    (".py 는 파이썬으로 실행", "t.py", "import sys; sys.exit(0 if sys.argv[1] == 'x' else 8)", ["x"], 0),
    ("바깥 네트워크 연결은 막힘", "t.py", NET, [], 0),
    ("로컬 가짜 서버 연결은 됨", "t.py", LOCAL, [], 0),
    ("임시 폴더 밖 .env 는 못 읽음", "t.sh", 'cat "{out}/real.env" 2>/dev/null; [ $? -ne 0 ]', [], 0),
    ("임시 폴더 밖 개인 키(.pem)는 못 읽음", "t.sh", 'cat "{out}/key.pem" 2>/dev/null; [ $? -ne 0 ]', [], 0),
    ("공개 인증서 묶음(cacert.pem)은 읽음(HTTPS 준비에 필요, 비밀 아님)", "t.sh", 'grep -q PUBLIC "{out}/cacert.pem"', [], 0),
    ("상자 안 .env 는 읽음", "t.sh", 'echo K=1 > "$TMPDIR/fake.env" && grep -q K=1 "$TMPDIR/fake.env"', [], 0),
    ("상자 안에서 실행기를 다시 불러도 바깥 쓰기는 막힘", "t.sh", 'echo \'echo a > "{out}/inner"\' > "$TMPDIR/in.sh"; "{box}" "$TMPDIR/in.sh" 2>/dev/null; [ ! -e "{out}/inner" ]', [], 0),
]

# (설명, 인자, 기대 종료 코드, stderr 에 들어 있어야 할 말)
USAGE_CASES = [
    ("임시 폴더를 못 만들면 돌리지 않고 4(현재 폴더를 상자로 착각해 지우지 않음)", ["__NOTMP__"], 4, "임시 폴더"),
    ("인자 없음", [], 2, "사용법"),
    ("없는 스크립트", ["{out}/없음.sh"], 2, "없음"),
    ("모르는 옵션", ["--zzz", "x.sh"], 2, "사용법"),
]


class BoxTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="cc-box-", dir=OUTSIDE)
        self.addCleanup(tmp.cleanup)
        self.out = os.path.realpath(tmp.name)
        for name, text in (("keep", "x"), ("real.env", "SECRET=1"), ("key.pem", "PRIVATE"), ("cacert.pem", "PUBLIC")):
            with open(os.path.join(self.out, name), "w", encoding="utf-8") as f:
                f.write(text)

    def fill(self, s):
        return s.replace("{out}", self.out).replace("{box}", BOX)

    def run_box(self, args, env=None, cwd=None):
        return subprocess.run([BOX, *args], capture_output=True, text=True, timeout=60, env=env, cwd=cwd)

    def write(self, fname, body):
        path = os.path.join(self.out, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.fill(body) + "\n")
        return path

    def test_scripts_run_confined(self):
        for name, fname, body, args, want in RUN_CASES:
            with self.subTest(name):
                r = self.run_box([self.write(fname, body), *args])
                self.assertEqual(r.returncode, want, r.stderr[-300:])

    def test_bad_usage_exits_2_with_reason(self):
        for name, args, want, word in USAGE_CASES:
            with self.subTest(name):
                env, cwd = None, None
                if args == ["__NOTMP__"]:  # 없는 폴더를 임시 폴더로 줌. 현재 폴더에 지켜볼 파일을 둠
                    args, cwd = [self.write("t.sh", "exit 0")], self.out
                    env = dict(os.environ, TMPDIR=os.path.join(self.out, "없는 폴더"))
                r = self.run_box([self.fill(a) for a in args], env=env, cwd=cwd)
                self.assertEqual(r.returncode, want)
                self.assertIn(word, r.stderr)
                self.assertTrue(os.path.exists(os.path.join(self.out, "keep")))

    def test_box_is_removed_after_run_and_kept_on_request(self):
        show = self.write("t.sh", 'echo "$TMPDIR"')
        gone = self.run_box([show]).stdout.strip()
        self.assertTrue(gone and not os.path.exists(gone), gone)
        r = self.run_box(["--keep", show])
        kept = r.stdout.strip()
        self.addCleanup(subprocess.run, ["rm", "-rf", kept])
        self.assertTrue(os.path.isdir(kept))
        self.assertIn(kept, r.stderr)


if __name__ == "__main__":
    unittest.main()
