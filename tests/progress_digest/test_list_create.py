"""slack_list_create.py 시험. 가짜 Slack(127.0.0.1)만 씀. 실제 Slack·토큰 파일은 쓰지 않음."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts")
SCRIPT = os.path.join(SCRIPTS, "slack_list_create.py")
WRAPPER = os.path.join(SCRIPTS, "with_bot_env.sh")
TOKEN = "-".join(["fake", "secret", "value", "123"])  # 실제 토큰 모양을 피함(비밀 패턴 검사)


class Fake(BaseHTTPRequestHandler):
    calls = []
    fail = {}  # method -> 오류 코드

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        m = self.path.rsplit("/", 1)[-1]
        Fake.calls.append((m, body, self.headers.get("Authorization")))
        if m in Fake.fail:
            out = {"ok": False, "error": Fake.fail[m], "needed": "lists:write"}
        elif m == "slackLists.create":
            out = {"ok": True, "list_id": "FNEW001", "list_metadata": {"schema": [{"key": "title", "id": "ColA"}, {"key": "done", "id": "ColB"}]}}
        elif m == "auth.test":
            out = {"ok": True, "url": "https://ws.slack.com/", "team_id": "T123"}
        else:
            out = {"ok": True}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class ListCreate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Fake.calls, Fake.fail = [], {}
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = t.name
        self.state = os.path.join(t.name, "bot-list.json")

    def run_cli(self, *args, **env):
        e = {"PATH": os.environ["PATH"], "SLACK_API_BASE": f"http://127.0.0.1:{self.srv.server_port}/api", "SLACK_BOT_TOKEN": TOKEN,
             "SLACK_USER_ID": "UFAKEUSER1", "LRM_BOTLIST_LOCAL_ONLY": "1", "LRM_BOTLIST_STATE": self.state}
        e.update(env)
        return subprocess.run([sys.executable, "-I", SCRIPT, *args], env=e, capture_output=True, text=True)

    def read_state(self):
        with open(self.state, encoding="utf-8") as f:
            return f.read()

    def methods(self):
        return [c[0] for c in Fake.calls]

    def test_create_and_share(self):
        r = self.run_cli("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual((out["id"], out["url"]), ("FNEW001", "https://ws.slack.com/lists/T123/FNEW001"))
        self.assertEqual(self.methods(), ["slackLists.create", "auth.test", "slackLists.access.set"])
        create, share = Fake.calls[0][1], Fake.calls[2][1]
        self.assertEqual(create["name"], "강비서 할 일")
        self.assertEqual([c["key"] for c in create["schema"]], ["title", "done", "link"])
        self.assertEqual(share, {"list_id": "FNEW001", "access_level": "owner", "user_ids": ["UFAKEUSER1"]})
        st = json.loads(self.read_state())["lists"]["강비서 할 일"]
        self.assertTrue(st["shared"])
        self.assertEqual(st["columns"], {"title": "ColA", "done": "ColB"})

    def test_rerun_no_change(self):
        self.run_cli()
        Fake.calls = []
        before = self.read_state()
        r = self.run_cli()
        self.assertEqual(r.returncode, 0)
        self.assertIn("변화 없음", r.stdout)
        self.assertEqual(Fake.calls, [])
        self.assertEqual(self.read_state(), before)

    def test_share_failure_then_retry_does_not_recreate(self):
        Fake.fail = {"slackLists.access.set": "user_not_found"}
        self.assertEqual(self.run_cli().returncode, 1)
        Fake.calls, Fake.fail = [], {}
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(self.methods(), ["slackLists.access.set"])

    def test_missing_scope(self):
        Fake.fail = {"slackLists.create": "missing_scope"}
        r = self.run_cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing_scope", r.stderr)
        self.assertIn("lists:write", r.stderr)
        self.assertFalse(os.path.exists(self.state))

    def test_dry_run_no_calls_no_state(self):
        r = self.run_cli("--dry-run", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["action"], "create+share")
        self.assertEqual(Fake.calls, [])
        self.assertFalse(os.path.exists(self.state))

    def test_token_never_printed(self):
        for args, env in ((("--json",), {}), (("--dry-run",), {}), ((), {"SLACK_API_BASE": "http://127.0.0.1:1/api"})):
            r = self.run_cli(*args, **env)
            self.assertNotIn(TOKEN, r.stdout + r.stderr)
        self.assertNotIn(TOKEN, self.read_state())

    def test_bearer_sent_to_fake_only(self):
        self.run_cli()
        self.assertTrue(all(c[2] == "Bearer " + TOKEN for c in Fake.calls))

    def test_remote_api_refused_in_local_only(self):
        r = self.run_cli(SLACK_API_BASE="https://slack.com/api")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(Fake.calls, [])

    def test_missing_token_and_bad_user(self):
        self.assertEqual(self.run_cli(SLACK_BOT_TOKEN="").returncode, 2)
        self.assertEqual(self.run_cli(SLACK_USER_ID="nobody").returncode, 2)
        self.assertEqual(Fake.calls, [])

    def test_help(self):
        r = subprocess.run([sys.executable, "-I", SCRIPT, "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--dry-run", r.stdout)


class Wrapper(unittest.TestCase):
    def test_wrapper_loads_env_silently(self):
        with tempfile.TemporaryDirectory() as d:
            envf = os.path.join(d, "x.env")
            with open(envf, "w") as f:
                f.write("SLACK_BOT_TOKEN=" + TOKEN + "\n")
            r = subprocess.run(["bash", WRAPPER, "sh", "-c", 'test "$SLACK_BOT_TOKEN" = ' + TOKEN],
                               env={"PATH": os.environ["PATH"], "LRM_BOT_ENV": envf}, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_wrapper_missing_env_fails_with_hint(self):
        r = subprocess.run(["bash", WRAPPER, "true"], env={"PATH": os.environ["PATH"], "LRM_BOT_ENV": "/없는/env"}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("slack-bot.env", r.stderr)


if __name__ == "__main__":
    unittest.main()
