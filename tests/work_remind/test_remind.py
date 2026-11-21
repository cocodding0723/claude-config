"""남은 작업 리마인드 시험: 무엇을 '내 차례'·'진행 중'·'멈춤'으로 세는지, 언제 보내는지. 작업판·Slack 은 쓰지 않음(순수 함수 + 가짜 전송 스크립트)."""
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import board_cards  # noqa: E402
import pending_judge  # noqa: E402
import work_remind as wr  # noqa: E402

NOW = 1795053600.0  # 2026-11-19 11:00 KST
HOUR = 3600
RULES = pending_judge.Rules(max_age_h=48, auto=pending_judge.compile_auto([r"D-ST[0-9a-f]{10}"]))


def sent(did, ts, by="", at=NOW - HOUR, title="계획 승인: 불꽃 연출"):
    return {"decision_id": did, "ts": ts, "at": at, "by": by, "form_kind": "decide", "options": {"1": "승인"}, "title": title}


def card(cid, status="open", labels=(), title="", kind="task", updated=NOW - 600, assignee=""):
    iso = __import__("datetime").datetime.fromtimestamp(updated, __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": cid, "status": status, "labels": list(labels), "title": title or cid, "issue_type": kind, "updated_at": iso, "assignee": assignee}


class WaitingTest(unittest.TestCase):
    """답을 기다리는 결정. 경우를 더할 때는 CASES 에 한 줄"""
    CASES = [
        ("답이 없는 결정 카드는 기다리는 중", [sent("D-A", "1.1")], ["D-A"]),
        ("답한 카드는 뺌", [sent("D-A", "1.1", by="slack")], []),
        ("같은 번호로 다시 올렸으면 가장 최근 카드만 봄", [sent("D-A", "1.1"), sent("D-A", "2.2", by="slack")], []),
        ("다시 올린 새 카드에 답이 없으면 기다리는 중", [sent("D-A", "1.1", by="slack"), sent("D-A", "2.2")], ["D-A"]),
        ("감시기 자동 알림 카드는 사람이 정할 것이 아님", [sent("D-ST0123456789", "1.1")], []),
        ("기한(48시간)을 넘긴 결정은 다시 알리지 않음", [sent("D-A", "1.1", at=NOW - 49 * HOUR)], []),
        ("선택지 없는 옛 글은 결정이 아님", [dict(sent("D-A", "1.1"), options=None, form_kind="progress")], []),
    ]

    def test_cases(self):
        for name, cards, want in self.CASES:
            with self.subTest(name):
                self.assertEqual([c["decision_id"] for c in wr.waiting(cards, RULES, NOW)], want)


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.cards = [
            card("t-e1", labels=["feature:flame", "flow:v1", "stage:build", "size:normal"], title="[LRM-684] 텐션업 불꽃 상승 연출", kind="epic"),
            card("t-e1.1", "closed"), card("t-e1.2", "in_progress", assignee="w"), card("t-e1.3"),
            card("t-e2", "closed", labels=["feature:banner", "stage:review"], title="[LRM-677] 승부 배너 앵커 자동 배치", kind="epic", updated=NOW - 20 * HOUR),
            card("t-e3", "closed", labels=["feature:old"], title="끝난 기능", kind="epic"),
            card("t-e4", labels=[], title="[EPIC] 버그 팀 11-17~", kind="epic"),
            card("t-e4.1", "in_progress", title="[LRM-645] 초상화 뒤바뀜 — 원인 조사", updated=NOW - 50 * HOUR, assignee="bug"),
        ]

    def test_review_waiting_features_are_my_turn(self):
        self.assertEqual([r["name"] for r in wr.reviews(self.cards)], ["승부 배너 앵커 자동 배치"])

    def test_open_features_show_stage_and_card_progress(self):
        got = wr.features(self.cards, wr.flow_lib())
        self.assertEqual([(f["name"], f["stage"], f["done"], f["total"]) for f in got],
                         [("텐션업 불꽃 상승 연출", "3 구현", 1, 3)])

    def test_feature_being_shipped_is_still_listed_after_its_card_closed(self):
        """검수를 통과해 반영 중인 기능은 카드가 닫혀 있어도 남은 일(PR·병합)"""
        self.cards.append(card("t-e5", "closed", labels=["feature:ship", "stage:ship"], title="[LRM-700] 반영 중인 기능", kind="epic"))
        self.assertIn(("반영 중인 기능", "6 반영"), [(f["name"], f["stage"]) for f in wr.features(self.cards, wr.flow_lib())])

    def test_feature_without_feature_label_is_not_listed_as_feature(self):
        self.assertNotIn("버그 팀 11-17~", [f["name"] for f in wr.features(self.cards)])

    def test_in_progress_cards_without_update_are_stalled(self):
        got = wr.stalled(self.cards, NOW, hours=24)
        self.assertEqual([(s["name"], s["days"]) for s in got], [("초상화 뒤바뀜 — 원인 조사", 2)])


class BuildTest(unittest.TestCase):
    def build(self, **over):
        parts = dict(decisions=[sent("D-A", "1.1")], reviews=[{"name": "승부 배너 앵커 자동 배치", "since": NOW - 20 * HOUR}],
                     features=[{"name": "텐션업 불꽃 상승 연출", "stage": "3 구현", "done": 1, "total": 3}],
                     stalled=[{"name": "초상화 뒤바뀜 — 원인 조사", "days": 2}])
        parts.update(over)
        return wr.build(NOW, **parts)

    def test_my_turn_comes_first_with_count_in_title(self):
        lines = self.build().splitlines()
        self.assertEqual(lines[0], "📋 남은 작업 11:00 — 내 차례 2건")
        self.assertLess(lines.index("🙋 내 차례"), lines.index("🔄 진행 중"))
        self.assertIn("- 결정: 계획 승인: 불꽃 연출", lines)
        self.assertIn("- 검수: 승부 배너 앵커 자동 배치", lines)
        self.assertIn("- 텐션업 불꽃 상승 연출: 3 구현, 카드 1/3", lines)
        self.assertIn("- 초상화 뒤바뀜 — 원인 조사: 2일째 갱신 없음", lines)

    def test_nothing_left_means_no_message(self):
        self.assertEqual(self.build(decisions=[], reviews=[], features=[], stalled=[]), "")

    def test_no_my_turn_section_when_nothing_waits_on_me(self):
        text = self.build(decisions=[], reviews=[])
        self.assertTrue(text.startswith("📋 남은 작업 11:00 — 내 차례 없음"))
        self.assertNotIn("🙋 내 차례", text)

    def test_message_passes_the_slack_format_check(self):
        """티켓 번호·카드 번호를 본문에 넣지 않고, 한 줄에 사실 하나"""
        text = self.build()
        self.assertEqual([g for g in board_cards.text_gaps(text, skip_first=True) if g[2] == "reject"], [])
        self.assertNotRegex(text, r"LRM-\d+")
        self.assertLessEqual(len(text.splitlines()[0].replace("📋", "").strip()), 40)

    def test_long_lists_are_cut_with_a_count(self):
        many = [{"name": f"기능 {i}", "stage": "3 구현", "done": 0, "total": 1} for i in range(12)]
        lines = self.build(features=many).splitlines()
        self.assertIn("- 그 밖에 4건", lines)


class SendRuleTest(unittest.TestCase):
    """언제 보내는가: 내 차례가 있으면 매번, 없으면 내용이 바뀐 때만"""
    CASES = [
        ("내 차례가 있으면 같은 내용이어도 다시 알림", "본문", 2, {"body": "본문"}, True),
        ("내 차례가 없고 내용이 같으면 보내지 않음", "본문", 0, {"body": "본문"}, False),
        ("내 차례가 없어도 내용이 바뀌면 보냄", "새 본문", 0, {"body": "본문"}, True),
        ("남은 것이 없으면 보내지 않음", "", 0, {"body": "본문"}, False),
        ("첫 실행은 보냄", "본문", 0, {}, True),
    ]

    def test_cases(self):
        for name, text, mine, state, want in self.CASES:
            with self.subTest(name):
                self.assertEqual(wr.should_send(text, mine, state), want)

    def test_time_in_title_does_not_count_as_a_change(self):
        a = wr.build(NOW, decisions=[], reviews=[], features=[{"name": "기능", "stage": "3 구현", "done": 0, "total": 1}], stalled=[])
        b = wr.build(NOW + 1800, decisions=[], reviews=[], features=[{"name": "기능", "stage": "3 구현", "done": 0, "total": 1}], stalled=[])
        self.assertNotEqual(a, b)
        self.assertFalse(wr.should_send(b, 0, {"body": wr.body_key(a)}))


class RunTest(unittest.TestCase):
    """보내기: 새 글을 올리고, 앞 리마인드는 한 줄로 줄여 DM 에 옛 목록이 쌓이지 않게 함"""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name
        self.fake = os.path.join(self.dir, "fake_notify.sh")
        with open(self.fake, "w") as f:
            f.write('#!/bin/bash\nprintf \'%s\\n\' "$(printf \'%s\\t\' "$@")" >> "$FAKE_LOG"\n[ -n "${FAKE_FAIL:-}" ] && { echo "거부: 시험" >&2; exit 2; }\necho "ok 1795053700.000100"\n')
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IXUSR)
        self.log = os.path.join(self.dir, "calls.log")
        os.environ["FAKE_LOG"] = self.log
        self.addCleanup(os.environ.pop, "FAKE_LOG", None)
        self.state = os.path.join(self.dir, "state.json")

    def calls(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                return [ln.rstrip("\n").split("\t")[:-1] for ln in f]
        except FileNotFoundError:
            return []

    def test_first_send_posts_and_remembers_the_message(self):
        out = wr.send("📋 남은 작업 11:00 — 내 차례 1건\n\n🙋 내 차례\n- 결정: x", 1, self.fake, self.state, NOW)
        self.assertEqual(out, "sent")
        self.assertEqual(self.calls()[0][:4], ["--tag", "remind", "--kind", "progress"])
        with open(self.state) as f:
            self.assertEqual(json.load(f)["ts"], "1795053700.000100")

    def test_next_send_shrinks_the_previous_message(self):
        wr.send("📋 남은 작업 11:00 — 내 차례 1건\n\n🙋 내 차례\n- 결정: x", 1, self.fake, self.state, NOW)
        wr.send("📋 남은 작업 11:30 — 내 차례 1건\n\n🙋 내 차례\n- 결정: x", 1, self.fake, self.state, NOW + 1800)
        upd = [c for c in self.calls() if "--update" in c]
        self.assertEqual(len(upd), 1)
        self.assertEqual(upd[0][upd[0].index("--update") + 1], "1795053700.000100")
        self.assertIn("11:00", upd[0][-1])

    def test_skipped_round_sends_nothing(self):
        wr.send("본문", 0, self.fake, self.state, NOW)
        n = len(self.calls())
        self.assertEqual(wr.send("본문", 0, self.fake, self.state, NOW + 1800), "skip")
        self.assertEqual(len(self.calls()), n)

    def test_failed_send_keeps_the_old_state_so_next_round_retries(self):
        os.environ["FAKE_FAIL"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_FAIL", None)
        self.assertTrue(wr.send("본문", 1, self.fake, self.state, NOW).startswith("fail"))
        self.assertFalse(os.path.exists(self.state))


SLACK_PASS = "Slack 결정 답: 사용자 원문: 결정 D-LRM687REVIEW = 선택 1(통과(지금 소리 유지)) (버튼) (출처: Slack)"


class PassedTest(unittest.TestCase):
    """기능 카드에 검수 통과가 이미 적혀 있는가. 경우를 더할 때는 CASES 에 한 줄"""
    CASES = [
        ("통과 기록(user-ok)이 있으면 통과", [(NOW, "user-ok: 통과, PR 올려")], NOW - HOUR, True),
        ("코디네이터가 꺼져 기록은 없어도 Slack 통과 답 코멘트가 있으면 통과", [(NOW, SLACK_PASS)], NOW - HOUR, True),
        ("고칠 점 답은 통과가 아님", [(NOW, "Slack 결정 답: 사용자 원문: 결정 D-X = 선택 2(고칠 점 있음) (버튼) (출처: Slack)")], NOW - HOUR, False),
        ("카드가 닫히기 전의 통과 답은 다른 결정의 답", [(NOW - 2 * HOUR, SLACK_PASS)], NOW - HOUR, False),
        ("통과라는 말이 든 보고 글은 통과 답이 아님", [(NOW, "기능 완료. 검수 통과 대기")], NOW - HOUR, False),
        ("코멘트가 없으면 통과 아님", [], NOW - HOUR, False),
    ]

    def test_cases(self):
        for name, comments, since, want in self.CASES:
            with self.subTest(name):
                self.assertEqual(wr.passed(comments, since), want)


class PrSummaryTest(unittest.TestCase):
    CASES = [
        ("열린 PR 이 하나라도 있으면 열림", [(923, "OPEN"), (901, "MERGED")], ("open", "PR #923 열림")),
        ("열린 것 없이 병합된 것이 있으면 병합됨", [(1005, "MERGED"), (1002, "MERGED")], ("merged", "PR #1002·#1005 병합됨")),
        ("병합 없이 닫힌 것뿐이면 닫힘", [(966, "CLOSED")], ("closed", "PR #966 병합 없이 닫힘")),
        ("PR 이 없으면 표시 없음", [], ("none", "")),
        ("조회를 못 했으면 모름(표시 없음)", None, ("unknown", "")),
    ]

    def test_cases(self):
        for name, prs, want in self.CASES:
            with self.subTest(name):
                got = wr.pr_summary(prs)
                self.assertEqual((got["state"], got["note"]), want)


class RealityTest(unittest.TestCase):
    """작업판 표시와 실제가 어긋날 때(11-19: 병합된 기능이 진행 중으로, 통과 답을 낸 기능이 검수 대기로 뜸)"""

    def setUp(self):
        self.review = dict(card("t-r1", "closed", labels=["feature:sfx", "stage:review"], title="[LRM-687] VS 증감 효과음", kind="epic"),
                           closed_at="2026-11-19T02:28:56Z")
        self.open = card("t-o1", "open", labels=["feature:lines", "stage:build"], title="[LRM-689] 승부·찬스 대사", kind="epic")

    def test_review_the_user_already_passed_is_not_my_turn(self):
        got = wr.reviews([self.review], comments_of=lambda c: [(wr.stamp("2026-11-19T02:37:00Z"), SLACK_PASS)], prs_of=lambda c: [])
        self.assertEqual([(r["name"], r["passed"], r["done"]) for r in got], [("VS 증감 효과음", True, True)])

    def test_review_without_a_pass_stays_my_turn_with_its_pr_state(self):
        got = wr.reviews([self.review], comments_of=lambda c: [], prs_of=lambda c: [(923, "OPEN")])
        self.assertEqual([(r["done"], r["pr"]["note"]) for r in got], [(False, "PR #923 열림")])

    def test_feature_whose_pr_merged_is_marked_merged(self):
        got = wr.features([self.open, card("t-o1.1")], wr.flow_lib(), prs_of=lambda c: [(1002, "MERGED"), (1005, "MERGED")])
        self.assertEqual([(f["name"], f["merged"]) for f in got], [("승부·찬스 대사", True)])

    def test_message_moves_them_out_of_my_turn_and_out_of_in_progress(self):
        revs = wr.reviews([self.review], comments_of=lambda c: [(wr.stamp("2026-11-19T02:37:00Z"), SLACK_PASS)], prs_of=lambda c: [])
        feats = wr.features([self.open, card("t-o1.1")], wr.flow_lib(), prs_of=lambda c: [(1002, "MERGED")])
        lines = wr.build(NOW, decisions=[], reviews=revs, features=feats, stalled=[]).splitlines()
        self.assertEqual(lines[0], "📋 남은 작업 11:00 — 내 차례 없음")
        self.assertNotIn("🙋 내 차례", lines)
        self.assertNotIn("🔄 진행 중", lines)
        self.assertIn("🧹 정리만 남음", lines)
        self.assertIn("- VS 증감 효과음: 통과 답을 이미 냄", lines)
        self.assertIn("- 승부·찬스 대사: 병합됨, 남은 카드 1 — PR #1002 병합됨", lines)

    def test_message_with_pr_notes_passes_the_slack_format_check(self):
        revs = wr.reviews([self.review], comments_of=lambda c: [], prs_of=lambda c: [(923, "OPEN")])
        feats = wr.features([self.open, card("t-o1.1")], wr.flow_lib(), prs_of=lambda c: [(1002, "MERGED"), (1005, "MERGED")])
        text = wr.build(NOW, decisions=[], reviews=revs, features=feats, stalled=[])
        self.assertEqual([g for g in board_cards.text_gaps(text, skip_first=True) if g[2] == "reject"], [])
        self.assertNotRegex(text, r"LRM-\d+")


class ClassificationTest(unittest.TestCase):
    def epic(self, status="open", stage="build", **extra):
        return dict(card("f", status, labels=["feature:x", "stage:" + stage], kind="epic"), **extra)

    def test_all_merged_without_remaining_cards_disappears_in_every_stage(self):
        for status in ("open", "closed"):
            for stage in ("build", "review", "ship"):
                for kids in ([], [card("f.1", "closed")]):
                    with self.subTest(status=status, stage=stage, kids=kids):
                        self.assertEqual(wr.features([self.epic(status, stage), *kids], prs_of=lambda c: [(1, "MERGED")]), [])

    def test_all_merged_with_remaining_cards_stays_in_cleanup(self):
        for status in ("open", "closed"):
            for stage in ("build", "review", "ship"):
                cards = [self.epic(status, stage), card("f.1", "closed"), card("f.2"), card("f.3", "in_progress")]
                features = wr.features(cards, prs_of=lambda c: [(1, "MERGED")])
                self.assertEqual(features[0]["remaining"], 2)
                text = wr.build(NOW, [], [], features, [])
                self.assertIn("🧹 정리만 남음", text)
                self.assertIn("병합됨, 남은 카드 2", text)
                self.assertNotIn("🔄 진행 중", text)

    def test_missing_open_or_unmerged_pr_does_not_disappear(self):
        for prs in (None, [], [(1, "OPEN")], [(1, "CLOSED")], [(1, "MERGED"), (2, "OPEN")], [(1, "MERGED"), (2, "CLOSED")]):
            with self.subTest(prs=prs):
                features = wr.features([self.epic()], prs_of=lambda c: prs)
                self.assertEqual(len(features), 1)
                self.assertFalse(features[0]["merged"])

    def test_merged_and_passed_review_disappears(self):
        c = self.epic("closed", "review")
        for prs in ([(1, "MERGED")], [(1, "MERGED"), (2, "CLOSED")]):
            got = wr.reviews([c], comments_of=lambda c: [(NOW, "user-ok: 통과")], prs_of=lambda c: prs)
            self.assertEqual(got, [])
            self.assertEqual(wr.build(NOW, [], got, [], []), "")

    def test_merged_review_without_pass_keeps_existing_cleanup_rule(self):
        got = wr.reviews([self.epic(stage="review")], comments_of=lambda c: [], prs_of=lambda c: [(1, "MERGED")])
        self.assertTrue(got[0]["done"])
        self.assertFalse(got[0]["passed"])

    def test_missing_hub_is_only_marked_when_known_to_have_no_pr(self):
        with tempfile.TemporaryDirectory() as existing:
            missing = os.path.join(existing, "missing")
            for description, prs, status, want in (
                ("기능 코디네이터 허브: " + missing, [], "open", True),
                ("설명\n기능 코디네이터 허브: " + missing + "\n다음 줄", [], "open", True),
                ("기능 코디네이터 허브: " + existing, [], "open", False),
                ("경로 줄 없음", [], "open", False),
                ("기능 코디네이터 허브: \n다음 줄", [], "open", False),
                ("기능 코디네이터 허브: " + missing, None, "open", False),
                ("기능 코디네이터 허브: " + missing, [(1, "OPEN")], "open", False),
                ("기능 코디네이터 허브: " + missing, [], "closed", False),
            ):
                with self.subTest(description=description, prs=prs, status=status):
                    features = wr.features([self.epic(status, "ship", description=description)], prs_of=lambda c: prs)
                    text = wr.build(NOW, [], [], features, [])
                    self.assertEqual(" — 맡은 코디네이터 없음" in text, want)
                    self.assertIn("🔄 진행 중", text)

    def test_stalled_children_of_closed_or_merged_parent_disappear(self):
        for status, prs, visible in (("closed", None, False), ("open", [(1, "MERGED")], False),
                                     ("open", [(1, "OPEN")], True), ("open", None, True),
                                     ("open", [(1, "MERGED"), (2, "CLOSED")], True)):
            cards = [self.epic(status), card("f.1", "in_progress", updated=NOW - 50 * HOUR),
                     card("f2.1", "in_progress", updated=NOW - 50 * HOUR)]
            got = wr.stalled(cards, NOW, prs_of=lambda c: prs)
            self.assertEqual([s["name"] for s in got], ["f.1", "f2.1"] if visible else ["f2.1"])

    def test_invalid_first_repo_falls_back_without_mutating_card(self):
        c = self.epic()
        c["labels"] += ["repo:/hub", "repo:/actual", "branch:feature/x"]
        original = list(c["labels"])
        index = Mock()
        index.for_card.side_effect = [None, [(1, "MERGED")]]
        self.assertEqual(wr.card_prs(index, c), [(1, "MERGED")])
        self.assertEqual(index.for_card.call_count, 2)
        self.assertEqual(index.for_card.call_args.args[0]["labels"], ["repo:/actual", "feature:x", "stage:build", "branch:feature/x"])
        self.assertEqual(c["labels"], original)

    def test_empty_valid_repo_does_not_fall_back(self):
        index = Mock()
        index.for_card.return_value = []
        self.assertEqual(wr.card_prs(index, dict(labels=["repo:/first", "repo:/second"])), [])
        self.assertEqual(index.for_card.call_count, 1)


if __name__ == "__main__":
    unittest.main()
