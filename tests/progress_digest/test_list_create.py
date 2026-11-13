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
            out = {"ok": True, "list_id": "FNEW001", "list_metadata": {"schema": [{"key": "title", "id": "ColA"}, {"key": "done", "id": "ColB"},
                 {"key": "kind", "id": "ColK", "options": {"choices": [{"value": "OptD", "label": "🙋 결정"}, {"value": "OptM", "label": "🔀 머지"}, {"value": "OptI", "label": "✍️ 직접 입력"}, {"value": "OptR", "label": "👀 검수"}]}}]}}
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


class Base(unittest.TestCase):
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
             "SLACK_USER_ID": "UFAKEUSER1", "LRM_BOTLIST_LOCAL_ONLY": "1", "LRM_BOTLIST_STATE": self.state,
             "LRM_HUB_DIR": os.path.join(self.dir, "empty-hub")}  # 실제 봇 환경 파일을 읽지 않게 격리
        e.update(env)
        return subprocess.run([sys.executable, "-I", SCRIPT, *args], env=e, capture_output=True, text=True)

    def read_state(self):
        with open(self.state, encoding="utf-8") as f:
            return f.read()

    def methods(self):
        return [c[0] for c in Fake.calls]


class ListCreate(Base):
    def test_create_and_share(self):
        r = self.run_cli("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual((out["id"], out["url"]), ("FNEW001", "https://ws.slack.com/lists/T123/FNEW001"))
        self.assertEqual(self.methods(), ["slackLists.create", "auth.test", "slackLists.access.set"])
        create, share = Fake.calls[0][1], Fake.calls[2][1]
        self.assertEqual(create["name"], "강비서 할 일")
        self.assertEqual([c["key"] for c in create["schema"]], ["title", "kind", "urgency", "hub", "answer", "done", "link", "archive", "order"])
        self.assertEqual(share, {"list_id": "FNEW001", "access_level": "owner", "user_ids": ["UFAKEUSER1"]})
        st = json.loads(self.read_state())["lists"]["강비서 할 일"]
        self.assertTrue(st["shared"])
        self.assertEqual(st["columns"], {"title": "ColA", "done": "ColB", "kind": "ColK"})
        self.assertEqual(st["options"]["kind"], {"decision": "OptD", "merge": "OptM", "input": "OptI", "review": "OptR"})  # Slack 이 값을 새로 매긴 경우

    def test_replace_keeps_old_record(self):
        self.run_cli()
        Fake.calls = []
        r = self.run_cli("--replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.methods(), ["slackLists.create", "auth.test", "slackLists.access.set"])
        st = json.loads(self.read_state())
        self.assertEqual(len(st["replaced"]), 1)
        self.assertEqual(st["replaced"][0]["id"], "FNEW001")

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


class BotEnvFile(Base):
    """환경 변수 대신 봇 환경 파일에서 토큰·사용자 ID 를 읽는 경로"""

    def run_file(self, content, **env):
        hub = os.path.join(self.dir, "hub")
        os.makedirs(hub, exist_ok=True)
        with open(os.path.join(hub, "slack-bot.env"), "w") as f:
            f.write(content)
        e = {"PATH": os.environ["PATH"], "SLACK_API_BASE": f"http://127.0.0.1:{self.srv.server_port}/api",
             "LRM_BOTLIST_LOCAL_ONLY": "1", "LRM_BOTLIST_STATE": self.state, "LRM_HUB_DIR": hub}
        e.update(env)
        return subprocess.run([sys.executable, "-I", SCRIPT], env=e, capture_output=True, text=True)

    def test_token_and_user_from_file(self):
        r = self.run_file(f"# 주석\nOTHER=zzz\nSLACK_BOT_TOKEN={TOKEN}\nexport SLACK_USER_ID=\"UFILEUSER1\"\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Fake.calls[-1][1]["user_ids"], ["UFILEUSER1"])
        self.assertTrue(all(c[2] == "Bearer " + TOKEN for c in Fake.calls))
        self.assertNotIn(TOKEN, r.stdout + r.stderr)

    def test_env_var_wins_over_file(self):
        self.run_file(f"SLACK_BOT_TOKEN=wrong\nSLACK_USER_ID=UFILEUSER1\n", SLACK_BOT_TOKEN=TOKEN, SLACK_USER_ID="UENVUSER01")
        self.assertEqual(Fake.calls[-1][1]["user_ids"], ["UENVUSER01"])
        self.assertTrue(all(c[2] == "Bearer " + TOKEN for c in Fake.calls))

    def test_file_without_token_errors_cleanly(self):
        r = self.run_file("SLACK_USER_ID=UFILEUSER1\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("slack-bot.env", r.stderr)
        self.assertEqual(Fake.calls, [])

    def test_no_file_no_env_errors_cleanly(self):
        r = self.run_cli(SLACK_BOT_TOKEN="", SLACK_USER_ID="", LRM_HUB_DIR=os.path.join(self.dir, "none"))
        self.assertEqual(r.returncode, 2)

    def test_wrapper_removed(self):
        self.assertFalse(os.path.exists(os.path.join(SCRIPTS, "with_bot_env.sh")))


if __name__ == "__main__":
    unittest.main()
