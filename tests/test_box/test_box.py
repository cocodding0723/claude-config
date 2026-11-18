"""격리 시험 실행기(test-box) 시험. 경우는 표 한 줄씩. 실행기가 상자 밖 쓰기·바깥 연결·비밀 파일 읽기를 막는지 실제로 돌려 봄.
새 경우는 표에 줄을 더함. 자리 표시: {out} 상자 밖 폴더(운영 폴더 자리), {box} 실행기, {pid} 상자 밖 프로세스, {utmp} 사용자 임시 폴더."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.environ.get("LRM_SCRIPT_DIR") or os.path.join(HERE, "..", "..", "hubs", "scripts")
BOX = os.path.join(SCRIPTS, "test-box")
RULES = os.path.join(SCRIPTS, "test-box.sb")
OUTSIDE = "/tmp"  # 사용자 임시 폴더(/var/folders/…)가 아닌 곳. 실행기가 쓰기를 막아야 하는 자리
USER_TMP = os.path.realpath(subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True).stdout.strip() or "/tmp")
ERR = "test-box:"  # 실행기 자신의 오류에 붙는 머리말(시험이 낸 같은 종료 코드와 구분)

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
SOCK_OUT = """import socket, sys
s = socket.socket(socket.AF_UNIX)
try: s.connect("{out}/s.sock")
except OSError as e: sys.exit(0 if e.errno == 1 else 5)
sys.exit(6)
"""
SOCK_IN = """import os, socket
p = os.path.join(os.environ["TMPDIR"], "s.sock")
a = socket.socket(socket.AF_UNIX); a.bind(p); a.listen(1)
socket.socket(socket.AF_UNIX).connect(p)
"""
IN_BOX = 'case "$1" in "$TMPDIR"/*) ;; *) exit 9 ;; esac'

# (설명, 파일 이름, 내용, 넘길 인자, 기대 종료 코드)
RUN_CASES = [
    ("상자 안 쓰기는 됨", "t.sh", 'echo a > "$TMPDIR/x" && [ -s "$TMPDIR/x" ]', [], 0),
    ("heredoc 을 쓸 수 있음(셸이 TMPDIR 과 무관하게 /var/tmp 에 임시 파일을 만듦)", "t.sh", "cat <<J >/dev/null\nx\nJ", [], 0),
    ("heredoc 자리(/var/tmp)의 다른 파일은 못 씀", "t.sh", 'echo a > /private/var/tmp/cc-box-probe 2>/dev/null; [ ! -e /private/var/tmp/cc-box-probe ]', [], 0),
    ("mktemp 로 만든 폴더와 파일은 상자 안에 생김", "t.sh", 'inbox() { ' + IN_BOX + '; }; d=$(mktemp -d) && inbox "$d" && f=$(mktemp) && inbox "$f" && echo a > "$d/x"', [], 0),
    ("mktemp -t 도 상자 안에, 틀을 준 mktemp 는 그 자리에", "t.sh", 'inbox() { ' + IN_BOX + '; }; a=$(mktemp -d -t cc) && inbox "$a" && b=$(mktemp "$TMPDIR/q.XXXXXX") && [ -f "$b" ] && c=$(mktemp -t cc) && inbox "$c"', [], 0),
    ("시스템 도구 대리 실행 파일(xcrun)이 캐시를 못 써서 경고를 출력에 섞지 않음", "t.sh", '[ -x /usr/bin/python3 ] || exit 0; h=$(mktemp -d); out=$(HOME="$h" /usr/bin/python3 -c "print(1)" 2>&1); [ "$out" = 1 ]', [], 0),
    ("사용자 임시 폴더(상자 밖)에는 못 씀", "t.sh", 'echo a > "{utmp}/cc-box-probe-{pid}" 2>/dev/null; [ ! -e "{utmp}/cc-box-probe-{pid}" ]', [], 0),
    ("상자 밖 쓰기는 막힘", "t.sh", 'echo a > "{out}/canary" 2>/dev/null; [ ! -e "{out}/canary" ]', [], 0),
    ("상자 밖 파일 지우기도 막힘", "t.sh", 'rm -f "{out}/keep" 2>/dev/null; [ -e "{out}/keep" ]', [], 0),
    ("하드링크로 바깥 파일에 닿기 막힘", "t.sh", 'ln "{out}/keep" "$TMPDIR/l" 2>/dev/null; [ ! -e "$TMPDIR/l" ]', [], 0),
    ("시험이 띄운 자식 프로세스도 같은 상자", "t.sh", '/bin/sh -c \'echo a > "{out}/child"\' 2>/dev/null; [ ! -e "{out}/child" ]', [], 0),
    ("상자 안에서 실행기를 다시 불러도 바깥 쓰기는 막힘", "t.sh", 'echo \'echo a > "{out}/inner"\' > "$TMPDIR/in.sh"; "{box}" "$TMPDIR/in.sh" 2>/dev/null; [ ! -e "{out}/inner" ]', [], 0),
    ("종료 코드를 그대로 돌려줌", "t.sh", "exit 3", [], 3),
    ("인자를 그대로 넘김", "t.sh", '[ "$1" = a ] && [ "$2" = "b c" ]', ["a", "b c"], 0),
    (".py 는 파이썬으로 실행", "t.py", "import sys; sys.exit(0 if sys.argv[1] == 'x' else 8)", ["x"], 0),
    ("확장자 대문자(.PY)도 파이썬으로 실행", "T.PY", "import sys; sys.exit(0)", [], 0),
    ("바깥 네트워크 연결은 막힘", "t.py", NET, [], 0),
    ("로컬 가짜 서버 연결은 됨", "t.py", LOCAL, [], 0),
    ("상자 밖 프로세스의 유닉스 소켓에는 연결 못 함", "t.py", SOCK_OUT, [], 0),
    ("상자 안에 만든 유닉스 소켓은 됨", "t.py", SOCK_IN, [], 0),
    ("상자 밖 프로세스에는 신호를 못 보냄", "t.sh", "kill -0 {pid} 2>/dev/null; [ $? -ne 0 ]", [], 0),
    ("자기가 띄운 프로세스에는 신호를 보냄(가짜 서버 정리)", "t.sh", "sleep 30 & kill $!", [], 0),
    ("상자 밖 .env 는 못 읽음", "t.sh", 'cat "{out}/real.env" 2>/dev/null; [ $? -ne 0 ]', [], 0),
    ("사용자 임시 폴더의 .env 도 못 읽음", "t.sh", 'cat "{utmp_env}" 2>/dev/null; [ $? -ne 0 ]', [], 0),
    ("상자 밖 개인 키(.pem)는 못 읽음", "t.sh", 'cat "{out}/key.pem" 2>/dev/null; [ $? -ne 0 ]', [], 0),
    ("공개 인증서 묶음(cacert.pem)은 읽음(HTTPS 준비에 필요, 비밀 아님)", "t.sh", 'grep -q PUBLIC "{out}/cacert.pem"', [], 0),
    ("상자 안 .env 는 읽음", "t.sh", 'echo K=1 > "$TMPDIR/fake.env" && grep -q K=1 "$TMPDIR/fake.env"', [], 0),
]

# 호출자가 환경 변수로 실행기를 속이려는 경우. (설명, 환경 변수, 생기면 안 되는 파일)
# 상자를 씌우기 전(실행기 자신)에 바깥에서 무언가 실행되면 안 됨
ENV_CASES = [
    ("BASH_ENV 로 넘긴 파일이 상자 밖에서 실행되지 않음", {"BASH_ENV": "{out}/plant.sh"}, "{out}/planted"),
    ("PATH 앞에 둔 가짜 mktemp 가 상자 밖에서 실행되지 않음", {"PATH": "{out}/bin:" + os.environ.get("PATH", "")}, "{out}/planted"),
    ("TMPDIR 을 운영 폴더로 줘도 상자가 그 안에 생기지 않음", {"TMPDIR": "{out}/prot"}, "{out}/prot/x"),
]

# (설명, 인자, 기대 종료 코드, stderr 에 들어 있어야 할 말)
USAGE_CASES = [
    ("인자 없음", [], 2, "사용법"),
    ("없는 스크립트", ["{out}/없음.sh"], 2, ERR),
    ("모르는 옵션", ["--zzz", "x.sh"], 2, ERR),
]


class BoxTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="cc-box-", dir=OUTSIDE)
        self.addCleanup(tmp.cleanup)
        self.out = os.path.realpath(tmp.name)
        for name, text in (("keep", "x"), ("real.env", "SECRET=1"), ("key.pem", "PRIVATE"), ("cacert.pem", "PUBLIC"),
                           ("plant.sh", f'echo x > "{self.out}/planted"')):
            with open(os.path.join(self.out, name), "w", encoding="utf-8") as f:
                f.write(text + "\n")
        os.makedirs(os.path.join(self.out, "prot"))
        os.makedirs(os.path.join(self.out, "bin"))
        fake = os.path.join(self.out, "bin", "mktemp")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/bash\necho x > "{self.out}/planted"\nexec /usr/bin/mktemp "$@"\n')
        os.chmod(fake, 0o755)
        # 상자 밖에서 도는 것들: 프로세스 하나, 유닉스 소켓 하나, 사용자 임시 폴더의 비밀 파일 하나
        self.proc = subprocess.Popen(["sleep", "120"])
        self.addCleanup(self.proc.kill)
        self.addCleanup(lambda p=os.path.join(USER_TMP, f"cc-box-probe-{self.proc.pid}"): os.path.exists(p) and os.remove(p))
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(os.path.join(self.out, "s.sock"))
        self.sock.listen(1)
        self.addCleanup(self.sock.close)
        fd, self.utmp_env = tempfile.mkstemp(suffix=".env", dir=USER_TMP)
        os.write(fd, b"SECRET=1\n")
        os.close(fd)
        self.addCleanup(os.remove, self.utmp_env)

    def fill(self, s):
        for k, v in (("{out}", self.out), ("{box}", BOX), ("{pid}", str(self.proc.pid)), ("{utmp_env}", self.utmp_env), ("{utmp}", USER_TMP)):
            s = s.replace(k, v)
        return s

    def run_box(self, args, env=None, cwd=None, box=BOX):
        return subprocess.run([box, *args], capture_output=True, text=True, timeout=60, env=env, cwd=cwd)

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

    def test_caller_environment_cannot_run_code_outside_the_box(self):
        script = self.write("t.sh", 'echo x > "$TMPDIR/x"; echo "$TMPDIR"')
        for name, env, must_not_exist in ENV_CASES:
            with self.subTest(name):
                r = self.run_box([script], env={**os.environ, **{k: self.fill(v) for k, v in env.items()}})
                self.assertEqual(r.returncode, 0, r.stderr[-300:])
                self.assertFalse(os.path.exists(self.fill(must_not_exist)))
                self.assertFalse(r.stdout.strip().startswith(self.out), r.stdout)
                self.assertEqual(os.listdir(os.path.join(self.out, "prot")), [])

    def test_bad_usage_exits_2_with_reason(self):
        for name, args, want, word in USAGE_CASES:
            with self.subTest(name):
                r = self.run_box([self.fill(a) for a in args])
                self.assertEqual(r.returncode, want)
                self.assertIn(word, r.stderr)

    def test_refuses_to_run_without_working_rules(self):
        """상자 규칙이 없거나 깨졌으면 격리 없이 돌리지 않고 4"""
        script = self.write("t.sh", "echo ran")
        for name, rules in (("규칙 파일 없음", None), ("규칙 파일 문법 오류", "(version 1)(bogus")):
            with self.subTest(name):
                d = tempfile.mkdtemp(dir=self.out)
                shutil.copy(BOX, d)
                if rules is not None:
                    with open(os.path.join(d, "test-box.sb"), "w", encoding="utf-8") as f:
                        f.write(rules)
                r = self.run_box([script], box=os.path.join(d, "test-box"))
                self.assertEqual((r.returncode, r.stdout), (4, ""))
                self.assertIn(ERR, r.stderr)

    def test_file_name_starting_with_dash(self):
        self.write("-x.sh", "exit 0")
        self.assertEqual(self.run_box(["--", "-x.sh"], cwd=self.out).returncode, 0)

    def test_box_is_removed_after_run_and_kept_on_request(self):
        show = self.write("t.sh", 'echo "$TMPDIR"')
        gone = self.run_box([show]).stdout.strip()
        self.assertTrue(gone and not os.path.exists(gone), gone)
        fail = self.write("f.sh", 'echo "$TMPDIR"; exit 3')
        r = self.run_box(["--keep", fail])
        kept = r.stdout.strip()
        self.addCleanup(subprocess.run, ["rm", "-rf", kept])
        self.assertEqual(r.returncode, 3)
        self.assertTrue(os.path.isdir(kept))
        self.assertIn(kept, r.stderr)


if __name__ == "__main__":
    unittest.main()
