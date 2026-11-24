"""검수 판정 json: 위험 영역은 상위 모델 요구, 1차 모델·놓친 지적 수는 경고만"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
loader = importlib.machinery.SourceFileLoader("board_done", os.path.join(ROOT, "board/bin/board-done"))
spec = importlib.util.spec_from_loader("board_done", loader)
bd = importlib.util.module_from_spec(spec)
loader.exec_module(bd)


class ReviewMetaTest(unittest.TestCase):
    def judge(self, data, risk=()):
        bd.GATE.update(risk=list(risk), notes=[])
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
        try:
            return bd.check_review(f.name), list(bd.GATE["notes"])
        finally:
            os.unlink(f.name)

    BASE = {"verdict": "pass", "author_model": "claude-sonnet-5-5"}

    def test_risk_needs_strong_model(self):
        why, _ = self.judge(dict(self.BASE, reviewer_model="gpt-6-astra"), risk=["Assets/Save/SaveStore.cs"])
        self.assertIn("위험 영역", why)

    def test_risk_with_fable_passes(self):
        why, _ = self.judge(dict(self.BASE, reviewer_model="claude-fable-5-2", first_model="claude-sonnet-5-5", missed=1),
                            risk=["Assets/Save/SaveStore.cs"])
        self.assertIsNone(why)

    def test_no_risk_any_model(self):
        why, notes = self.judge(dict(self.BASE, reviewer_model="gpt-6-astra", first_model="gpt-6-astra", missed=0))
        self.assertIsNone(why)
        self.assertEqual([], notes)

    def test_missing_meta_only_warns(self):
        why, notes = self.judge(dict(self.BASE, reviewer_model="claude-fable-5-2"))
        self.assertIsNone(why)
        self.assertTrue(notes and "first_model" in notes[0])

    def test_bad_missed_warns(self):
        _, notes = self.judge(dict(self.BASE, reviewer_model="claude-fable-5-2", first_model="x", missed=-1))
        self.assertTrue(notes)


if __name__ == "__main__":
    unittest.main()
