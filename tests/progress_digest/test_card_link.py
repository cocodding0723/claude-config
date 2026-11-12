"""결정 카드 링크 시험: 진행 항목·DM 목록이 결정 카드의 Slack 메시지 링크를 달고, 채널·ts 가 없으면 링크를 생략."""
import os
import sys
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import progress_collect  # noqa: E402
import progress_digest  # noqa: E402


class CardLinkTest(unittest.TestCase):
    def test_decision_item_gets_card_link(self):
        items = progress_collect.decision_items({"items": [{"title": "알림 시각", "id": "D-0001", "ts": "1700000000.000100", "channel": "D1"}]})
        self.assertEqual(items[0]["link"], "https://slack.com/archives/D1/p1700000000000100")

    def test_missing_channel_or_ts_omits_link(self):
        items = progress_collect.decision_items({"items": [{"title": "a", "id": "D-1"}, {"title": "b", "id": "D-2", "ts": "1.2", "channel": None}]})
        self.assertEqual([i["link"] for i in items], ["", ""])

    def test_dm_lists_decision_as_slack_link_and_escapes_syntax_chars(self):
        link = "https://slack.com/archives/D1/p1700000000000100"
        item = progress_digest.Item("decision:D-0001", progress_digest.WAIT, "결정 A|B<C>", link)
        self.assertEqual(progress_digest.name_lines([item]), [f"- <{link}|결정 ABC>"])

    def test_dm_without_link_is_plain(self):
        item = progress_digest.Item("decision:D-0002", progress_digest.WAIT, "결정 알림", "")
        self.assertEqual(progress_digest.name_lines([item]), ["- 결정 알림"])

    def test_non_decision_item_keeps_plain_name(self):
        item = progress_digest.Item("pr:1", progress_digest.WAIT, "PR 병합 대기", "https://github.com/x/y/pull/1")
        self.assertEqual(progress_digest.name_lines([item]), ["- PR 병합 대기"])


if __name__ == "__main__":
    unittest.main()
