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
        self.epic = {"id": "lrm-test", "issue_type": "epic", "status": "open", "title": "보고 정리", "labels": ["flow:v1", "feature:test", "slack-thread:123.456"]}
        self.kids = [{"id": "lrm-test.1", "title": "첫 작업", "status": "open", "parent": "lrm-test"}]
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
                      "with (Path(__file__).parent/'queries').open('a') as f: f.write(json.dumps(a)+'\\n')\n"
                      "if a[0]=='show':\n"
                      " for c in [d['epic'],*d['kids']]: print(json.dumps([c|{'comments':d['comments']}]))\n"
                      "elif '--parent' in a: print(json.dumps(d['kids']))\n"
                      "else: print(json.dumps([c for c in [d['epic'],*d['kids']] if c.get('status')!='closed']))\n")
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
        sender.run(self.conf, boards=True)
        calls = self.calls()
        self.assertEqual(1, len(calls))
        self.assertIn("--update", calls[0])
        self.assertIn("--tag", calls[0])
        self.assertNotIn("--kind", calls[0])
        topic = sender.load_state(self.conf.state)["topics"]["card:lrm-test"]
        self.assertEqual(f"{sender.BOARD_FORMAT}:{fb.summary(calls[0][-1])}", topic["fmt"])
        self.assertEqual(66, len(topic["fmt"]))
        sender.run(self.conf, boards=True)
        self.assertEqual(1, len(self.calls()))
        queries = [json.loads(line) for line in (self.root / "queries").read_text().splitlines()]
        self.assertEqual(1, sum(q[0] == "show" for q in queries))
        self.assertEqual(0, sum(q[0] == "comments" for q in queries))
        self.assertEqual(4, len(queries))
        self.assertIn("--parent", queries[1])
        self.assertIn("lrm-test.1", queries[2])
        self.assertIn("--include-comments", queries[2])
        self.kids[0]["status"] = "closed"
        self.write_data()
        sender.run(self.conf, boards=True)
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
        self.assertEqual([], self.calls())
        self.state["topics"]["card:lrm-test"]["board_next"] = 0
        self.refresh()
        self.assertEqual(1, len(self.calls()))
        self.state["topics"]["card:lrm-test"]["fmt"] = "old"
        self.state["topics"]["card:lrm-test"].pop("board_signal")
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
        for changes in ({"status": "closed"}, {"issue_type": "task"}, {"labels": []}):
            self.epic.update(status="open", issue_type="epic", labels=["flow:v1", "feature:test", "slack-thread:123.456"])
            self.state["topics"]["card:lrm-test"].pop("board_skip", None)
            self.epic.update(changes)
            self.write_data()
            self.refresh()
            self.assertTrue(self.state["topics"]["card:lrm-test"]["board_skip"])
        with patch.object(fb.BdReader, "query") as query:
            self.refresh()
            query.assert_not_called()
        self.state["topics"]["card:lrm-test"]["shared"] = "lrm-parent"
        with patch.object(fb.BdReader, "query") as query:
            self.refresh()
            query.assert_not_called()
        self.assertEqual([], self.calls())

    def test_dry_outputs_body_and_stamp_without_mutation(self):
        before = Path(self.conf.state).read_text()
        result = sender.run(self.conf, dry=True, boards=True)
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
            result = sender.run(self.conf, boards=True)
        self.assertEqual(1, result["sent"])
        self.assertEqual(0, result["failed"])
        self.assertEqual(1, len(self.calls()))
        self.assertNotIn("--update", self.calls()[0])

    def test_synchronous_sender_skips_board_queries(self):
        with patch.object(sender, "refresh_boards") as refresh:
            sender.run(self.conf)
            refresh.assert_not_called()

    def test_batch_read_failure_skips_without_card_fallback_or_backoff(self):
        with patch.object(fb.BdReader, "query", side_effect=[[self.epic, *self.kids], self.kids, RuntimeError("일괄 조회 실패")]) as query:
            self.refresh()
            self.assertEqual(3, query.call_count)
            self.assertEqual("show", query.call_args.args[0])
        topic = self.state["topics"]["card:lrm-test"]
        self.assertEqual("1:faa6c1234567", topic["fmt"])
        self.assertEqual(0, topic.get("board_fails", 0))
        self.assertEqual(0, topic.get("board_next", 0))
        self.assertEqual([], self.calls())

    def test_top_and_other_hub_decisions_belong_by_card_or_hub(self):
        base = dict(ts="222.333", form_kind="decide", options={"1": "승인", "2": "보류"},
                    decision_id="D-PLAN", title="계획 승인", thread_ts="")
        for binding in ({"card": "lrm-test.1", "tag": "other"}, {"tag": "feat-test"}):
            Path(self.conf.sent).write_text(json.dumps(base | binding) + "\n")
            self.state["topics"]["card:lrm-test"].pop("board_signal", None)
            self.refresh()
            self.assertTrue(self.calls()[-1][-1].startswith("🟡"))

    def test_comment_count_alone_triggers_full_refresh(self):
        self.refresh()
        self.comments.append({"id": "new", "text": "새 진행 기록"})
        self.epic["comment_count"] = 1
        self.write_data()
        self.refresh()
        self.assertIn("새 진행 기록", self.calls()[-1][-1])

    def test_other_epic_prefix_does_not_change_signal_or_mix_children(self):
        self.kids.append(dict(id="lrm-test2.1", title="다른 기능의 하위", status="open"))
        self.write_data()
        self.refresh()
        self.assertNotIn("다른 기능의 하위", self.calls()[-1][-1])
        self.kids[-1].update(status="blocked", updated_at="2026-11-23T01:00:00Z")
        self.write_data()
        self.refresh()
        self.assertEqual(1, len(self.calls()))
        queries = [json.loads(line) for line in (self.root / "queries").read_text().splitlines()]
        self.assertEqual(4, len(queries))

    def test_zero_dependency_count_does_not_hide_closed_children(self):
        self.epic["dependent_count"] = 0
        self.kids[0]["status"] = "closed"
        self.write_data()
        self.refresh()
        self.assertIn("하위 카드 1/1 완료", self.calls()[-1][-1])

    def test_permanent_rejection_backs_off_before_next_board_read(self):
        topic = self.state["topics"]["card:lrm-test"]
        for attempt in (1, 2):
            topic["board_next"] = 0
            before = time.time()
            with patch.object(sender, "run_notify", side_effect=sender.NotifyError("양식 거부", permanent=True)):
                self.refresh()
            self.assertEqual(attempt, topic["board_fails"])
            self.assertGreaterEqual(topic["board_next"], before + self.conf.backoff_s * 2 ** (attempt - 1))
            with patch.object(fb.BdReader, "query") as query:
                self.refresh()
                query.assert_not_called()

    def test_budget_pause_reuses_body_only_for_same_change_signal(self):
        topic = self.state["topics"]["card:lrm-test"]
        for changed in (False, True):
            topic.pop("board_signal", None)
            topic["fmt"] = "old"
            cursor = self.state["boards"].get("cursor", "") if "boards" in self.state else ""
            with patch.object(sender, "run_notify", side_effect=sender.NotifyError("예산 초과", counts=False)):
                self.refresh()
            self.assertEqual(cursor, self.state["boards"].get("cursor", ""))
            self.assertEqual(0, topic.get("board_fails", 0))
            self.assertEqual(0, topic.get("board_next", 0))
            self.assertIn("text", topic["board_pending"])
            if changed:
                self.epic["comment_count"] = 2
                self.comments = [{"text": "신호 변경 뒤 새 기록"}]
                self.write_data()
            with patch.object(fb, "compose", wraps=fb.compose) as compose:
                self.refresh()
                self.assertEqual(int(changed), compose.call_count)
            self.assertNotIn("board_pending", topic)
        self.assertIn("신호 변경 뒤 새 기록", self.calls()[-1][-1])

    def test_five_slow_features_obey_cap_and_rotate_without_starvation(self):
        cards = [self.epic | {"id": f"lrm-slow{i}", "title": f"기능 {i}"} for i in range(5)]
        self.data.write_text(json.dumps(cards))
        for card in cards:
            (self.root / (card["id"] + ".json")).write_text(json.dumps([card | {"comments": []}]))
        Path(self.conf.bd).write_text('#!/bin/sh\nsleep 0.12\nrootdir=${0%/*}\n'
                                     'case "$3" in\n'
                                     ' list) case "$*" in *--parent*) printf "[]";; *) cat "$rootdir/data.json";; esac;;\n'
                                     ' show) cat "$rootdir/$4.json";;\nesac\n')
        self.state["topics"] = {"card:" + c["id"]: {"ts": f"123.{i}"} for i, c in enumerate(cards)}
        fb.Decisions(self.conf.sent).waiting(cards[0], [], "123.0")
        fb.BdReader(self.board, self.conf.bd).stages()
        durations = []
        # bd 대기 0.12초 + notify·fsync 측정. shell 가짜 bd로 실제 Go CLI에 없는 Python 시작 비용 제외.
        with patch.object(sender, "BOARD_BUDGET_S", 1.2):
            for _ in range(3):
                started = time.monotonic()
                self.refresh()
                durations.append(time.monotonic() - started)
        self.assertLessEqual(max(durations), 1.2)
        self.assertEqual(5, len(self.calls()))
        self.assertTrue(all(t.get("fmt", "").startswith("2:") for t in self.state["topics"].values()))
        print(f"현황판 순환: 5개/3회차, 상한 1.2초, 실측 최대 {max(durations):.3f}초")

    def test_ten_unchanged_features_need_only_one_list_query(self):
        cards = [self.epic | {"id": f"lrm-same{i}", "title": f"기능 {i}"} for i in range(10)]
        self.state["topics"] = {"card:" + c["id"]: {"ts": f"123.{i}"} for i, c in enumerate(cards)}
        with patch.object(fb.BdReader, "query", return_value=cards):
            self.refresh()
        self.assertEqual(10, len(self.calls()))
        with patch.object(fb.BdReader, "query", return_value=cards) as query, \
                patch.object(fb, "compose") as compose, patch.object(sender, "run_notify") as notify:
            self.refresh()
            query.assert_called_once_with("list", "--limit", "0", "--brief")
            compose.assert_not_called()
            notify.assert_not_called()
        print("현황판 변경 없음: 10개, 목록 1회, 추가 bd 조회·본문 계산·Slack 갱신 0회")


if __name__ == "__main__":
    unittest.main()
