"""todo_list_migrate.py 시험: 백업 선행·봇이 만든 행만 삭제. 가짜 API(127.0.0.1)만 씀."""
import glob
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts", "todo_list_migrate.py")
ROWS = [{"id": f"R{i}", "fields": [{"key": "title", "text": f"행{i}"}]} for i in range(1, 5)]


class Fake(BaseHTTPRequestHandler):
    calls = []
    gone = set()
    throttle = 0  # 앞의 n 번 삭제는 429

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        m = self.path.rsplit("/", 1)[-1]
        Fake.calls.append((m, body))
        if m == "slackLists.items.list":
            out = {"ok": True, "items": ROWS[:2], "response_metadata": {"next_cursor": "c2"}} if not body.get("cursor") else {"ok": True, "items": ROWS[2:]}
        elif m == "slackLists.items.delete" and Fake.throttle > 0:
            Fake.throttle -= 1
            self.send_response(429)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif m == "slackLists.items.delete" and body["id"] in Fake.gone:
            out = {"ok": False, "error": "item_not_found"}
        else:
            out = {"ok": True}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class MigrateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Fake.calls, Fake.gone, Fake.throttle = [], set(), 0
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name
        self.state = os.path.join(t.name, "state.json")
        json.dump({"items": {"a": {"record": "R1"}, "done:R2": {"record": "R2"}, "b": {"record": "R9"}}}, open(self.state, "w"))  # R3·R4 는 손으로 넣은 행

    def run_cli(self, *args):
        env = {"PATH": os.environ["PATH"], "HOME": self.d, "LRM_DIGEST_STATE": self.state, "LRM_MIGRATE_PACE_S": "0", "LRM_DIGEST_LOCAL_ONLY": "1", "SLACK_BOT_TOKEN": "fake-test-token",
               "SLACK_API_BASE": f"http://127.0.0.1:{self.srv.server_port}/api"}
        return subprocess.run([sys.executable, "-I", SCRIPT, "--backup-dir", os.path.join(self.d, "bk"), *args], env=env, capture_output=True, text=True, timeout=60)

    def deletes(self):
        return [b["id"] for m, b in Fake.calls if m == "slackLists.items.delete"]

    def test_backup_all_rows_then_delete_only_bot_rows(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        (f,) = glob.glob(os.path.join(self.d, "bk", "todo-list-backup-*.json"))
        data = json.load(open(f))
        self.assertEqual((data["count"], [x["id"] for x in data["rows"]]), (4, ["R1", "R2", "R3", "R4"]))  # 페이지 2장 모두, 손 행 포함
        self.assertEqual(self.deletes(), ["R1", "R2"])
        self.assertNotIn("fake-test-token", r.stdout + r.stderr)

    def test_dry_run_and_keep_never_delete(self):
        self.assertEqual(self.run_cli("--dry-run").returncode, 0)
        self.assertEqual(self.run_cli("--keep").returncode, 0)
        self.assertEqual(self.deletes(), [])

    def test_backup_failure_blocks_delete(self):
        open(os.path.join(self.d, "bk"), "w").close()  # 폴더 자리에 파일
        r = self.run_cli()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.deletes(), [])

    def test_rate_limit_retried(self):
        Fake.throttle = 2
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.deletes(), ["R1", "R1", "R1", "R2"])  # R1 은 두 번 막히고 세 번째에 성공

    def test_already_gone_row_skipped(self):
        Fake.gone = {"R1"}
        self.assertEqual(self.run_cli().returncode, 0)


if __name__ == "__main__":
    unittest.main()
