"""막힘 근거: 근거 표기 파싱(hub_status.py) + 수집기 자동 제외(progress_collect.py). 가짜 gh·임시 폴더만 씀(라이브 Slack·상태 파일 없음)."""
import json
import os
import stat
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

from test_collect import Base

HS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts", "hub_status.py")
PR = "Basakansoft/LoveRevolutionMaker#746"


class ParseTest(unittest.TestCase):
    def write(self, *blockers, repos=PR.split("#")[0], tasks=()):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            args = [a for b in blockers for a in ("--blocker", b)] + [a for t in tasks for a in ("--task", t)]
            env = {k: v for k, v in os.environ.items() if not k.startswith(("LRM_", "PANEL_"))}
            env.update(LRM_HUB_STATUS_DIR=d, LRM_HUB_DIR=d, LRM_DIGEST_REPOS=repos)  # 라이브 panel.env 를 읽지 않게 작업 폴더도 격리
            r = subprocess.run([sys.executable, "-I", HS, "write", "--slug", "ui", *args], env=env, capture_output=True, text=True)
            data = None
            if r.returncode == 0:
                with open(os.path.join(d, "ui.json"), encoding="utf-8") as f:
                    data = json.load(f)
            return r, data

    def test_parse_refs(self):
        r, d = self.write(f"서버 병합 대기 [pr:{PR}] [decision:D-CFGAPPLY]", "티켓 대기 [ticket:LRM-436]", "[pr:#12] 단축형")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(d["blockers"][0], {"text": "서버 병합 대기", "refs": [{"kind": "pr", "id": PR}, {"kind": "decision", "id": "D-CFGAPPLY"}]})
        self.assertEqual(d["blockers"][1]["refs"], [{"kind": "ticket", "id": "LRM-436"}])
        self.assertEqual(d["blockers"][2], {"text": "단축형", "refs": [{"kind": "pr", "id": "Basakansoft/LoveRevolutionMaker#12"}]})  # 저장소 하나면 전체 형식으로 저장

    def test_task_refs_parsed(self):
        r, d = self.write(tasks=[f"기능 구현 [pr:{PR}] [task:T0123]", "근거 없는 줄", "[ticket:LRM-9] 티켓만"], *["장부 대기 [task:T0007]"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(d["tasks"][0], {"text": "기능 구현", "refs": [{"kind": "pr", "id": PR}, {"kind": "task", "id": "T0123"}]})
        self.assertEqual(d["tasks"][1], "근거 없는 줄")  # 근거 없으면 문자열 그대로
        self.assertEqual(d["tasks"][2], {"text": "티켓만", "refs": [{"kind": "ticket", "id": "LRM-9"}]})
        self.assertEqual(d["blockers"][0]["refs"], [{"kind": "task", "id": "T0007"}])  # 막힘에도 task 근거

    def test_task_bad_format_rejected(self):
        for bad in ("x [task:123]", "x [task:t0001]", "[task:T0001]", "x [foo:bar]"):
            r, d = self.write(tasks=[bad])
            self.assertEqual(r.returncode, 2, bad)
            self.assertIsNone(d)
            self.assertIn("--task", r.stderr)

    def test_short_form_rejected_when_many_repos(self):
        r, d = self.write("x [pr:#12]", repos="A/a,B/b")
        self.assertEqual(r.returncode, 2)
        self.assertIsNone(d)
        self.assertIn("pr:Owner/Repo#746", r.stderr)  # 안내에 전체 형식 예
        r, d = self.write(f"x [pr:{PR}]", repos="A/a,B/b")  # 전체 형식은 저장소가 여럿이어도 통과
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plain_blocker_stays_string(self):
        r, d = self.write("키 발급 막힘")
        self.assertEqual(d["blockers"], ["키 발급 막힘"])

    def test_bad_format_rejected(self):
        for bad in ("x [pr:746]", "x [decision:cfg-apply]", "x [ticket:lrm-1]", "x [foo:bar]", "[pr:#1]"):
            r, d = self.write(bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIsNone(d)
            self.assertIn("ERROR", r.stderr)


class EvidenceTest(Base):
    def setUp(self):
        super().setUp()
        d = self.t.name
        self.gh = os.path.join(d, "gh")
        with open(self.gh, "w") as f:  # pr view N → gh-pr-N.json (없으면 실패). 그 밖(pr list)은 빈 목록
            f.write(f'#!/bin/sh\necho "$@" >> "{d}/gh.calls"\nif [ "$1 $2" = "pr view" ]; then cat "{d}/gh-pr-$3.json" 2>/dev/null || exit 1; exit 0; fi\necho "[]"\n')
        os.chmod(self.gh, os.stat(self.gh).st_mode | stat.S_IXUSR)

    def pr_state(self, num, state):
        with open(os.path.join(self.t.name, f"gh-pr-{num}.json"), "w") as f:
            json.dump({"state": state, "headRefName": "fix/x"}, f)

    def put(self, blockers, hours_old=1, tasks=()):
        at = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat(timespec="seconds")
        with open(os.path.join(self.status, "ui.json"), "w", encoding="utf-8") as f:
            json.dump({"slug": "ui", "updatedAt": at, "tasks": list(tasks), "blockers": blockers}, f)

    def task_rows(self, **states):
        """작업 장부(tasks.jsonl) 기록: 번호=상태"""
        with open(os.path.join(self.t.name, "tasks.jsonl"), "w", encoding="utf-8") as f:
            for tid, st in states.items():
                f.write(json.dumps({"id": tid, "state": st, "text": "x"}, ensure_ascii=False) + "\n")

    def collect_tasks(self):
        r = self.run_once("--once", "--repo", "Basakansoft/LoveRevolutionMaker")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.load()
        return d, [i["title"] for i in d["items"] if i["key"].startswith("hub:ui:t:")]

    def collect(self, **extra):
        r = self.run_once("--once", "--repo", "Basakansoft/LoveRevolutionMaker", **extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.load()
        return d, [i for i in d["items"] if i["key"].startswith("hub:ui:b:")]

    def blocker(self, text, *refs):
        return {"text": text, "refs": [{"kind": k, "id": i} for k, i in refs]}

    def test_merged_pr_excluded_and_recorded(self):
        self.pr_state(746, "MERGED")
        self.put([self.blocker("병합 대기", ("pr", PR))])
        d, items = self.collect()
        self.assertEqual(items, [])
        self.assertEqual(d["resolved"][0]["text"], "병합 대기")
        self.assertEqual(d["resolved"][0]["refs"], [{"kind": "pr", "id": PR}])

    def test_same_number_other_repo_not_confused(self):
        d0 = self.t.name  # 저장소별로 다른 상태를 돌려주는 gh
        with open(self.gh, "w") as f:
            f.write(f'''#!/bin/sh
if [ "$1 $2" = "pr view" ]; then
  case "$*" in
    *Basakansoft/Other*) echo '{{"state":"OPEN","headRefName":"b"}}';;
    *) echo '{{"state":"MERGED","headRefName":"a"}}';;
  esac
  exit 0
fi
echo "[]"
''')
        self.put([self.blocker("A 대기", ("pr", "Basakansoft/LoveRevolutionMaker#746")), self.blocker("B 대기", ("pr", "Basakansoft/Other#746"))])
        d, items = self.collect()
        self.assertEqual([i["title"] for i in items], ["ui: B 대기"])  # 같은 번호라도 저장소별로 따로 판정·캐시
        d, items = self.collect()  # 캐시가 있어도 섞이지 않음
        self.assertEqual([i["title"] for i in items], ["ui: B 대기"])
        keys = [k for k in d["evidence"] if k.startswith("pr:")]
        self.assertEqual(sorted(keys), ["pr:Basakansoft/LoveRevolutionMaker#746", "pr:Basakansoft/Other#746"])

    def test_closed_unmerged_pr_kept_and_tagged(self):
        self.pr_state(746, "CLOSED")
        self.put([self.blocker("병합 대기", ("pr", PR))])
        d, items = self.collect()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["evidence"], "unmerged")
        self.assertIn("[PR 닫힘·미병합]", items[0]["title"])
        self.assertEqual(d["resolved"], [])

    def test_hold_answer_keeps_decision_blocker(self):
        with open(os.path.join(self.t.name, "slack-sent.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "1.1", "decision_id": "D-CFGAPPLY", "form_kind": "decide", "options": ["A", "B"], "tag": "ui"}) + "\n")
        with open(os.path.join(self.t.name, "slack-inbox.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "reaction", "parent_ts": "1.1", "kind": "decide", "decision_id": "D-CFGAPPLY", "action": "hold"}) + "\n")
        self.put([self.blocker("설정 답 대기", ("decision", "D-CFGAPPLY"))])
        d, items = self.collect()
        self.assertEqual(len(items), 1)  # 보류 답은 결정이 끝난 게 아님
        self.assertEqual(items[0]["evidence"], "open")

    def test_open_pr_kept(self):
        self.pr_state(746, "OPEN")
        self.put([self.blocker("병합 대기", ("pr", PR))])
        d, items = self.collect()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["evidence"], "open")
        self.assertEqual(d["resolved"], [])

    def test_gh_failure_keeps_blocker(self):
        self.put([self.blocker("병합 대기", ("pr", PR))])  # gh-pr-746.json 없음 → gh 실패
        d, items = self.collect()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["evidence"], "unknown")

    def test_pr_result_cached(self):
        self.pr_state(746, "OPEN")
        self.put([self.blocker("병합 대기", ("pr", PR))])
        self.collect()
        def calls():
            with open(os.path.join(self.t.name, "gh.calls")) as f:
                return sum(1 for ln in f if ln.startswith("pr view"))
        n = calls()
        self.collect()
        self.assertEqual(calls(), n)  # 10분 캐시: 두 번째 회차는 gh 를 다시 부르지 않음

    def test_answered_decision_excluded(self):
        with open(os.path.join(self.t.name, "slack-sent.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "1.1", "decision_id": "D-CFGAPPLY", "form_kind": "decide", "options": ["A", "B"], "tag": "ui"}) + "\n")
        with open(os.path.join(self.t.name, "slack-inbox.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "reaction", "parent_ts": "1.1", "kind": "decide", "decision_id": "D-CFGAPPLY", "action": "approve", "pick": 1}) + "\n")
        self.put([self.blocker("설정 답 대기", ("decision", "D-CFGAPPLY")), self.blocker("다른 답 대기", ("decision", "D-OTHER"))])
        d, items = self.collect()
        self.assertEqual([i["title"] for i in items], ["ui: 다른 답 대기"])  # 기록 없는 결정은 막힘 유지
        self.assertEqual(d["resolved"][0]["refs"], [{"kind": "decision", "id": "D-CFGAPPLY"}])

    def test_legacy_string_blocker(self):
        self.put(["키 발급 막힘"])
        d, items = self.collect()
        self.assertEqual([i["title"] for i in items], ["ui: 키 발급 막힘"])
        self.assertNotIn("evidence", items[0])

    def age_seen(self, hours):
        """막힘 최초 관측 시각을 hours 시간 전으로 되돌림(시험용: 실제로 기다리지 않음)"""
        with open(self.snap, encoding="utf-8") as f:
            d = json.load(f)
        for v in d["blockerSeen"].values():
            v["first"] -= hours * 3600
        with open(self.snap, "w", encoding="utf-8") as f:
            json.dump(d, f)

    def test_no_evidence_old_marked_not_deleted(self):
        self.put(["키 발급 막힘"])
        self.collect()
        self.age_seen(25)
        d, items = self.collect()
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["title"].startswith("[근거 없음·오래됨] "))

    def test_hub_rewrite_keeps_elapsed_time(self):
        self.put(["키 발급 막힘"])
        self.collect()
        self.age_seen(25)
        self.put(["키 발급  막힘 "], hours_old=0)  # 허브가 상태를 다시 씀: updatedAt 갱신 + 공백만 다름
        d, items = self.collect()
        self.assertTrue(items[0]["title"].startswith("[근거 없음·오래됨] "), items[0]["title"])
        self.assertEqual(len(d["blockerSeen"]), 1)

    def test_fresh_no_evidence_unmarked(self):
        self.put(["키 발급 막힘"], hours_old=23)
        d, items = self.collect()
        self.assertNotIn("근거 없음", items[0]["title"])

    def test_old_status_file_but_new_blocker_unmarked(self):
        self.put(["키 발급 막힘"], hours_old=48)  # 상태 파일은 오래됐어도 막힘은 지금 처음 봄
        d, items = self.collect()
        self.assertNotIn("근거 없음", items[0]["title"])

    def test_gone_blocker_seen_pruned_after_7_days(self):
        self.put(["키 발급 막힘"])
        self.collect()
        self.put([])
        self.age_seen(0)
        with open(self.snap, encoding="utf-8") as f:
            d = json.load(f)
        for v in d["blockerSeen"].values():
            v["last"] -= 6 * 86400
        with open(self.snap, "w", encoding="utf-8") as f:
            json.dump(d, f)
        self.assertEqual(len(self.collect()[0]["blockerSeen"]), 1)  # 6일: 유지
        with open(self.snap, encoding="utf-8") as f:
            d = json.load(f)
        for v in d["blockerSeen"].values():
            v["last"] -= 2 * 86400
        with open(self.snap, "w", encoding="utf-8") as f:
            json.dump(d, f)
        self.assertEqual(self.collect()[0]["blockerSeen"], {})  # 8일: 정리

    # ---- 진행 줄(tasks) 근거 ----

    def test_task_line_merged_pr_removed(self):
        self.pr_state(746, "MERGED")
        self.put([], tasks=[self.blocker("기능 구현", ("pr", PR)), "근거 없는 줄"])
        d, titles = self.collect_tasks()
        self.assertEqual(titles, ["ui: 근거 없는 줄"])
        self.assertEqual(d["resolved"], [{"hub": "ui", "line": "task", "text": "기능 구현", "refs": [{"kind": "pr", "id": PR}]}])

    def test_task_line_unmerged_close_ends_but_blocker_stays(self):
        self.pr_state(746, "CLOSED")
        self.put([self.blocker("병합 대기", ("pr", PR))], tasks=[self.blocker("기능 구현", ("pr", PR))])
        d, titles = self.collect_tasks()
        self.assertEqual(titles, [])  # 접힌 일은 진행 목록에서 내림
        self.assertEqual(len([i for i in d["items"] if i["key"].startswith("hub:ui:b:")]), 1)  # 막힘은 기존 규칙대로 유지

    def test_task_line_open_pr_and_unknown_kept(self):
        self.pr_state(746, "OPEN")
        self.put([], tasks=[self.blocker("열림", ("pr", PR)), self.blocker("조회 불가", ("pr", "Basakansoft/LoveRevolutionMaker#999"))])
        d, titles = self.collect_tasks()
        self.assertEqual(titles, ["ui: 열림", "ui: 조회 불가"])

    def test_task_line_answered_decision_removed(self):
        with open(os.path.join(self.t.name, "slack-sent.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "1.1", "decision_id": "D-CFGAPPLY", "form_kind": "decide", "options": ["A", "B"], "tag": "ui"}) + "\n")
        with open(os.path.join(self.t.name, "slack-inbox.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "reaction", "parent_ts": "1.1", "kind": "decide", "decision_id": "D-CFGAPPLY", "action": "approve", "pick": 1}) + "\n")
        self.put([], tasks=[self.blocker("답 반영", ("decision", "D-CFGAPPLY"))])
        self.assertEqual(self.collect_tasks()[1], [])

    def test_task_line_ledger_done_dropped_removed_open_kept(self):
        self.task_rows(T0001="완료", T0002="버림", T0003="진행")
        self.put([], tasks=[self.blocker("끝", ("task", "T0001")), self.blocker("버려짐", ("task", "T0002")), self.blocker("하는 중", ("task", "T0003")),
                            self.blocker("번호 없음", ("task", "T0404"))])
        d, titles = self.collect_tasks()
        self.assertEqual(titles, ["ui: 하는 중", "ui: 번호 없음"])  # 장부에 없는 번호는 모름 → 유지
        self.assertEqual([r["text"] for r in d["resolved"]], ["끝", "버려짐"])

    def test_task_line_ledger_missing_keeps_line(self):
        self.put([], tasks=[self.blocker("끝", ("task", "T0001"))])  # tasks.jsonl 없음
        self.assertEqual(self.collect_tasks()[1], ["ui: 끝"])

    def test_blocker_with_task_ref(self):
        self.task_rows(T0001="완료")
        self.put([self.blocker("장부 대기", ("task", "T0001"))])
        d, items = self.collect()
        self.assertEqual(items, [])
        self.assertEqual(d["resolved"][0]["refs"], [{"kind": "task", "id": "T0001"}])

    def test_string_tasks_still_read_and_ticket_only_kept(self):
        self.put([], tasks=["옛 문자열 줄", self.blocker("티켓만", ("ticket", "LRM-1"))])
        self.assertEqual(self.collect_tasks()[1], ["ui: 옛 문자열 줄", "ui: 티켓만"])

    def test_ticket_only_kept(self):
        self.put([self.blocker("티켓 대기", ("ticket", "LRM-436"))])
        d, items = self.collect()
        self.assertEqual(len(items), 1)
        self.assertNotIn("evidence", items[0])


if __name__ == "__main__":
    unittest.main()
