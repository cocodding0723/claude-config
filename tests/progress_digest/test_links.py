"""진행 목록 항목 링크 시험: 정식 주소(chat.getPermalink)만 쓰고, 주제·카드·첨부·링크 없음, 조회 실패, 회차 캐시, 첫 줄 인용, 목록 접근 불가."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts")
sys.path.insert(0, SCRIPTS)
import progress_collect as pc  # noqa: E402
import progress_digest as pd  # noqa: E402

PERMA = "https://example.slack.com/archives/D1/p1700000000000100"


def write_state(d, topics=None, reports=None):
    path = os.path.join(d, "report-sender.state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"topics": topics or {}, "reports": reports or {}, "warned": {}}, f)
    return path


class FakeApi:
    """api_get 대체. 호출 기록과 응답 표를 가짐"""

    def __init__(self, text="허브 분할 승인\n둘째 줄", fail=()):
        self.calls, self.text, self.fail = [], text, fail

    def __call__(self, conf, method, params):
        self.calls.append((method, dict(params)))
        if method in self.fail:
            raise pd.Fail(f"{method} 오류(message_not_found)", 1, "message_not_found")
        if method == "chat.getPermalink":
            return {"ok": True, "permalink": f"https://example.slack.com/archives/{params['channel']}/p{params['message_ts'].replace('.', '')}"}
        if method == "files.info":
            return {"ok": True, "file": {"shares": {"private": {"D1": [{"ts": "1700000000.000200", "thread_ts": "1700000000.000100"}]}}}}
        if method == "conversations.history":
            return {"ok": True, "messages": [{"text": self.text}]}
        if method == "conversations.replies":
            return {"ok": True, "messages": [{"ts": params["oldest"], "text": self.text}]}
        raise AssertionError(method)


def links(api, warns=None):
    with mock.patch.object(pd, "api_get", api):
        return pd.Links(pd.Conf(token="t", api="http://127.0.0.1:9"), warn=(warns.append if warns is not None else None))


class Collect(unittest.TestCase):
    def test_decision_card_ref_and_no_hand_made_link(self):
        items = pc.decision_items({"items": [{"title": "알림 시각", "id": "D-1", "ts": "1700000000.000100", "channel": "D1"}, {"title": "b", "id": "D-2"}]})
        self.assertEqual(items[0]["ref"], {"kind": "card", "channel": "D1", "ts": "1700000000.000100"})
        self.assertEqual([i["link"] for i in items], ["", ""])  # 수집기는 주소를 만들지 않음
        self.assertIsNone(items[1]["ref"])

    def test_topic_and_attachment_refs(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_state(d, topics={"F1": {"ts": "1.1", "channel": "D1"}, "F2": {"ts": "2.2", "channel": "D1"}, "hub:ui": {"ts": "3.3", "channel": "D1"}},
                               reports={"r1": {"task": "F2", "sent": 5, "att_done": {"a.html": "Fold", "p": ""}}, "r2": {"task": "F2", "sent": 9, "att_done": {"b.html": "Fnew"}}})
            refs = pc.ReportRefs(path)
        self.assertEqual(refs.for_task("F1"), {"kind": "topic", "channel": "D1", "ts": "1.1"})  # 주제
        self.assertEqual(refs.for_task("F2"), {"kind": "file", "file": "Fnew"})  # 첨부(가장 최근 보고)
        self.assertEqual(refs.for_hub("ui")["ts"], "3.3")
        self.assertIsNone(refs.for_task("F9"))  # 링크 없음

    def test_unreadable_state_gives_no_refs(self):
        self.assertIsNone(pc.ReportRefs("/없는/상태.json").for_task("F1"))


class Resolve(unittest.TestCase):
    def test_permalink_comes_from_api_for_topic_card_and_file(self):
        api, warns = FakeApi(), []
        lk = links(api, warns)
        with mock.patch.object(pd, "api_get", api):
            items = [pd.Item("ledger:F1", pd.DOING, "F1", ref={"kind": "topic", "channel": "D1", "ts": "1700000000.000100"}),
                     pd.Item("decision:D-1", pd.WAIT, "결정", ref={"kind": "card", "channel": "D1", "ts": "1700000000.000300"}),
                     pd.Item("ledger:F2", pd.DOING, "F2", ref={"kind": "file", "file": "Ffile"})]
            pd.resolve_links(items, lk)
        self.assertEqual([i.link for i in items], [PERMA, "https://example.slack.com/archives/D1/p1700000000000300", "https://example.slack.com/archives/D1/p1700000000000200"])
        self.assertEqual(warns, [])
        self.assertEqual(sum(1 for m, _ in api.calls if m == "chat.getPermalink"), 3)

    def test_same_message_is_looked_up_once_per_run(self):
        api = FakeApi()
        lk = links(api)
        ref = {"kind": "topic", "channel": "D1", "ts": "1700000000.000100"}
        with mock.patch.object(pd, "api_get", api):
            items = [pd.Item(f"ledger:F{n}", pd.WAIT, "x", ref=ref) for n in range(3)]
            pd.resolve_links(items, lk, items)
        self.assertEqual([m for m, _ in api.calls].count("chat.getPermalink"), 1)
        self.assertEqual([m for m, _ in api.calls].count("conversations.history"), 1)

    def test_permalink_failure_means_no_link_not_a_made_up_one(self):
        api, warns = FakeApi(fail=("chat.getPermalink",)), []
        lk = links(api, warns)
        it = pd.Item("ledger:F1", pd.WAIT, "F1 로그인", ref={"kind": "topic", "channel": "D1", "ts": "1700000000.000100"})
        with mock.patch.object(pd, "api_get", api):
            pd.resolve_links([it], lk, [it])
        self.assertEqual(it.link, "")
        self.assertEqual(pd.name_lines([it]), ["- F1 로그인 · 링크 없음"])
        self.assertEqual(len(warns), 1)
        self.assertNotIn("archives", json.dumps(warns))

    def test_decision_without_card_ts_warns_and_shows_no_link(self):
        warns = []
        lk = links(FakeApi(), warns)
        it = pd.Item("decision:D-7", pd.WAIT, "결정 알림 시각", ref=None)
        pd.resolve_links([it], lk, [it])
        self.assertEqual(it.link, "")
        self.assertTrue(any("D-7" in w for w in warns))
        self.assertEqual(pd.name_lines([it]), ["- 결정 알림 시각 · 카드 링크 없음 ⚠️"])  # 눈에 띄게

    def test_pr_item_has_no_link_marker(self):
        it = pd.Item("pr:x/y#1", pd.WAIT, "PR 병합 대기", "https://github.com/x/y/pull/1")
        self.assertEqual(pd.name_lines([it]), ["- PR 병합 대기"])


class Quote(unittest.TestCase):
    def test_dm_line_has_quote_and_link_at_end(self):
        api = FakeApi()
        lk = links(api)
        it = pd.Item("ledger:F1", pd.WAIT, "허브 보고", ref={"kind": "topic", "channel": "D1", "ts": "1700000000.000100"})
        with mock.patch.object(pd, "api_get", api):
            pd.resolve_links([it], lk, [it])
        self.assertEqual(pd.name_lines([it]), [f'- 허브 보고 · "허브 분할 승인" <{PERMA}|원본>'])

    def test_file_message_in_thread_is_read_with_replies(self):
        api = FakeApi()
        lk = links(api)
        it = pd.Item("ledger:F2", pd.WAIT, "F2", ref={"kind": "file", "file": "Ff"})
        with mock.patch.object(pd, "api_get", api):
            pd.resolve_links([it], lk, [it])
        replies = [p for m, p in api.calls if m == "conversations.replies"]
        self.assertEqual(replies[0]["ts"], "1700000000.000100")
        self.assertEqual(it.quote, "허브 분할 승인")

    def test_quote_only_for_dm_visible_items(self):
        api = FakeApi()
        lk = links(api)
        a = pd.Item("ledger:F1", pd.DOING, "a", ref={"kind": "topic", "channel": "D1", "ts": "1.1"})
        with mock.patch.object(pd, "api_get", api):
            pd.resolve_links([a], lk, pd.dm_visible([a]))
        self.assertEqual(a.quote, "")
        self.assertNotIn("conversations.history", [m for m, _ in api.calls])

    def test_quote_length_secret_and_path_rules(self):
        self.assertLessEqual(len(pd.clean_quote("가" * 100)), pd.QUOTE_MAX)
        self.assertEqual(pd.clean_quote("값 " + "xo" + "xb-1234567890-abcdefghij"), "")
        self.assertEqual(pd.clean_quote("보고서 /Users/me/out/report.html"), "")
        self.assertEqual(pd.clean_quote('제목 <a|b> *굵게* "인용"'), "제목 ab 굵게 인용")

    def test_quote_failure_keeps_link(self):
        api = FakeApi(fail=("conversations.history",))
        lk = links(api)
        it = pd.Item("ledger:F1", pd.WAIT, "F1", ref={"kind": "topic", "channel": "D1", "ts": "1700000000.000100"})
        with mock.patch.object(pd, "api_get", api):
            pd.resolve_links([it], lk, [it])
        self.assertEqual(pd.name_lines([it]), [f"- F1 <{PERMA}|원본>"])


class ListAccess(unittest.TestCase):
    def test_list_not_found_stops_list_updates_once(self):
        conf = pd.Conf(token="t", api="http://127.0.0.1:9", list_id="F0X")
        ops = [("create", pd.Item(f"ledger:F{n}", pd.DOING, "x"), "") for n in range(5)]
        calls = []

        def api(c, method, body):
            calls.append(method)
            raise pd.Fail(f"{method} 오류(list_not_found)", 1, "list_not_found")

        state = {"items": {}}
        with mock.patch.object(pd, "api", api):
            errors = pd.apply_list(conf, ops, state)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("목록 갱신 건너뜀", errors[0])

    def test_dm_still_sent_when_list_blocked(self):
        sent = []
        conf = pd.Conf(token="t", api="http://127.0.0.1:9", state=os.path.join(tempfile.mkdtemp(), "s.json"))
        args = pd.parser().parse_args(["--force", "--no-pr"])
        it = pd.Item("ledger:F1", pd.WAIT, "x")
        with mock.patch.object(pd, "load_items", lambda use_pr: ([it], [], {})), \
                mock.patch.object(pd, "api", mock.Mock(side_effect=pd.Fail("slackLists.items.create 오류(list_not_found)", 1, "list_not_found"))), \
                mock.patch.object(pd, "send_dm", lambda c, t: sent.append(t)), \
                mock.patch.object(pd, "api_get", FakeApi()):
            rc = pd.run(conf, args)
        self.assertEqual(rc, 1)
        self.assertEqual(len(sent), 1)


class Recovery(unittest.TestCase):
    """링크 조회가 일시 실패했다 복구돼도 DM 이 다시 가지 않고, 링크·인용은 캐시"""

    def run_once(self, conf, api, dry=False, items=None):
        sent = []
        it = items or [pd.Item("ledger:F1", pd.WAIT, "F1 로그인", ref={"kind": "topic", "channel": "D1", "ts": "1700000000.000100"})]
        updates = []

        def fake_api(c, method, body):
            updates.append((method, body))
            return {"ok": True, "item": {"id": "Rec1"}}

        argv = ["--force"] + (["--dry-run"] if dry else [])
        args = pd.parser().parse_args(argv + ["--no-pr"])
        with mock.patch.object(pd, "load_items", lambda use_pr: (it, [], {})), mock.patch.object(pd, "api", fake_api), \
                mock.patch.object(pd, "send_dm", lambda c, t: sent.append(t)), mock.patch.object(pd, "api_get", api):
            pd.run(conf, args)
        return sent, updates

    def conf(self):
        return pd.Conf(token="t", api="http://127.0.0.1:9", state=os.path.join(tempfile.mkdtemp(), "s.json"))

    def test_hash_ignores_link_and_quote(self):
        a = pd.Item("ledger:F1", pd.WAIT, "F1")
        b = pd.Item("ledger:F1", pd.WAIT, "F1", link=PERMA, quote="인용")
        self.assertEqual(pd.digest_hash([a]), pd.digest_hash([b]))
        self.assertNotEqual(pd.digest_hash([a]), pd.digest_hash([pd.Item("ledger:F1", pd.DOING, "F1")]))

    def test_recovery_does_not_resend_dm_but_fills_list_link(self):
        conf = self.conf()
        sent, ups = self.run_once(conf, FakeApi(fail=("chat.getPermalink",)))
        self.assertEqual(len(sent), 1)
        self.assertIn("링크 없음", sent[0])
        sent2, ups2 = self.run_once(conf, FakeApi())  # 복구된 회차
        self.assertEqual(sent2, [])  # DM 재전송 없음
        self.assertTrue(any(m == "slackLists.items.update" and PERMA in json.dumps(b) for m, b in ups2))  # 목록 행에 링크 조용히 보강

    def test_cache_persists_in_state_and_skips_lookups(self):
        conf = self.conf()
        api1 = FakeApi()
        self.run_once(conf, api1)
        self.assertGreater(len(api1.calls), 0)
        state = pd.load_state(conf.state)
        self.assertTrue(any(k.startswith("link:") for k in state["links"]))
        api2 = FakeApi()
        self.run_once(conf, api2)
        self.assertEqual(api2.calls, [])  # 다음 회차는 캐시로 해결(전수 재조회 없음)

    def test_failure_is_not_cached_and_expired_entries_are_dropped(self):
        cache = {}
        lk = pd.Links(pd.Conf(token="t", api="http://127.0.0.1:9"), cache, now=1000.0, warn=lambda m: None)
        with mock.patch.object(pd, "api_get", FakeApi(fail=("chat.getPermalink",))):
            self.assertEqual(lk.link({"kind": "topic", "channel": "D1", "ts": "1.1"}), "")
        self.assertEqual(cache, {})
        lk2 = pd.Links(pd.Conf(token="t", api="http://127.0.0.1:9"), {"link:D1:1.1": {"v": "u", "at": 1000.0}}, now=1000.0 + pd.LINK_TTL_S + 1)
        lk2.prune()
        self.assertEqual(lk2.cache, {})

    def test_dry_run_makes_no_slack_calls(self):
        conf = self.conf()
        api = FakeApi()
        sent, ups = self.run_once(conf, api, dry=True)
        self.assertEqual((api.calls, sent, ups), ([], [], []))


class ListRows(unittest.TestCase):
    """Slack 목록 행: 쉬운 제목, 눌리는 링크 칸, 결정 대기 → 막힘 → 진행 순서(오래된 것 먼저), 답이 오면 완료 체크"""

    def test_titles_have_no_internal_ids_and_hub_name_first(self):
        self.assertEqual(pd.Item("ledger:F11", pd.DOING, "F11 진행 목록 링크 추가").list_title(), "진행 목록 링크 추가")
        self.assertEqual(pd.Item("hub:ui:t:ab12cd34", pd.DOING, "ui: 버튼 정리").list_title(), "ui · 버튼 정리")
        self.assertEqual(pd.Item("hub:ui:t:ab12cd34", pd.DOING, "[낡음] ui: 버튼 정리").list_title(), "[낡음] ui · 버튼 정리")
        self.assertEqual(pd.Item("decision:D-1", pd.WAIT, "결정 알림 시각").list_title(), "결정 · 알림 시각")
        self.assertNotIn("ab12cd34", pd.Item("hub:ui:t:ab12cd34", pd.DOING, "ui: x").line())

    def test_link_cell_is_rich_text_link_element(self):
        cell = pd.rich_link(PERMA, "결정 카드 열기")
        el = cell[0]["elements"][0]["elements"][0]
        self.assertEqual(el, {"type": "link", "url": PERMA, "text": "결정 카드 열기"})
        self.assertEqual(pd.link_label(pd.Item("decision:D-1", pd.WAIT, "x")), "결정 카드 열기")
        self.assertEqual(pd.link_label(pd.Item("ledger:F1", pd.DOING, "x")), "메시지 열기")

    def test_create_sends_link_as_link_element(self):
        calls = []
        conf = pd.Conf(token="t", api="http://127.0.0.1:9", list_id="L1")
        it = pd.Item("decision:D-1", pd.WAIT, "결정 알림", link=PERMA)
        with mock.patch.object(pd, "api", lambda c, m, b: calls.append((m, b)) or {"ok": True, "item": {"id": "R1"}}):
            pd.apply_list(conf, [("create", it, "")], {"items": {}})
        fields = {f["column_id"]: f for f in calls[0][1]["initial_fields"]}
        self.assertEqual(fields[pd.COL_LINK]["rich_text"][0]["elements"][0]["elements"][0]["type"], "link")
        self.assertEqual(fields[pd.COL_TITLE]["rich_text"][0]["elements"][0]["elements"][0]["text"], "⏳ 결정 · 알림")

    def test_new_rows_ordered_wait_block_doing_oldest_first(self):
        mk = lambda k, b, since: pd.Item(k, b, k, since=since)  # noqa: E731
        items = [mk("ledger:a", pd.DOING, 1), mk("decision:new", pd.WAIT, 50), mk("ledger:blk", pd.BLOCK, 5), mk("decision:old", pd.WAIT, 10), mk("ledger:b", pd.DOING, 0)]
        ops = pd.plan_list(items, {"items": {}})
        self.assertEqual([o[1].key for o in ops], ["decision:old", "decision:new", "ledger:blk", "ledger:a", "ledger:b"])  # since 모르는 것(0)은 같은 종류 맨 뒤

    def test_answered_decision_is_checked_done(self):
        state = {"items": {"decision:D-1": {"record": "Rec9", "title": "결정 x", "bucket": pd.WAIT, "link": PERMA}}}
        ops = pd.plan_list([], state)  # 답이 와 결정 대기에서 빠짐
        self.assertEqual([(o[0], o[2]) for o in ops], [("done", "Rec9")])
        calls = []
        with mock.patch.object(pd, "api", lambda c, m, b: calls.append(b) or {"ok": True}):
            pd.apply_list(pd.Conf(token="t", api="http://127.0.0.1:9", list_id="L1"), ops, state)
        self.assertTrue(calls[0]["cells"][0]["checkbox"])

    def test_collector_sets_since(self):
        d = pc.decision_items({"items": [{"title": "a", "id": "D-1", "ts": "1700000000.000100", "channel": "D1"}]})
        self.assertEqual(d[0]["since"], 1700000000.0001)
        self.assertEqual(pc.received_epoch("garbage"), 0.0)
        self.assertLess(pc.received_epoch("11-01", now=1794000000), pc.received_epoch("11-05", now=1794000000))


class ListChange(unittest.TestCase):
    def test_defaults_point_to_new_list(self):
        self.assertEqual(pd.DEFAULT_LIST_ID, "F0CH72MJKBN")
        self.assertTrue(pd.DEFAULT_LIST_URL.endswith("/F0CH72MJKBN"))
        self.assertEqual((pd.COL_TITLE, pd.COL_DONE, pd.COL_LINK), ("Col0CHXNEMP1N", "Col0CH58810LR", "Col0CGN0B5WT1"))

    def test_other_list_id_resets_row_mapping(self):
        st = {"hash": "h", "dm_hash": "d", "items": {"ledger:F1": {"record": "Rec1", "title": "t", "bucket": "doing"}}, "list_id": "FOLD"}
        self.assertTrue(pd.sync_list_id(st, "FNEW"))
        self.assertEqual((st["items"], st["hash"], st["list_id"], st["dm_hash"]), ({}, "", "FNEW", "d"))

    def test_legacy_state_without_list_id_resets(self):
        st = {"hash": "h", "dm_hash": "", "items": {"ledger:F1": {"record": "Rec1"}}}
        self.assertTrue(pd.sync_list_id(st, "FNEW"))
        self.assertEqual(st["items"], {})

    def test_same_list_keeps_rows_and_empty_state_just_records_id(self):
        st = {"hash": "h", "dm_hash": "", "items": {"ledger:F1": {"record": "Rec1"}}, "list_id": "FNEW"}
        self.assertFalse(pd.sync_list_id(st, "FNEW"))
        self.assertIn("ledger:F1", st["items"])
        empty = {"hash": "", "dm_hash": "", "items": {}}
        self.assertFalse(pd.sync_list_id(empty, "FNEW"))
        self.assertEqual(empty["list_id"], "FNEW")


if __name__ == "__main__":
    unittest.main()
