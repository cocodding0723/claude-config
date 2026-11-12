"""slack_poll.sh 가 허브 보고 전송기에 폴러 남은 예산을 넘기는지 시험. 가짜 curl·대역 전송기만 씀."""
import os
import subprocess
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts")


class PollBudgetTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def poll(self, total):
        """복사한 slack_poll.sh 를 가짜 curl·대역 전송기로 돌려 전송기에 넘긴 예산을 읽음"""
        d = os.path.join(self.dir.name, "poll")
        scripts, bindir, hub = (os.path.join(d, n) for n in ("scripts", "bin", "hub"))
        for x in (scripts, bindir, hub):
            os.makedirs(x, exist_ok=True)
        import shutil
        shutil.copy(os.path.join(SCRIPTS, "slack_poll.sh"), scripts)
        with open(os.path.join(scripts, "report_sender.py"), "w") as f:
            f.write("import os,sys\nopen(os.environ['LRM_HUB_DIR']+'/budget','a').write(os.environ.get('LRM_REPORT_BUDGET_S','none')+'\\n')\n")
        with open(os.path.join(bindir, "curl"), "w") as f:
            f.write('#!/bin/sh\necho \'{"ok":false,"error":"fake"}\'\n')
        os.chmod(os.path.join(bindir, "curl"), 0o755)
        with open(os.path.join(hub, "slack-bot.env"), "w") as f:
            f.write("SLACK_BOT_TOKEN=fake-token-0123456789-abcdefghij\nSLACK_USER_ID=Ufake\n")
        env = {**os.environ, "LRM_HUB_DIR": hub, "LRM_TOTAL_BUDGET": str(total), "PATH": bindir + ":" + os.environ["PATH"], "LRM_PUSH": "off", "LRM_ACK": "off"}
        env.pop("LRM_REPORT_BUDGET_S", None)
        subprocess.run(["bash", os.path.join(scripts, "slack_poll.sh"), "--once"], env=env, capture_output=True, timeout=60)
        try:
            with open(os.path.join(hub, "budget"), encoding="utf-8") as f:
                return f.read().split()
        except FileNotFoundError:
            return []

    def test_poll_passes_remaining_budget_as_sender_cap(self):
        got = self.poll(total=30)
        self.assertEqual(len(got), 1)
        self.assertLessEqual(float(got[0]), 30)  # 40초 고정이 아니라 남은 예산 이하
        self.assertGreaterEqual(float(got[0]), 1)

    def test_poll_skips_sender_when_budget_spent(self):
        self.assertEqual(self.poll(total=0), [])


if __name__ == "__main__":
    unittest.main()
