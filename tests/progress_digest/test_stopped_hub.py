"""정지 허브 접기: 세션 없이 낡은 허브의 줄을 한 줄로 접음(progress_collect.py). 가짜 orca·임시 폴더만 씀."""
import json
import os
import stat
import unittest
from datetime import datetime, timedelta, timezone

from test_collect import Base

COORD = "/w/coord-ui-hub"


class StoppedHubTest(Base):
    def setUp(self):
        super().setUp()
        d = self.t.name
        self.terms = os.path.join(d, "terms.json")
        self.orca = os.path.join(d, "orca")
        with open(self.orca, "w") as f:  # terminal list 는 terms.json 내용, worktree list 는 빈 목록
            f.write(f'#!/bin/sh\ncase "$*" in\n*terminal*) cat "{self.terms}";;\n*worktree*) echo \'{{"ok":true,"result":{{"worktrees":[]}}}}\';;\nesac\n')
        os.chmod(self.orca, os.stat(self.orca).st_mode | stat.S_IXUSR)
        self.set_terms([])

    def set_terms(self, terminals, truncated=False):
        with open(self.terms, "w") as f:
            json.dump({"ok": True, "result": {"terminals": terminals, "truncated": truncated}}, f)

    def agent(self, path=COORD):
        return {"handle": "t1", "worktreePath": path, "agentIdentity": "claude"}

    def put(self, slug, tasks, blockers, hours_old=48):
        at = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat(timespec="seconds")
        with open(os.path.join(self.status, slug + ".json"), "w", encoding="utf-8") as f:
            json.dump({"slug": slug, "updatedAt": at, "tasks": tasks, "blockers": blockers}, f)
        return datetime.fromisoformat(at).astimezone().strftime("%m-%d")

    def hubs_table(self, *rows):
        with open(os.path.join(self.t.name, "hubs.tsv"), "w", encoding="utf-8") as f:
            f.write("# slug\tname\trole\tws\trepo\n" + "".join("\t".join(r) + "\n" for r in rows))

    def items(self, orca=None, prefix="hub:"):
        r = self.run_once("--once", "--no-pr", LRM_ORCA=orca or self.orca)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [i for i in self.load()["items"] if i["key"].startswith(prefix)]

    def test_no_session_stale_hub_folded_to_one_line(self):
        self.hubs_table(("ui", "UI 계층", "-", "-", "-"))
        day = self.put("ui", ["버튼 구현", "팝업 구현"], ["담당자님 확인 대기", "서버 키 발급 막힘"])
        got = self.items()
        self.assertEqual([i["key"] for i in got], ["hub:ui:stopped"])  # 같은 허브 개별 줄은 사라져 digest 가 완료 처리함
        head, *body = got[0]["title"].split("\n")
        self.assertEqual(head, f"정지 허브: UI 계층 — 진행 2·막힘 2 (마지막 갱신 {day})")
        self.assertEqual(body, ["· 담당자님 확인 대기"])  # 사용자·외부가 풀 막힘만 본문에 한 줄씩. 다른 막힘은 접힘
        self.assertEqual((got[0]["bucket"], got[0]["group"]), ("wait", "ui"))

    def test_user_wait_words_also_kept_in_body(self):
        self.put("ui", ["작업"], ["사용자 확인 대기", "결정 대기 D-1"])
        body = self.items()[0]["title"].split("\n")[1:]
        self.assertEqual(body, ["· 사용자 확인 대기", "· 결정 대기 D-1"])

    def test_no_user_blockers_means_block_bucket_and_single_line(self):
        self.put("ui", ["작업"], ["서버 키 발급 막힘"])
        got = self.items()
        self.assertEqual(got[0]["bucket"], "block")
        self.assertNotIn("\n", got[0]["title"])
        self.assertIn("정지 허브: ui — 진행 1·막힘 1", got[0]["title"])  # hubs.tsv 에 이름이 없으면 slug

    def test_hub_with_session_keeps_individual_stale_lines(self):
        self.set_terms([self.agent()])
        self.put("ui", ["버튼 구현"], ["키 막힘"])
        got = self.items()
        self.assertEqual(sorted(i["title"] for i in got), ["[낡음] ui: 버튼 구현", "[낡음] ui: 키 막힘"])

    def test_session_lookup_failure_does_not_fold(self):
        self.put("ui", ["버튼 구현"], ["키 막힘"])
        got = self.items(orca=os.path.join(self.t.name, "no-orca"))
        self.assertEqual(len(got), 2)
        self.assertTrue(all(i["title"].startswith("[낡음] ") for i in got))

    def test_truncated_terminal_list_does_not_fold(self):
        self.set_terms([], truncated=True)  # 목록이 잘리면 세션이 없다고 단정할 수 없음
        self.put("ui", ["버튼 구현"], [])
        self.assertEqual([i["title"] for i in self.items()], ["[낡음] ui: 버튼 구현"])

    def test_terminal_without_agent_is_not_a_session(self):
        self.set_terms([{"handle": "t1", "worktreePath": COORD}])
        self.put("ui", ["버튼 구현"], [])
        self.assertEqual([i["key"] for i in self.items()], ["hub:ui:stopped"])

    def test_fresh_hub_without_session_not_folded(self):
        self.put("ui", ["버튼 구현"], [], hours_old=1)
        self.assertEqual([i["title"] for i in self.items()], ["ui: 버튼 구현"])

    def test_resolved_lines_not_counted_and_all_resolved_hides_hub(self):
        with open(os.path.join(self.t.name, "tasks.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "T0001", "state": "완료"}) + "\n")
        done = {"text": "끝난 일", "refs": [{"kind": "task", "id": "T0001"}]}
        self.put("ui", [done, "남은 일"], [])
        self.assertIn("진행 1·막힘 0", self.items()[0]["title"])
        self.put("ui", [done], [])
        self.assertEqual(self.items(), [])  # 남은 줄이 없으면 접힌 줄도 없음

    def test_alias_status_file_matches_session_of_hubs_table_slug(self):
        """hubs.tsv slug coord-server-hub, 상태 파일 server.json, 폴더 coord-coord-server-hub-hub"""
        self.hubs_table(("coord-server-hub", "서버", "-", "-", "-"))
        self.set_terms([self.agent("/w/coord-coord-server-hub-hub")])
        self.put("server", ["서버 작업"], [])
        self.assertEqual([i["title"] for i in self.items()], ["[낡음] coord-server-hub: 서버 작업"])  # 세션 있음 → 접지 않음. 칸 이름은 hubs.tsv slug
        self.set_terms([])
        self.assertTrue(self.items()[0]["title"].startswith("정지 허브: 서버 — "))

    def test_new_name_file_wins_over_alias_file(self):
        """허브가 coord-server-hub.json 을 쓰기 시작하면 옛 server.json 은 무시(같은 허브가 두 번 나오지 않음)"""
        self.hubs_table(("coord-server-hub", "서버", "-", "-", "-"))
        self.set_terms([self.agent("/w/coord-coord-server-hub-hub")])
        self.put("server", ["옛 파일 작업"], [])
        self.put("coord-server-hub", ["새 파일 작업"], [], hours_old=1)
        self.assertEqual([i["title"] for i in self.items()], ["coord-server-hub: 새 파일 작업"])

    def test_other_hub_session_does_not_protect_this_hub(self):
        self.set_terms([self.agent("/w/coord-vfx-hub")])
        self.put("ui", ["버튼 구현"], [])
        self.assertEqual([i["key"] for i in self.items()], ["hub:ui:stopped"])


if __name__ == "__main__":
    unittest.main()
