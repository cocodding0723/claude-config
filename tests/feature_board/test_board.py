import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hubs/scripts"))
import feature_board as fb

STAGES = tuple((str(i), name) for i, name in enumerate(("접수", "이해", "설계", "구현", "검증", "검수", "반영")))


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.epic = {"id": "lrm-test", "title": "보고 정리", "status": "open",
                     "description": "- 목표: 기록 자동 표시", "labels": ["stage:3"],
                     "updated_at": "2026-11-21T10:00:00Z"}
        self.children = [{"id": "lrm-test.1", "title": "첫 작업", "status": "closed"},
                         {"id": "lrm-test.2", "title": "둘째 작업", "status": "in_progress"}]
        self.comments = [{"id": "1", "text": "검증 시작", "created_at": "2026-11-21T10:01:00Z"}]

    def text(self):
        return fb.render(self.epic, self.children, self.comments, STAGES)

    def test_stage_and_children(self):
        text = self.text()
        self.assertTrue(text.startswith("🟢 진행 보고 정리\n\n"))
        for part in ("- 무엇: 기록 자동 표시", "▶ 3 구현", "6 반영", "1/2 완료",
                     "⏳ 둘째 작업", "- 지금: 검증 시작", "👉 사용자 할 일: 없음"):
            self.assertIn(part, text)
        self.assertNotIn("첫 작업", text)

    def test_every_stage_and_missing_stage(self):
        for key, name in STAGES:
            self.epic["labels"] = ["stage:" + key]
            self.assertIn(f"▶ {key} {name}", self.text())
        self.epic["labels"] = []
        self.assertIn("(미지정)", self.text())

    def test_waiting_decisions_and_closed_answer(self):
        self.children[1]["labels"] = ["kind:decision"]
        self.assertTrue(self.text().startswith("🟡 사용자 답 기다림"))
        self.assertIn("👉 사용자 할 일: 둘째 작업", self.text())
        self.children[1]["status"] = "closed"
        self.assertTrue(self.text().startswith("🟢 진행"))

    def test_blocked_status_has_priority_over_waiting(self):
        self.children[1].update(status="blocked", labels=["kind:decision"])
        self.assertTrue(self.text().startswith("🔴 막힘"))
        self.assertIn("👉 사용자 할 일: 둘째 작업", self.text())

    def test_latest_block_per_card_and_resolution(self):
        self.comments = [{"id": "1", "issue_id": "lrm-test.2", "text": "막힘: 시험 실패"},
                         {"id": "2", "issue_id": "lrm-test", "text": "다른 작업 진행"}]
        self.assertTrue(self.text().startswith("🔴 막힘"))
        self.comments.append({"id": "3", "issue_id": "lrm-test.2", "text": "해결: 시험 통과"})
        self.assertTrue(self.text().startswith("🟢 진행"))
        self.comments[-1]["text"] = "막힘: 시험 실패"
        self.children[1]["status"] = "closed"
        self.assertTrue(self.text().startswith("🟢 진행"))

    def test_echoes_do_not_replace_now(self):
        for prefix in fb.Format.echoes:
            self.comments.append({"id": "2", "text": prefix + " 답 도착",
                                  "created_at": "2026-11-21T12:00:00Z"})
        self.assertIn("- 지금: 검증 시작", self.text())
        self.assertNotIn("12:00:00", self.text())

    def test_long_text_ids_links_and_escaping(self):
        self.comments[0]["text"] = "lrm-test.2 abc1234 PR #123 <확인> / " + "길" * 160 + "\n문서 /tmp/hub/docs/out/설계.html"
        text = self.text()
        body, refs = text.split("\n참고:")
        self.assertNotIn("lrm-test", body)
        self.assertNotIn("abc1234", body)
        self.assertNotIn("PR #123", body)
        self.assertIn("&lt;확인&gt;", body)
        self.assertIn("…", body)
        self.assertIn("PR #123", refs)
        self.assertIn("설계.html", refs)
        self.assertNotIn("/tmp", refs)
        self.assertIn("abc1234", refs)

    def test_references_allow_only_results_pr_hashes_and_card_ids(self):
        text = ("lrm-i46u의 abc1234 PR #3 https://github.com/org/repo/pull/4 "
                "https://example.com/abcdef1.html https://example.com/docs/out/외부.html /Users/me/private.html "
                "/Users/me/hub/docs/out/day/보고.html docs/out/검수.html")
        refs = fb.references([text])
        for expected in ("lrm-i46u", "abc1234", "PR #3", "https://github.com/org/repo/pull/4", "보고.html", "검수.html"):
            self.assertIn(expected, refs)
        for forbidden in ("lrm-i46u의", "abcdef1", "example.com", "외부.html", "/Users", "private.html", "docs/out"):
            self.assertNotIn(forbidden, refs)
        self.assertIn("의 확인", fb.plain("lrm-i46u의 확인"))

    def test_same_second_sorts_by_id_for_now_and_block_resolution(self):
        self.comments = [{"id": 9, "text": "막힘: 실패", "created_at": "2026-11-21T10:01:00Z"},
                         {"id": 10, "text": "해결: 성공", "created_at": "2026-11-21T10:01:00Z"}]
        self.assertTrue(self.text().startswith("🟢 진행"))
        self.assertIn("- 지금: 해결: 성공", self.text())
        self.comments.reverse()
        self.assertTrue(self.text().startswith("🟢 진행"))
        self.assertIn("- 지금: 해결: 성공", self.text())

    def test_null_text_and_titles_are_empty(self):
        self.epic.update(title=None, description=None)
        self.children[1].update(title=None, labels=["kind:decision"])
        self.comments.append({"id": 2, "text": None})
        self.assertIn("기능 이름 없음", self.text())
        self.assertIn("- 지금: 검증 시작", self.text())
        self.assertNotIn("None", self.text())

    def test_natural_child_order(self):
        self.children = [{"id": f"lrm-test.{i}", "title": title} for i, title in ((10, "열째"), (2, "둘째"))]
        text = self.text()
        self.assertLess(text.index("둘째"), text.index("열째"))

    def test_mentions_in_titles_are_inert(self):
        self.epic["title"] = "@here @channel <!here> <!channel>"
        self.children[1].update(title="@channel <!subteam^S123>", labels=["kind:decision"])
        text = self.text()
        for mention in ("@here", "@channel", "<!"):
            self.assertNotIn(mention, text)
        self.assertIn("＠here", text)

    def test_recent_references_limit_and_overflow(self):
        self.comments = [{"text": f"PR #{i}", "created_at": f"2026-11-21T10:{i:02}:00Z"} for i in range(12)]
        refs = self.text().split("\n참고: ")[1]
        self.assertEqual([f"PR #{i}" for i in range(11, 3, -1)] + ["외 5개"], refs.split(" · "))

    def test_message_cap_with_sixty_cards_and_three_hundred_records(self):
        for title in ("긴" * 100, "<&>" * 100):
            for status in ("open", "closed", "tombstone"):
                with self.subTest(title=title[:3], status=status):
                    self.children = [{"id": f"lrm-test.{i}", "title": title, "status": status,
                                      "labels": ["kind:decision"]} for i in range(60)]
                    self.comments = [{"text": f"진행 {title} PR #{i}"} for i in range(300)]
                    text = self.text()
                    self.assertLessEqual(len(text), 3000)
                    self.assertIn("- 지금:", text)
                    self.assertIn("👉 사용자 할 일:", text)
                    self.assertIn("- 최근 갱신:", text)
                    self.assertIn("참고: PR #299", text)
                    self.assertNotIn("&l…", text)
                    if status in fb.Format.ended:
                        self.assertIn("60/60 완료" if status == "closed" else "0/0 완료", text)
                        self.assertNotIn("⏳", text)

    def test_numbers_commands_mentions_deleted_and_duplicate_completion(self):
        self.assertEqual("보상 1000000 골드 /catch-up 실행", fb.plain("보상 1000000 골드 /catch-up 실행"))
        text = fb.plain("@everyone @channel <!here>")
        for mention in ("@everyone", "@channel", "<!here>"):
            self.assertNotIn(mention, text)
        self.children.append({"id": "lrm-test.3", "status": "tombstone", "title": "삭제됨"})
        text = self.text()
        self.assertIn("1/2 완료", text)
        self.assertNotIn("삭제됨", text)
        self.assertNotIn("- 완료", text)

    def test_external_decision_is_pure_input(self):
        text = fb.render(self.epic, self.children, self.comments, STAGES, [{"title": "계획 승인"}])
        self.assertTrue(text.startswith("🟡"))
        self.assertIn("👉 사용자 할 일: 계획 승인", text)

    def test_reference_budget_includes_escaping(self):
        text = fb.references(["docs/out/" + "&" * 2000 + ".html"])
        self.assertEqual("참고: 외 1개", text)

    def test_same_snapshot_same_digest_without_mutation(self):
        before = copy.deepcopy((self.epic, self.children, self.comments))
        first = self.text()
        self.children.reverse()
        self.assertEqual(first, self.text())
        self.assertEqual(fb.summary(first), fb.summary(self.text()))
        self.children.reverse()
        self.assertEqual(before, (self.epic, self.children, self.comments))
        self.epic["labels"] = ["stage:4"]
        self.assertNotEqual(fb.summary(first), fb.summary(self.text()))

    def test_empty_snapshot(self):
        self.children = []
        self.comments = []
        self.assertIn("0/0 완료", self.text())
        self.assertIn("- 지금: 기록 없음", self.text())

    def test_child_comment_is_latest_and_query_failure_aborts(self):
        reader = fb.BdReader()
        comment = dict(self.comments[0], text="자식에서 검증 완료")
        with patch.object(reader, "query", side_effect=[[self.epic], self.children, [], [], [comment]]), \
                patch.object(reader, "stages", return_value=STAGES):
            self.assertIn("- 지금: 자식에서 검증 완료", reader.text("lrm-test"))
        with patch.object(reader, "query", side_effect=[[self.epic], RuntimeError("조회 실패")]):
            with self.assertRaises(RuntimeError):
                reader.text("lrm-test")

    def test_bd_failures_do_not_become_empty_board(self):
        with patch.object(fb.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "[]", "")) as run:
            fb.BdReader().query("show", "lrm-test")
            self.assertEqual(15, run.call_args.kwargs["timeout"])
        for output in ("bad json", "null", "{}", '["bad"]'):
            with patch.object(fb.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                with self.assertRaises(RuntimeError):
                    fb.BdReader().query("show", "lrm-test")
        for error in (OSError(), subprocess.TimeoutExpired("bd", 1), subprocess.CalledProcessError(1, "bd")):
            with patch.object(fb.subprocess, "run", side_effect=error):
                with self.assertRaises(RuntimeError):
                    fb.BdReader().query("show", "lrm-test")

    def test_reader_cli_uses_fake_bd_and_real_stage_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "lib").mkdir()
            (tmp / "lib/board_flow.py").write_text(
                "from types import SimpleNamespace\nSTAGES = [SimpleNamespace(key='build', name='구현')]\n")
            fake = tmp / "bd"
            fake.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                            "from pathlib import Path\n"
                            "args=sys.argv[3:-1]\n"
                            "with (Path(sys.argv[2])/'calls').open('a') as f: f.write(json.dumps(args)+'\\n')\n"
                            "if args[0]=='show': print(json.dumps([{'id':'lrm-test','title':'가짜 기능','labels':['stage:build']}]))\n"
                            "elif args[0]=='list': print('[]')\n"
                            "elif args[0]=='comments': print('[]')\n"
                            "else: raise SystemExit(9)\n")
            fake.chmod(0o755)
            result = subprocess.run([sys.executable, str(ROOT / "hubs/scripts/feature_board.py"),
                                     "lrm-test", "--board", str(tmp), "--bd", str(fake)],
                                    capture_output=True, text=True, check=True)
            self.assertIn("가짜 기능", result.stdout)
            self.assertIn("▶ 0 구현", result.stdout)
            calls = [json.loads(line) for line in (tmp / "calls").read_text().splitlines()]
            self.assertEqual(["show", "list", "comments"], [c[0] for c in calls])
            self.assertIn("0", calls[1])


if __name__ == "__main__":
    unittest.main()
