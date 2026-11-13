import json
import os
import re
import unittest

from common import ROOT, SVG3, Env, page

SUMMARY = '<div data-check="summary"><b>3줄 요약</b><br>① 문제 하나.<br>② 조치 둘.<br>③ 결과 셋.<br>④ 넷째는 버림.</div>'


class SendTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        # 가짜 알림 스크립트: 인자를 기록하고 FAKE_RC 로 종료. 실제 Slack 호출 없음
        self.notify = os.path.join(self.env.hub, "fake_notify.sh")
        with open(self.notify, "w") as f:
            f.write('#!/bin/bash\nprintf "%s\\n" "$@" > "$LRM_HUB_DIR/notify-args"\n[ "${FAKE_RC:-0}" = 0 ] || { echo "Slack 오류 channel_not_found" >&2; exit "$FAKE_RC"; }\necho ok\n')
        os.environ["LRM_DOC_NOTIFY"] = self.notify
        self.rec = os.path.join(self.env.hub, "doc-recipients.json")
        with open(self.rec, "w", encoding="utf-8") as f:
            json.dump({"recipients": [{"name": "시험", "slack_id": "UREC0001"}, {"name": "미설정", "slack_id": ""}]}, f)
        self.doc = self.env.write(self.env.docs, "a.html", page("문서", SUMMARY + SVG3))
        import doc_send
        self.ds = doc_send

    def tearDown(self):
        self.env.close()

    def args(self):
        with open(os.path.join(self.env.hub, "notify-args"), encoding="utf-8") as f:
            return f.read().splitlines()

    def test_send_calls_notify_with_recipient_and_file_then_logs(self):
        r = self.ds.send(self.doc, "문서", "시험", "안녕하세요")
        a = self.args()
        self.assertEqual(a[:2], ["--to-user", "UREC0001"])
        self.assertEqual(a[-3:], ["--", "안녕하세요", self.doc])
        self.assertEqual(r["to"], "시험")
        log = [json.loads(x) for x in open(self.ds.SENT_LOG, encoding="utf-8")]
        self.assertEqual((log[0]["to"], log[0]["path"], log[0]["message"]), ("시험", self.doc, "안녕하세요"))
        self.assertEqual(self.ds.sent_by_path()[self.doc][0]["to"], "시험")

    def test_failed_send_leaves_no_record_and_allows_retry(self):
        os.environ["FAKE_RC"] = "1"
        with self.assertRaises(self.ds.SendError) as c:
            self.ds.send(self.doc, "문서", "시험", "글")
        self.assertIn("channel_not_found", str(c.exception))
        self.assertFalse(os.path.exists(self.ds.SENT_LOG))
        os.environ["FAKE_RC"] = "0"
        self.ds.send(self.doc, "문서", "시험", "글")  # 실패는 중복 잠금에 남지 않음

    def test_duplicate_within_window_rejected(self):
        self.ds.send(self.doc, "문서", "시험", "글")
        with self.assertRaises(self.ds.SendError):
            self.ds.send(self.doc, "문서", "시험", "글")
        self.ds.send(self.doc, "문서", "시험", "다른 글")

    def test_secret_file_rejected_before_notify(self):
        env = self.env.write(self.env.docs, ".env", "KEY=1")
        with self.assertRaises(self.ds.SendError) as c:
            self.ds.send(env, "비밀", "시험", "글")
        self.assertIn("비밀", str(c.exception))
        self.assertFalse(os.path.exists(os.path.join(self.env.hub, "notify-args")))

    def test_unknown_or_unready_recipient_rejected(self):
        for who in ("없는사람", "미설정"):
            with self.assertRaises(self.ds.SendError):
                self.ds.send(self.doc, "문서", who, "글")
        self.assertFalse(os.path.exists(os.path.join(self.env.hub, "notify-args")))

    def test_empty_and_oversize_message_rejected(self):
        for m in ("  ", "가" * (self.ds.MSG_MAX + 1)):
            with self.assertRaises(self.ds.SendError):
                self.ds.send(self.doc, "문서", "시험", m)

    def test_dash_message_cannot_become_option(self):
        self.ds.send(self.doc, "문서", "시험", "--to-user UEVIL000 안녕")
        self.assertIn("UREC0001", self.args())
        self.assertEqual(self.args().index("--"), len(self.args()) - 3)

    def test_public_recipients_hide_slack_id(self):
        pub = json.dumps(self.ds.public_recipients(), ensure_ascii=False)
        self.assertNotIn("UREC0001", pub)
        self.assertEqual([r["ready"] for r in self.ds.public_recipients()], [True, False])

    def test_draft_takes_title_and_three_summary_lines(self):
        d = self.ds.draft(self.doc, "문서")
        self.assertEqual(d["message"], "문서\n\n① 문제 하나.\n② 조치 둘.\n③ 결과 셋.")

    def test_draft_without_summary_asks_user_to_write(self):
        p = self.env.write(self.env.docs, "b.html", page("요약 없음"))
        self.assertIn("직접 적어", self.ds.draft(p, "요약 없음")["message"])

    def test_missing_config_gives_empty_recipients(self):
        os.remove(self.rec)
        self.assertEqual(self.ds.load_recipients(), [])

    def test_only_browser_module_imports_doc_send_and_no_cli(self):
        base = os.path.join(ROOT, "hubs", "scripts")
        users = []
        for n in os.listdir(base):
            if n.endswith(".py") and n != "doc_send.py":
                if re.search(r"^\s*(import doc_send|from doc_send)", open(os.path.join(base, n), encoding="utf-8").read(), re.M):
                    users.append(n)
        self.assertEqual(users, [] if not os.path.exists(os.path.join(base, "doc_browser.py")) else ["doc_browser.py"])
        self.assertNotIn("__main__", open(os.path.join(base, "doc_send.py"), encoding="utf-8").read())
        for n in os.listdir(base):  # 다른 스크립트가 보내기 로그·받는 사람 설정으로 직접 전송하지 않음
            if n.endswith((".sh", ".py")) and n not in ("slack_notify.sh", "doc_send.py"):
                self.assertNotIn("--to-user", open(os.path.join(base, n), encoding="utf-8").read(), n)


if __name__ == "__main__":
    unittest.main()
