import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hubs/scripts"))
import report_audit as audit


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.now = audit.said.stamp("2026-11-21T11:00:00Z")
        self.ws = str(self.home / "coord-feat-test-hub")
        self.root = dict(id="lrm-test", issue_type="epic", title="시험", labels=["feature:test", "flow:v1", "stage:build"],
                         description="- 목표: 보고 검증\n- 기능 코디네이터 허브: " + self.ws)
        self.child = dict(id="lrm-test.1", parent="lrm-test", title="하위", labels=[], status="open")
        self.rows = {"lrm-test": [self.row("stage", "단계: 3 구현", "09:55"),
                     self.row("said", audit.said.said_line(1, self.now - 4000, "계속 확인"), "09:56"),
                     self.row("done", "지시 처리 #1: 반영 — 시험 완료", "10:16"),
                     self.row("after", "단계: 3 구현 — 복원", "10:17")],
                     "lrm-test.1": [self.row("child", "하위 기록", "10:20"),
                                    self.row("echo", "Slack 결정 답: 승인", "10:21")]}
        self.fixture = self.home / "fixture.json"
        self.bd = self.home / "fake-bd"
        self.bd.write_text("#!" + sys.executable + "\nimport json,sys\n"
                           + "d=json.load(open(" + repr(str(self.fixture)) + "))\na=sys.argv[3:]\n"
                           + "print(json.dumps(d['cards'] if a[0]=='list' else d['rows'][a[1]]))\n")
        self.bd.chmod(0o755)
        self.config = self.home / "claude"
        folder = self.config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", self.ws)
        folder.mkdir(parents=True)
        self.session = folder / "session.jsonl"
        self.session.write_text(json.dumps(dict(type="system", subtype="compact_boundary", timestamp="2026-11-21T10:15:00Z")) + "\n"
            + json.dumps(dict(type="assistant", timestamp="2026-11-21T10:16:00Z", message=dict(usage=dict(
                input_tokens=10, cache_read_input_tokens=20, cache_creation_input_tokens=30)))) + "\n")
        self.env = patch.dict(os.environ, {"BOARD_CLAUDE_DIRS": str(self.config), "LRM_HUB_DIR": str(self.home)})
        self.env.start()
        self.queue = [dict(id="q1", at=int(self.now - 1200), hub="feat-test", task="없음", kind="info", text="기록", files=[],
                          card="lrm-test", mirror_card="lrm-test", mirror_ids=["done", "after"]),
                      dict(id="q2", at=int(self.now - 1200), hub="feat-test", task="없음", kind="info", text="하위", files=[],
                          card="lrm-test", mirror_card="lrm-test.1", mirror_ids=["child"])]
        self.save()
        text = audit.board.render(self.root, [self.child], [dict(r, issue_id=cid) for cid, rows in self.rows.items() for r in rows],
                                  [(s.key, s.name) for s in audit.board_flow.STAGES])
        self.state = {"topics": {"card:lrm-test": {"fmt": audit.board.summary(text)}}}
        self.save()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def row(self, rid, text, at):
        return dict(id=rid, text=text, created_at="2026-11-21T" + at + ":00Z")

    def save(self):
        self.fixture.write_text(json.dumps(dict(cards=[self.root, self.child], rows=self.rows)))
        (self.home / "hub-reports.jsonl").write_text("\n".join(json.dumps(q) for q in self.queue) + "\n")
        if hasattr(self, "state"):
            (self.home / "report-sender.state.json").write_text(json.dumps(self.state))

    def inspect(self):
        return audit.inspect("feat-test", audit.board.BdReader(self.home, str(self.bd)), self.home, self.now - 3600, self.now)

    def test_all_pass_read_only_and_cli_json(self):
        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        result = self.inspect()
        self.assertEqual(["통과"] * 4, [r["status"] for r in result["rows"]])
        self.assertEqual(60, result["compactions"][0]["tokens"])
        self.assertEqual([], result["missing_records"])
        p = subprocess.run([sys.executable, "-B", str(ROOT / "hubs/scripts/report_audit.py"), "feat-test", "--json",
                            "--now", "2026-11-21T11:00:00Z", "--board", str(self.home), "--bd", str(self.bd)], capture_output=True, text=True)
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual("feat-test", json.loads(p.stdout)["hub"])
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()})

    def test_unconnected_is_not_failure(self):
        self.state["topics"]["card:lrm-test"]["fmt"] = "1:old-parent"
        self.save()
        self.assertEqual("미연결", self.inspect()["rows"][0]["status"])

    def test_board_mismatch_missing_and_duplicate_ids(self):
        self.state["topics"]["card:lrm-test"]["fmt"] = "0" * 64
        self.queue = [self.queue[0], dict(self.queue[0], id="q3")]
        self.save()
        result = self.inspect()
        self.assertEqual("실패", result["rows"][0]["status"])
        self.assertEqual([dict(card="lrm-test.1", id="child")], result["missing_records"])
        self.assertEqual(2, len(result["duplicate_records"]))

    def test_recovery_missing_instruction_and_stage(self):
        self.rows["lrm-test"] = self.rows["lrm-test"][:2]
        self.save()
        result = self.inspect()
        self.assertEqual("실패", result["rows"][2]["status"])
        self.assertEqual(["지시 #1", "단계"], result["compactions"][0]["missing"])

    def test_recent_compaction_waits_for_thirty_minutes(self):
        result = audit.restored([dict(at=self.now - 60, tokens=60)], [], self.now)
        self.assertTrue(result[0]["pending"])
        self.session.write_text(json.dumps(dict(subtype="compact_boundary", timestamp="2026-11-21T10:59:00Z")) + "\n")
        self.assertEqual("대기", self.inspect()["rows"][3]["status"])

    def test_nested_card_count_keeps_board_direct_children(self):
        grandchild = dict(id="lrm-test.1.1", parent="lrm-test.1", title="손자", labels=[])
        self.rows[grandchild["id"]] = [self.row("nested", "손자 기록", "10:25")]
        self.fixture.write_text(json.dumps(dict(cards=[self.root, self.child, grandchild], rows=self.rows)))
        result = self.inspect()
        self.assertEqual("통과", result["rows"][0]["status"])
        self.assertIn(dict(card=grandchild["id"], id="nested"), result["missing_records"])

    def test_no_session_and_missing_usage_are_visible(self):
        self.session.unlink()
        self.assertEqual("확인 불가", self.inspect()["rows"][3]["status"])
        self.session.write_text(json.dumps(dict(subtype="compact_boundary", timestamp="2026-11-21T10:15:00Z")) + "\n")
        self.assertEqual("실패", self.inspect()["rows"][3]["status"])

    def test_window_excludes_old_boundary_and_sidechains(self):
        extra = [dict(subtype="compact_boundary", timestamp="2026-11-21T09:00:00Z"),
                 dict(subtype="compact_boundary", timestamp="2026-11-21T10:30:00Z", isSidechain=True)]
        with self.session.open("a") as f:
            f.write("\n".join(json.dumps(x) for x in extra) + "\n")
        self.assertEqual(1, len(self.inspect()["compactions"]))

    def test_bad_json_and_bd_failure_are_errors(self):
        self.session.write_text("broken\n")
        with self.assertRaises(ValueError):
            self.inspect()
        self.bd.write_text("#!/bin/sh\nexit 1\n")
        with self.assertRaises(RuntimeError):
            self.inspect()

    def test_unrecorded_user_instruction_is_counted_until_restored(self):
        at = self.now - 3000
        boundary = dict(at=self.now - 2500, tokens=60)
        result = audit.restored([boundary], [], self.now, [(at, "사용자 새 지시")])
        self.assertIn(f"미기록 지시@{int(at)}", result[0]["missing"])
        row = dict(created_at="2026-11-21T10:20:00Z", text=audit.said.said_line(2, at, "사용자 새 지시"))
        result = audit.restored([boundary], [row], self.now, [(at, "사용자 새 지시")])
        self.assertNotIn(f"미기록 지시@{int(at)}", result[0]["missing"])


if __name__ == "__main__":
    unittest.main()
