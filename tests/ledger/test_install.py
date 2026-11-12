"""설치 경로 시험: install.sh 로 임시 HOME 에 복사한 수신기가 시작되는지(import 단계 의존 모듈 누락 방지).
임시 HOME·빈 작업 폴더만 쓰며 실제 ~/.config·Slack·토큰은 건드리지 않음."""
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VALUES = "OWNER_NAME=a\nPERSON_FULL=b\nPERSON_SHORT=c\nDM_CHANNEL=d\nREMOTE_ENV=e\n"
LOCAL_DEPS = ("ledger.py", "ledger_sync.py")  # slack_socket.py 가 같은 폴더에서 import 하는 모듈


class InstalledReceiverTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.dir.name, "home")
        cfg = os.path.join(self.home, ".config", "claude-config")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "values.env"), "w", encoding="utf-8") as f:
            f.write(VALUES)
        os.chmod(os.path.join(cfg, "values.env"), 0o600)
        self.env = {"PATH": os.environ["PATH"], "HOME": self.home, "CLAUDE_CONFIG_BACKUP_ROOT": os.path.join(self.dir.name, "bk")}
        done = subprocess.run(["bash", os.path.join(REPO, "install.sh")], env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        self.hub = os.path.join(self.home, ".config", "lrm-hubs")

    def tearDown(self):
        self.dir.cleanup()

    def test_install_copies_every_local_module_the_receiver_imports(self):
        for name in ("slack_socket.py",) + LOCAL_DEPS:
            self.assertTrue(os.path.isfile(os.path.join(self.hub, name)), f"{name} 설치 안 됨")

    def test_installed_receiver_starts_without_import_error(self):
        """토큰 없는 빈 설정 파일로 시작 → import 를 통과해 설정 오류(종료 2)로 끝나야 함. ModuleNotFoundError 면 종료 1"""
        empty = os.path.join(self.dir.name, "empty-hub")
        os.makedirs(empty)
        open(os.path.join(empty, "slack-bot.env"), "w").close()  # 빈 설정 파일
        env = dict(self.env, LRM_HUB_DIR=empty, LRM_LEDGER=os.path.join(empty, "none.tsv"))
        done = subprocess.run([sys.executable, "-I", os.path.join(self.hub, "slack_socket.py")], env=env,
                              capture_output=True, text=True, timeout=60, cwd=self.dir.name)
        self.assertNotIn("ModuleNotFoundError", done.stderr)
        self.assertEqual(done.returncode, 2, done.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
