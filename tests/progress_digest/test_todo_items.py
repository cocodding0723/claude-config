"""todo_items.py 시험: 종류 분류·급함·제목 생성·🔄 제외. 외부 호출 없음."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts"))
import todo_items as t  # noqa: E402

H = 3600


class ClassifyTest(unittest.TestCase):
    def test_four_kinds(self):
        c = t.classify
        self.assertEqual(c("decision:D-X1", "wait", "결정 반동 범위"), t.DECISION)
        self.assertEqual(c("hub:vfx:b:1", "block", "vfx: 결정 대기 5개: D-VFX0101"), t.DECISION)
        self.assertEqual(c("pr:r#731", "wait", "PR #731 제목"), t.MERGE)
        self.assertEqual(c("hub:ui:t:1", "wait", "ui: 데이터 허브 #731 병합 대기"), t.MERGE)
        self.assertEqual(c("hub:server:b:1", "block", "server: 라이브 규칙 창 직접 승인"), t.INPUT)
        self.assertEqual(c("hub:x:b:1", "block", "x: 공용 stash 삭제(사용자)"), t.INPUT)
        self.assertEqual(c("hub:vfx:b:2", "block", "vfx: 묶음 PR: 사용자 검수"), t.REVIEW)

    def test_progress_and_pr_status_excluded(self):
        c = t.classify
        self.assertIsNone(c("hub:vfx:t:1", "doing", "vfx: 녹화 2차 진행 중, 병합 뒤 재실행"))
        self.assertIsNone(c("pr:r#730", "doing", "PR #730 제목"))
        self.assertIsNone(c("pr:r#730", "block", "PR #730 충돌"))  # PR 상태는 진행 목록 DM 에만
        self.assertIsNone(c("ledger:F1", "doing", "F1 요청"))
        self.assertIsNone(c("ledger:C1", "block", "C1 접수 시험"))
        self.assertIsNone(c("hub:s:b:1", "block", "s: Codex 한도 소진 — 워커 Claude"))
        self.assertIsNone(c("hub:q:t:1", "wait", "q: 구조안 승인 대기 예정"))  # 앞으로의 일


class TitleTest(unittest.TestCase):
    def title(self, key, bucket, text):
        rows = t.build_rows([(key, bucket, text, 0)], 100, {})
        return rows[0] if rows else None

    def test_merge_pr_title_not_copied(self):
        r = self.title("pr:o/r#730", "wait", "PR LoveRevolutionMaker#730 [LRM-436] fix: 1일차 지도 스모크")
        self.assertEqual((r.title, r.answer), ("#730 머지", "#730 머지 버튼"))

    def test_input_title_names_hub_window(self):
        r = self.title("hub:server:b:1", "block", "server: 라이브 규칙 창 직접 승인")
        self.assertEqual(r.title, "라이브 규칙 창 직접 승인 — server 허브 창")
        self.assertEqual(r.answer, "server 허브 창 1. Yes")

    def test_no_internal_symbols(self):
        r = self.title("hub:vfx:b:1", "block", "vfx: 점검 결과 확인(ctx_5c701880b937) 7540648c5 사용자 검수")
        self.assertNotIn("ctx_", r.title)
        self.assertNotRegex(r.title, r"[0-9a-f]{7,}")

    def test_decision_card_id_in_answer(self):
        r = self.title("hub:server:b:1", "block", "server: D-TESTERAUTO")
        self.assertEqual((r.title, r.answer), ("D-TESTERAUTO 결정", "결정 D-TESTERAUTO A 식으로 카드에 답"))

    def test_decision_card_head_and_ellipsis_removed(self):
        self.assertEqual(self.title("decision:x1", "wait", "결정 비서-선택해주세요: 환경 동기화 도구 PR 올려도 될까?").title, "환경 동기화 도구 PR 올려도 될까?")
        self.assertEqual(self.title("decision:x2", "wait", "결정 오토마 push, 3곳 직렬화 …").title, "오토마 push, 3곳 직렬화 결정")

    def test_same_title_one_row(self):
        rows = t.build_rows([("hub:a:b:1", "block", "a: 권한 결정", 0), ("hub:a:t:2", "wait", "a: 권한 결정 대기", 0)], 100, {})
        self.assertEqual(len(rows), 1)

    def test_length_limit(self):
        r = self.title("decision:D-X", "wait", "결정 " + "가" * 200)
        self.assertLessEqual(len(r.title), t.TITLE_MAX)


class UrgencyTest(unittest.TestCase):
    def test_steps(self):
        self.assertEqual(t.urgency("wait", 0), 1)
        self.assertEqual(t.urgency("wait", 4 * H), 2)
        self.assertEqual(t.urgency("wait", 24 * H), 3)

    def test_blocked_boost_and_cap(self):
        self.assertEqual(t.urgency("block", 0), 2)
        self.assertEqual(t.urgency("block", 30 * H), 3)

    def test_card_uses_card_time_hub_uses_first_seen(self):
        now = 100 * H
        rows = t.build_rows([("decision:D-A", "wait", "결정 가", now - 25 * H), ("hub:a:b:1", "wait", "a: 권한 결정", now)], now, {"hub:a:b:1": now - 5 * H})
        self.assertEqual([r.urgency for r in rows], [3, 2])


if __name__ == "__main__":
    unittest.main()
