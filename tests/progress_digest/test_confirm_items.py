"""confirm_items.py 시험: 종류·상태·근거. 임시 작업 폴더·미리 채운 근거 캐시만 씀(gh·Slack·orca 호출 없음)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts"))
import answer_store  # noqa: E402
import confirm_items as ci  # noqa: E402
import todo_facts  # noqa: E402

NOW = 2_000_000_000
REPO = "Org/Repo"
PR = f"pr:{REPO}#746"
RAW = [
    ("decision:D-A", "wait", "결정 D-A 보상 방식 선택", NOW - 100),
    (PR, "wait", "#746 병합 대기", NOW - 50),
    ("hub:alpha:1", "wait", "alpha: 직접 실행 `bash run.sh --check` 필요", 0),
    ("hub:alpha:2", "block", "alpha: 구글 시트 입력 직접 입력 대기", 0),
    ("hub:alpha:3", "wait", "alpha: 창 직접 승인 대기", 0),
    ("hub:alpha:4", "wait", "alpha: 검수 자료 확인 대기", 0),
]


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.store = ci.Store(self.t.name)

    def facts(self, cards=None, prs=None):
        cache = {f"pr:{REPO}#{n}": {"state": s, "branch": "", "repo": REPO, "at": NOW} for n, s in (prs or {}).items()}
        f = todo_facts.Facts("gh", [REPO], lambda r: None, {"alpha"}, cache, NOW, use_pr=False)
        f.cards = dict(cards or {})
        f.dec_hubs = {"D-A": "alpha"}
        return f

    def build(self, raw=RAW, **kw):
        return ci.build(raw, NOW, self.facts(**kw), self.store)

    def by_key(self, items):
        return {i.key: i for i in items}


class KindTest(Base):
    def test_every_kind_is_classified(self):
        got = {i.key: i.kind for i in self.build()}
        self.assertEqual(got, {"decision:D-A": ci.DECISION, PR: ci.MERGE, "hub:alpha:1": ci.COMMAND, "hub:alpha:2": ci.SHEET,
                               "hub:alpha:3": ci.WINDOW, "hub:alpha:4": ci.REVIEW})

    def test_command_text_and_pr_url_and_decision_id_are_extracted(self):
        by = self.by_key(self.build())
        self.assertEqual(by["hub:alpha:1"].command, "bash run.sh --check")
        self.assertEqual(by[PR].url, f"https://github.com/{REPO}/pull/746")
        self.assertEqual(by["decision:D-A"].did, "D-A")
        self.assertEqual(by["hub:alpha:2"].command, "")

    def test_todo_first_then_by_kind_order(self):
        items = self.build()
        self.assertTrue(all(not i.done for i in items))
        self.assertEqual([i.kind for i in items], [ci.DECISION, ci.MERGE, ci.REVIEW, ci.COMMAND, ci.SHEET, ci.WINDOW])

    def test_only_unproven_kinds_are_confirmable(self):
        pub = {i.key: i.public()["confirmable"] for i in self.build()}
        self.assertEqual(sorted(k for k, v in pub.items() if v), ["hub:alpha:1", "hub:alpha:2", "hub:alpha:3", "hub:alpha:4"])


class StateTest(Base):
    def test_answered_decision_is_done_with_answer_source(self):
        with open(os.path.join(self.t.name, "slack-sent.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "1.1", "channel": "C", "title": "t", "form_kind": "decide", "decision_id": "D-A", "options": {"1": "a"}}) + "\n")
        with open(os.path.join(self.t.name, "slack-inbox.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "button", "decision_id": "D-A", "kind": "decide", "parent_ts": "1.1", "source": "docsite", "action": "pick", "pick": 1}) + "\n")
        d = self.by_key(self.build(cards={"D-A": True}))["decision:D-A"]
        self.assertEqual((d.state, d.basis), (ci.DONE, "이미 문서 사이트에서 답함"))

    def test_merged_pr_is_done_with_basis(self):
        p = self.by_key(self.build(prs={746: "MERGED"}))[PR]
        self.assertEqual((p.state, p.basis), (ci.DONE, "#746 병합됨"))

    def test_open_pr_stays_todo(self):
        self.assertFalse(self.by_key(self.build(prs={746: "OPEN"}))[PR].done)

    def test_confirm_log_marks_done_and_is_idempotent(self):
        self.assertTrue(self.store.confirm("hub:alpha:1", answer_store.DOCSITE, NOW))
        self.assertFalse(self.store.confirm("hub:alpha:1", answer_store.DOCSITE, NOW + 5))
        with open(self.store.log, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 1)
        by = self.by_key(self.build())
        self.assertTrue(by["hub:alpha:1"].done)
        self.assertIn("사용자 확인 — 문서 사이트", by["hub:alpha:1"].basis)
        self.assertFalse(by["hub:alpha:2"].done)

    def test_confirm_log_does_not_close_decision_or_pr(self):
        self.store.confirm("decision:D-A", answer_store.DOCSITE, NOW)
        self.store.confirm(PR, answer_store.DOCSITE, NOW)
        by = self.by_key(self.build())
        self.assertFalse(by["decision:D-A"].done or by[PR].done)

    def test_done_sorted_after_todo(self):
        self.store.confirm("hub:alpha:4", answer_store.DOCSITE, NOW)
        items = self.build()
        self.assertEqual(items[-1].key, "hub:alpha:4")


class VanishTest(Base):
    def test_vanished_merged_pr_stays_as_done_from_registry(self):
        self.build()  # 열린 상태로 명부에 기록
        got = self.by_key(self.build(raw=[r for r in RAW if r[0] != PR], prs={746: "MERGED"}))
        self.assertEqual((got[PR].state, got[PR].basis, got[PR].url), (ci.DONE, "#746 병합됨", f"https://github.com/{REPO}/pull/746"))

    def test_vanished_decision_without_evidence_is_dropped_not_done(self):
        self.build()
        got = self.by_key(self.build(raw=[r for r in RAW if not r[0].startswith("decision:")]))
        self.assertNotIn("decision:D-A", got)
        self.assertNotIn("decision:D-A", self.store.load_seen()["entries"])

    def test_vanished_hub_item_is_done_by_hub_report(self):
        self.build()
        got = self.by_key(self.build(raw=[r for r in RAW if r[0] != "hub:alpha:3"]))
        self.assertEqual((got["hub:alpha:3"].state, got["hub:alpha:3"].basis), (ci.DONE, "허브 상태에서 사라짐(허브 보고)"))

    def test_done_items_expire_after_keep_period(self):
        self.build()
        self.build(raw=[r for r in RAW if r[0] != "hub:alpha:3"])
        late = ci.build([r for r in RAW if r[0] != "hub:alpha:3"], NOW + ci.DONE_KEEP_S + 10, self.facts(), self.store)
        self.assertNotIn("hub:alpha:3", self.by_key(late))

    def test_registry_written_atomically_without_leftovers(self):
        self.build()
        self.assertEqual(sorted(n for n in os.listdir(self.t.name) if n.startswith(".confirm-seen")), [])
        self.assertIn("hub:alpha:1", self.store.load_seen()["entries"])

    def test_persist_false_writes_nothing(self):
        ci.build(RAW, NOW, self.facts(), self.store, persist=False)
        self.assertFalse(os.path.exists(self.store.seen_path))


if __name__ == "__main__":
    unittest.main()
