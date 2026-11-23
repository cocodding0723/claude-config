import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hubs/scripts"))
import feature_board as fb
import hub_report
import report_sender as sender


class BoardParentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.epic = {"id": "lrm-test", "issue_type": "epic", "status": "open", "title": "보고 정리"}
        self.kids = [{"id": "lrm-test.1", "title": "첫 작업", "status": "open"}]
        self.comments = []
        self.board = self.root / "board"
        (self.board / "lib").mkdir(parents=True)
        (self.board / "lib/board_flow.py").write_text(
            "from types import SimpleNamespace\nSTAGES=[SimpleNamespace(key='build',name='구현')]\n")
        self.data = self.root / "data.json"
        self.write_data()
        bd = self.root / "bd"
        bd.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                      "from pathlib import Path\n"
                      "d=json.loads((Path(__file__).parent/'data.json').read_text())\n"
                      "a=sys.argv[3:-1]\n"
                      "print(json.dumps([d['epic']] if a[0]=='show' else d['kids'] if a[0]=='list' else d['comments']))\n")
        bd.chmod(0o755)
        notify = self.root / "notify"
        notify.write_text("#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n"
                          "with (Path(__file__).parent/'calls').open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
                          "print('ok 123.456')\n")
        notify.chmod(0o755)
        self.conf = sender.Conf(state=str(self.root / "state"), outbox=str(self.root / "outbox"),
                                sent=str(self.root / "slack-sent.jsonl"), log=str(self.root / "log"),
                                bd=str(bd), board=str(self.board), notify=str(notify))
        self.state = {k: {} for k in sender.STATE_KEYS}
        self.state["topics"]["card:lrm-test"] = {"ts": "123.456", "fmt": "1:faa6c1234567"}
        sender.save_state(self.conf.state, self.state)

    def write_data(self):
        self.data.write_text(json.dumps(dict(epic=self.epic, kids=self.kids, comments=self.comments)))

    def refresh(self, **kwargs):
        return sender.refresh_boards(self.conf, self.state, time.monotonic() + 10, **kwargs)

    def calls(self):
        path = self.root / "calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_idle_round_updates_once_then_changed_snapshot_updates(self):
        sender.run(self.conf)
        calls = self.calls()
        self.assertEqual(1, len(calls))
        self.assertIn("--update", calls[0])
        topic = sender.load_state(self.conf.state)["topics"]["card:lrm-test"]
        self.assertEqual(f"{sender.BOARD_FORMAT}:{fb.summary(calls[0][-1])}", topic["fmt"])
        self.assertEqual(66, len(topic["fmt"]))
        sender.run(self.conf)
        self.assertEqual(1, len(self.calls()))
        self.kids[0]["status"] = "closed"
        self.write_data()
        sender.run(self.conf)
        self.assertEqual(2, len(self.calls()))
        self.assertIn("1/1 완료", self.calls()[-1][-1])

    def test_decision_waiting_then_answer_green_and_other_thread_ignored(self):
        card = dict(ts="222.333", form_kind="decide", options={"1": "승인", "2": "보류"},
                    decision_id="D-PLAN", thread_ts="123.456", title="계획 승인")
        Path(self.conf.sent).write_text(json.dumps(card) + "\n" + json.dumps(card | {"ts": "444.555", "thread_ts": "999.000"}) + "\n")
        self.refresh()
        self.assertTrue(self.calls()[-1][-1].startswith("🟡"))
        (self.root / "slack-inbox.jsonl").write_text(json.dumps(dict(type="button", kind="decide", decision_id="D-PLAN", parent_ts="222.333")) + "\n")
        self.refresh()
        self.assertTrue(self.calls()[-1][-1].startswith("🟢"))

    def test_transient_retry_and_update_dead(self):
        with patch.object(sender, "run_notify", side_effect=sender.NotifyError("timeout")):
            self.refresh()
        self.assertEqual("1:faa6c1234567", self.state["topics"]["card:lrm-test"]["fmt"])
        self.refresh()
        self.assertEqual(1, len(self.calls()))
        self.state["topics"]["card:lrm-test"]["fmt"] = "old"
        with patch.object(sender, "run_notify", side_effect=sender.NotifyError("message_not_found")):
            self.refresh()
        self.assertTrue(sender.load_state(self.conf.state)["topics"]["card:lrm-test"]["update_dead"])
        self.refresh()
        self.assertEqual(1, len(self.calls()))

    def test_budget_skips_all_queries_and_notify(self):
        with patch.object(fb.BdReader, "query") as query:
            sender.refresh_boards(self.conf, self.state, time.monotonic() - 1)
            query.assert_not_called()
        with self.assertRaises(RuntimeError):
            fb.BdReader(deadline=time.monotonic() - 1).query("show", "lrm-test")
        self.assertEqual([], self.calls())

    def test_closed_non_epic_and_shared_topics_are_skipped(self):
        for changes in ({"status": "closed"}, {"issue_type": "task"}):
            self.epic.update(changes)
            self.write_data()
            self.refresh()
        self.state["topics"]["card:lrm-test"]["shared"] = "lrm-parent"
        with patch.object(fb.BdReader, "query") as query:
            self.refresh()
            query.assert_not_called()
        self.assertEqual([], self.calls())

    def test_dry_outputs_body_and_stamp_without_mutation(self):
        before = Path(self.conf.state).read_text()
        result = sender.run(self.conf, dry=True)
        self.assertIn("fmt=2:", result["lines"][0])
        self.assertIn("🟢 진행 보고 정리", result["lines"][0])
        self.assertEqual(before, Path(self.conf.state).read_text())
        self.assertEqual([], self.calls())

    def test_board_failure_does_not_block_report_delivery(self):
        report = hub_report.make_report("ui", "C1", "info", "진행 기록", [], thread="555.666")
        Path(self.conf.outbox).write_text(json.dumps(report | {"id": "report1", "at": int(time.time())}) + "\n")
        notify = sender.run_notify

        def fail_update(conf, args, deadline, upload=False):
            if "--update" in args:
                raise sender.NotifyError("timeout")
            return notify(conf, args, deadline, upload)

        with patch.object(sender, "run_notify", side_effect=fail_update):
            result = sender.run(self.conf)
        self.assertEqual(1, result["sent"])
        self.assertEqual(0, result["failed"])
        self.assertEqual(1, len(self.calls()))
        self.assertNotIn("--update", self.calls()[0])


if __name__ == "__main__":
    unittest.main()
