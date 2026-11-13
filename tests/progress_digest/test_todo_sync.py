"""todo_sync.py·todo_items 대상 키·정렬 시험. 순수 함수만 씀."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts"))
import todo_items  # noqa: E402
import todo_sync  # noqa: E402

NOW = 1_000_000


def rows(*items):
    return todo_items.build_rows([(k, b, t, s) for k, b, t, s in items], NOW, {})


class TargetsTest(unittest.TestCase):
    def test_target_keys_by_kind(self):
        got = {r.key: r.targets for r in rows(
            ("pr:Org/Repo#753", "wait", "PR Repo#753 제목", 1),
            ("hub:a:b:1", "block", "a: #751·#746 병합 대기", 1),
            ("hub:a:b:2", "wait", "a: LRM-395 D-JSID 답 대기", 1),
            ("decision:D-X", "wait", "결정 :비서-선택해주세요: 뭘까?", 1),
            ("hub:a:b:3", "block", "a: 탭 발행 사용자 직접 입력", 1))}
        self.assertEqual(got["pr:Org/Repo#753"], ("pr:753",))
        self.assertEqual(got["hub:a:b:1"], ("pr:751", "pr:746"))
        self.assertEqual(got["hub:a:b:2"], ("decision:D-JSID",))
        self.assertEqual(got["decision:D-X"], ("decision:D-X",))
        self.assertEqual(got["hub:a:b:3"], ())  # 직접 입력은 대상으로 완료를 판정하지 않음


class SettleTest(unittest.TestCase):
    def test_closed_target_completes_row(self):
        r = rows(("hub:a:b:1", "wait", "a: #731 병합 대기", 1))
        kept, dropped = todo_sync.settle(r, {"pr:731"})
        self.assertEqual((kept, [(d.key, w) for d, w, _ in dropped]), ([], [("hub:a:b:1", "done")]))

    def test_same_target_rows_become_one_keeping_existing(self):
        r = rows(("hub:a:b:1", "wait", "a: #731 병합 대기", 1), ("hub:b:b:2", "block", "b: #731 머지", 2), ("pr:R#731", "wait", "PR #731 x", 3))
        kept, dropped = todo_sync.settle(r, set(), prefer=["hub:b:b:2"])
        self.assertEqual([k.key for k in kept], ["hub:b:b:2"])
        self.assertEqual(sorted(d.key for d, w, _ in dropped), ["hub:a:b:1", "pr:R#731"])
        self.assertTrue(all(w == "dup" for _, w, _ in dropped))
        self.assertEqual(kept[0].urgency, max(x.urgency for x in r))

    def test_partial_close_rewrites_title_then_merges_with_existing_row(self):
        r = rows(("hub:a:b:1", "wait", "a: #751·#746 병합 대기", 1), ("hub:s:b:2", "block", "s: #746 병합", 1))
        kept, dropped = todo_sync.settle(r, {"pr:751"}, prefer=["hub:s:b:2"])
        self.assertEqual([(k.key, k.title) for k in kept], [("hub:s:b:2", "#746 머지")])
        self.assertEqual([d.key for d, _, _ in dropped], ["hub:a:b:1"])

    def test_overlapping_targets_union(self):
        r = rows(("hub:a:b:1", "wait", "a: #751·#746 병합 대기", 1), ("hub:s:b:2", "block", "s: #746 병합", 1))
        kept, _ = todo_sync.settle(r, set())
        self.assertEqual([(k.targets, k.title) for k in kept], [(("pr:751", "pr:746"), "#751·#746 머지")])

    def test_rows_without_targets_untouched(self):
        r = rows(("hub:a:b:1", "block", "a: #742 강제 push 사용자 창 승인", 1))
        kept, dropped = todo_sync.settle(r, {"pr:742"})
        self.assertEqual((len(kept), dropped), (1, []))

    def test_same_title_collected(self):
        same = []
        out = todo_items.build_rows([("hub:a:b:1", "block", "a: 탭 발행 사용자 직접 입력", 1), ("hub:a:b:2", "block", "a: 탭 발행 사용자 직접 입력", 1)], NOW, {}, same)
        self.assertEqual((len(out), same), (1, ["hub:a:b:2"]))


class SortTest(unittest.TestCase):
    def test_order_urgency_then_kind_then_oldest(self):
        mk = lambda key, kind, urg, since: todo_items.Row(key, kind, key, urg, "", "", (), since)  # noqa: E731
        r = [mk("old-input", todo_items.INPUT, 1, 10), mk("merge3", todo_items.MERGE, 3, 50), mk("dec3-new", todo_items.DECISION, 3, 90),
             mk("dec3-old", todo_items.DECISION, 3, 20), mk("review1", todo_items.REVIEW, 1, 5), mk("merge1", todo_items.MERGE, 1, 5)]
        self.assertEqual([x.key for x in sorted(r, key=todo_items.sort_key)], ["dec3-old", "dec3-new", "merge3", "merge1", "review1", "old-input"])
        self.assertEqual([x.key for x in sorted(r, key=todo_items.order_value)], [x.key for x in sorted(r, key=todo_items.sort_key)])

    def test_order_value_ignores_other_rows(self):
        a = todo_items.Row("a", todo_items.MERGE, "a", 2, "", "", (), 1_794_000_000)
        self.assertEqual(todo_items.order_value(a), 1 * 10**11 + 1 * 10**10 + 1_794_000_000)


if __name__ == "__main__":
    unittest.main()
