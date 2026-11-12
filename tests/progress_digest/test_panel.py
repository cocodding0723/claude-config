"""panel_collect.py 가 진행 스냅샷을 같은 회차에 싣는지 시험. orca 는 없는 명령으로 두어 다른 원료가 실패해도 진행 칸이 유지되는지 봄."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(HERE, "..", "..", "hubs", "scripts", "panel_collect.py")
HEADER = "id\t접수\t요청(원문 요약)\t상태\t담당\t진행·증거\t갱신\n"


class PanelProgressTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = self.t.name
        with open(os.path.join(self.d, "feedback-ledger.tsv"), "w", encoding="utf-8") as f:
            f.write(HEADER + "F1\t11-12\t로그인\t진행\t개인\t구현 중\t2026-11-12 10:00\n")

    def run_panel(self, mode):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SLACK_", "LRM_", "PANEL_"))}
        env.update(HOME=self.d, LRM_HUB_DIR=self.d, PANEL_ORCA=os.path.join(self.d, "no-such-orca"), PANEL_GH=os.path.join(self.d, "no-such-gh"))
        return subprocess.run([sys.executable, "-I", PANEL, mode], env=env, capture_output=True, text=True, timeout=60)

    def test_print_includes_progress_without_writing_snapshot(self):
        r = self.run_panel("--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        src = json.loads(r.stdout)["sources"]
        self.assertTrue(src["progress"]["ok"])
        self.assertEqual(src["progress"]["value"]["counts"]["doing"], 1)
        self.assertFalse(src["hubs"]["ok"])  # 다른 원료 실패와 무관
        self.assertFalse(os.path.exists(os.path.join(self.d, "progress.json")))

    def test_once_writes_both_files_and_keeps_manual_panel_json(self):
        manual = os.path.join(self.d, "panel.json")
        with open(manual, "w") as f:
            f.write('{"todos":[]}')
        self.assertEqual(self.run_panel("--once").returncode, 0)
        snap = json.load(open(os.path.join(self.d, "progress.json")))
        self.assertEqual(snap["counts"]["doing"], 1)
        auto = json.load(open(os.path.join(self.d, "panel-auto.json")))
        self.assertEqual(auto["sources"]["progress"]["value"]["items"][0]["key"], "ledger:F1")
        self.assertEqual(open(manual).read(), '{"todos":[]}')  # 손 칸은 열지도 쓰지도 않음


if __name__ == "__main__":
    unittest.main()
