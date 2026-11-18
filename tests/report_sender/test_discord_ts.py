"""CHAT=discord 메시지 ID(snowflake)를 Slack ts 와 같이 받는지. 못 받으면 첫 전송 뒤 상태 파일이 손상 판정돼 전송이 멈춤"""
import json
import os
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import hub_report  # noqa: E402
import report_sender  # noqa: E402

SNOW = "1572616836078510262"


class DiscordTsTest(unittest.TestCase):
    def test_ts_accepts_snowflake_and_slack(self):
        self.assertTrue(report_sender.is_ts(SNOW))
        self.assertTrue(report_sender.is_ts("1700000000.000001"))
        self.assertFalse(report_sender.is_ts("123"))
        self.assertTrue(hub_report.TS_RE.match(SNOW))

    def test_state_with_discord_topic_loads(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            st = {"topics": {"card:x": {"ts": SNOW, "channel": "1", "card": "x"}}, "reports": {}, "warned": {}}
            json.dump(st, open(p, "w"))
            self.assertEqual(report_sender.load_state(p)["topics"]["card:x"]["ts"], SNOW)


if __name__ == "__main__":
    unittest.main()
