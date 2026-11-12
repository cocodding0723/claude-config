"""ledger_sync.py 시험. Slack 은 가짜 API(Stub)로 대체해 실제 호출·토큰 없이 확인함. 임시 폴더만 씀."""
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "hubs", "scripts"))
import ledger  # noqa: E402
import ledger_sync as ls  # noqa: E402

HEAD = "id\t접수\t요청(원문 요약)\t상태\t담당\t진행·증거\t갱신\n"
T0 = 1793000000.0  # 고정 시각
TODAY = ls.time.strftime("%Y-%m-%d", ls.time.localtime(T0))


def row(rid, state, req="요청", note="", stamp=None):
    return f"{rid}\t11-12\t{req}\t{state}\t개인설정\t{note}\t{stamp or TODAY + ' 10:00'}\n"


class Stub:
    """chat.* 호출 기록. script 에 예외를 넣으면 해당 순서의 호출에서 던짐"""

    def __init__(self):
        self.calls, self.script = [], []
        self.parent = ""  # find_parent 가 돌려줄 기존 부모 ts

    def _hit(self, name, **kw):
        self.calls.append((name, kw))
        if self.script:
            err = self.script.pop(0)
            if err:
                raise err

    def open_dm(self, user):
        self._hit("open", user=user)
        return "D123"

    def find_parent(self, channel):
        self.calls.append(("find", {"channel": channel}))
        return self.parent

    def post(self, channel, text, thread_ts=""):
        if not thread_ts:
            self.parent = "1700000000.000100"  # Slack 은 응답이 유실돼도 이미 받은 메시지를 기억함
            self.parents_made = getattr(self, "parents_made", 0) + 1
        self._hit("post", channel=channel, text=text, thread_ts=thread_ts)
        return "1700000000.000100" if not thread_ts else "1700000000.000200"

    def update(self, channel, ts, text):
        self._hit("update", channel=channel, ts=ts, text=text)

    def names(self):
        return [n for n, _ in self.calls]


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ledger = os.path.join(self.dir.name, "l.tsv")
        self.state = os.path.join(self.dir.name, "state.json")
        self.conf = ls.Conf(ledger=self.ledger, state=self.state, user_id="U1")
        self.sleeps, self.clock = [], [T0]
        self.write(row("F1", "진행", "첫째", "분석 중") + row("F2", "접수", "둘째") + row("F3", "진행", "셋째"))

    def tearDown(self):
        self.dir.cleanup()

    def write(self, body):
        with open(self.ledger, "w", encoding="utf-8") as f:
            f.write(HEAD + body)

    def run_sync(self, api, **kw):
        return ls.sync(self.conf, api, now=lambda: self.clock[0], sleep=self.sleeps.append, **kw)

    def st(self):
        return ls.load_state(self.state)

    def test_creates_parent_when_missing_and_saves_ts_with_mode_600(self):
        api = Stub()
        res = self.run_sync(api)
        self.assertEqual((res["status"], api.names()), ("synced", ["open", "find", "post"]))
        self.assertEqual(api.calls[0][1]["user"], "U1")
        self.assertEqual(api.calls[2][1]["thread_ts"], "")  # 부모는 스레드 답글이 아님
        self.assertEqual(self.st()["ts"], "1700000000.000100")
        self.assertEqual(self.st()["channel"], "D123")
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o600)
        self.assertEqual(res["replies"], 0)  # 첫 생성은 답글 없음

    def test_same_hash_makes_zero_calls(self):
        self.run_sync(Stub())
        api = Stub()
        res = self.run_sync(api)
        self.assertEqual((res["status"], res["calls"], api.calls), ("unchanged", 0, []))

    def test_only_changed_items_get_replies_and_parent_updated(self):
        self.run_sync(Stub())
        self.write(row("F1", "완료", "첫째", "분석 중 · 증거: abc1234")
                   + row("F2", "접수", "둘째") + row("F3", "진행", "셋째"))
        api = Stub()
        res = self.run_sync(api)
        self.assertEqual(api.names(), ["update", "post"])  # 채널은 상태 파일에서 재사용
        self.assertEqual(api.calls[0][1]["ts"], "1700000000.000100")
        reply = api.calls[1][1]
        self.assertEqual(reply["thread_ts"], "1700000000.000100")
        self.assertEqual(reply["text"], "✅ 첫째 — 완료")
        self.assertEqual(res["replies"], 1)

    def test_new_item_reply_and_summary_shape(self):
        self.run_sync(Stub())
        self.write(row("F1", "진행", "첫째", "분석 중") + row("F2", "접수", "둘째") + row("F3", "진행", "셋째") + row("F4", "접수", "넷째"))
        api = Stub()
        self.run_sync(api)
        self.assertEqual(api.calls[1][1]["text"], "📥 새로 접수 · 넷째")
        parent = api.calls[0][1]["text"]
        self.assertIn("📥 접수 2 · 🔄 진행 2 · ✅ 완료 0 · ⏸️ 보류 0", parent)
        self.assertIn("🔄 첫째 (개인설정)", parent)

    def test_summary_shows_only_todays_done(self):
        self.write(row("F1", "완료", "오늘 끝", "x · 증거: e1", TODAY + " 09:00") + row("F2", "완료", "어제 끝", "x · 증거: e2", "2026-01-01 09:00")
                   + row("F3", "진행", "진행 중"))
        text = ls.summary(ledger.read_rows(self.ledger), TODAY)
        self.assertIn("오늘 끝", text)
        self.assertNotIn("어제 끝", text)
        self.assertIn("✅ 완료 2", text)  # 개수에는 포함

    def test_many_changes_bundled_into_at_most_n_replies(self):
        self.run_sync(Stub())
        self.write("".join(row(f"F{i}", "진행", f"항목{i}") for i in range(1, 10)))
        api = Stub()
        res = self.run_sync(api)
        posts = [c for n, c in api.calls if n == "post"]
        self.assertEqual(len(posts), self.conf.max_replies)
        self.assertEqual(res["replies"], self.conf.max_replies)
        # 바뀐 9건(F1 요청·설명 변경, F2 접수→진행, F3 요청 변경, F4~F9 신규) = 단독 4개 + 마지막 묶음 5줄
        self.assertEqual(posts[-1]["text"].count("\n"), 4)
        self.assertEqual(len(self.st()["snapshot"]), 9)

    def test_retry_after_honored_on_429_then_succeeds(self):
        api = Stub()
        api.script = [None, ls.RateLimited(7), None]  # open ok, post 429, post ok
        res = self.run_sync(api)
        self.assertEqual(res["status"], "synced")
        self.assertEqual(self.sleeps, [7])
        self.assertEqual(api.names(), ["open", "find", "post", "post"])
        self.assertEqual(self.st()["ts"], "1700000000.000100")

    def test_long_retry_after_defers_and_skips_until_time_passes(self):
        api = Stub()
        api.script = [ls.RateLimited(600)]
        res = self.run_sync(api)
        self.assertEqual((res["status"], self.sleeps), ("deferred", []))  # 오래 안 기다림
        self.assertEqual(self.st()["not_before"], T0 + 600)
        quiet = Stub()
        self.assertEqual(self.run_sync(quiet)["status"], "deferred")
        self.assertEqual(quiet.calls, [])
        self.clock[0] = T0 + 601
        ok = Stub()
        self.assertEqual(self.run_sync(ok)["status"], "synced")
        self.assertNotIn("not_before", self.st())

    def test_retries_exhausted_defers(self):
        api = Stub()
        api.script = [None] + [ls.RateLimited(1)] * 10
        res = self.run_sync(api)
        self.assertEqual(res["status"], "deferred")
        self.assertEqual(len(self.sleeps), self.conf.retries)

    def test_api_error_keeps_state_so_next_round_retries(self):
        self.run_sync(Stub())
        before = self.st()
        self.write(row("F1", "보류", "첫째", "대기") + row("F2", "접수", "둘째") + row("F3", "진행", "셋째"))
        bad = Stub()
        bad.script = [ls.ApiError("internal_error")]
        res = self.run_sync(bad)
        self.assertEqual((res["status"], res["error"]), ("failed", "internal_error"))
        self.assertEqual(self.st()["hash"], before["hash"])
        good = Stub()
        self.assertEqual(self.run_sync(good)["status"], "synced")
        self.assertIn("⏸️ 첫째 — 보류 · 대기", good.calls[1][1]["text"])

    def test_partial_reply_failure_does_not_repeat_sent_replies(self):
        self.run_sync(Stub())
        self.write(row("F1", "완료", "첫째", "x · 증거: e1") + row("F2", "진행", "둘째") + row("F3", "진행", "셋째"))
        api = Stub()
        api.script = [None, None, ls.ApiError("fatal")]  # update ok, 첫 답글 ok, 둘째 답글 실패
        res = self.run_sync(api)
        self.assertEqual((res["status"], res["replies"]), ("failed", 1))
        retry = Stub()
        self.run_sync(retry)
        texts = [c["text"] for n, c in retry.calls if n == "post"]
        self.assertEqual(texts, ["🔄 둘째 — 작업 시작"])  # 첫 답글(F1)은 다시 안 보냄

    def test_deleted_parent_is_recreated(self):
        self.run_sync(Stub())
        self.write(row("F1", "진행", "첫째") + row("F2", "접수", "둘째") + row("F3", "진행", "셋째") + row("F4", "접수", "넷째"))
        api = Stub()
        api.script = [ls.ApiError("message_not_found")]
        res = self.run_sync(api)
        self.assertEqual((res["status"], api.names()), ("synced", ["update", "post"]))
        self.assertEqual(api.calls[1][1]["thread_ts"], "")

    def test_day_change_refreshes_parent_without_replies(self):
        self.run_sync(Stub())
        self.clock[0] = T0 + 86400
        api = Stub()
        res = self.run_sync(api)
        self.assertEqual((api.names(), res["replies"]), (["update"], 0))

    def test_dry_run_prints_payload_without_writing_state(self):
        api = ls.DryApi()
        res = self.run_sync(api, dry=True)
        self.assertEqual(res["status"], "synced")
        self.assertEqual(api.sent[0]["api"], "chat.postMessage")
        self.assertFalse(os.path.exists(self.state))

    def test_missing_ledger_is_empty_not_crash(self):
        os.unlink(self.ledger)
        api = Stub()
        self.assertEqual(self.run_sync(api)["status"], "synced")
        self.assertIn("처리할 항목 없음", api.calls[2][1]["text"])

    # ---- 리뷰 재현: 부모 중복(심각2·3) --------------------------------------
    def parents(self, api):
        return getattr(api, "parents_made", 0)

    def test_state_save_failure_after_parent_post_does_not_make_second_parent(self):
        api, real, n = Stub(), ls.save_state, [0]

        def flaky(path, st):
            n[0] += 1
            if n[0] == 2:  # 1번째는 게시 전 채널 저장, 2번째는 부모 ts 저장
                raise OSError("disk full")
            real(path, st)

        with mock.patch.object(ls, "save_state", flaky):
            res = self.run_sync(api)
        self.assertEqual((res["status"], res["error"], self.parents(api)), ("failed", "state_write_failed", 1))
        self.assertNotIn("ts", self.st())
        again = self.run_sync(api)  # 재시도: 이력에서 기존 부모를 찾아 이어받음
        self.assertEqual((again["status"], again["replies"], self.parents(api)), ("synced", 0, 1))
        self.assertEqual(self.st()["ts"], "1700000000.000100")
        self.assertEqual(api.names().count("update"), 1)

    def test_unwritable_state_stops_before_any_parent_is_posted(self):
        api = Stub()
        with mock.patch.object(ls, "save_state", side_effect=OSError("read-only")):
            res = self.run_sync(api)
        self.assertEqual((res["status"], self.parents(api), "post" in api.names()), ("failed", 0, False))

    def test_lost_or_corrupt_state_adopts_existing_parent_without_reply_flood(self):
        api = Stub()
        self.run_sync(api)
        with open(self.state, "w", encoding="utf-8") as f:
            f.write("{깨진 json")
        res = self.run_sync(api)
        self.assertEqual((res["status"], res["replies"], self.parents(api)), ("synced", 0, 1))
        self.assertEqual(sorted(self.st()["snapshot"]), ["F1", "F2", "F3"])
        os.unlink(self.state)
        self.assertEqual((self.run_sync(api)["replies"], self.parents(api)), (0, 1))

    def test_parent_post_with_unknown_result_is_found_next_round(self):
        api = Stub()
        api.script = [None, ls.ApiError("ReadTimeout")]  # open ok, 부모 post 는 Slack 이 받은 뒤 응답 유실
        res = self.run_sync(api)
        self.assertEqual((res["status"], res["error"], self.parents(api)), ("failed", "ReadTimeout", 1))
        self.assertEqual(self.run_sync(api)["status"], "synced")
        self.assertEqual(self.parents(api), 1)

    def test_find_parent_scans_history_pages_for_bot_parent_only(self):
        pages = [
            {"messages": [{"ts": "3", "text": "*피드백 처리 현황* 사람이 쓴 글"}, {"ts": "2", "bot_id": "B1", "text": "*피드백 처리 현황*", "thread_ts": "1"}],
             "response_metadata": {"next_cursor": "c2"}},
            {"messages": [{"ts": "1", "bot_id": "B1", "text": "*피드백 처리 현황* · 마지막 갱신", "thread_ts": "1"}]},
        ]
        web = mock.Mock()
        web.conversations_history.side_effect = [mock.Mock(data=p) for p in pages]
        self.assertEqual(ls.SlackApi(web).find_parent("D1"), "1")
        self.assertEqual(web.conversations_history.call_args_list[1].kwargs["cursor"], "c2")

    def test_history_deeper_than_scan_pages_defers_instead_of_making_second_parent(self):
        """재리뷰 재현: 부모가 SCAN_PAGES 보다 깊으면 빈 결과로 보고 부모를 또 만들던 결함"""
        pages = [{"messages": [{"ts": str(i), "text": "다른 알림"}], "response_metadata": {"next_cursor": f"c{i}"}}
                 for i in range(ls.SCAN_PAGES)]
        web = mock.Mock()
        web.conversations_history.side_effect = [mock.Mock(data=p) for p in pages]
        with self.assertRaises(ls.ApiError) as cm:
            ls.SlackApi(web).find_parent("D1")
        self.assertEqual(cm.exception.code, "parent_search_incomplete")

        api = Stub()
        api.find_parent = mock.Mock(side_effect=ls.ApiError("parent_search_incomplete"))
        res = self.run_sync(api)
        self.assertEqual((res["status"], res["error"], "post" in api.names()), ("failed", "parent_search_incomplete", False))

    def test_history_fully_scanned_without_parent_returns_empty(self):
        web = mock.Mock()
        web.conversations_history.return_value = mock.Mock(data={"messages": [{"ts": "1", "text": "다른 알림"}]})
        self.assertEqual(ls.SlackApi(web).find_parent("D1"), "")

    # ---- 리뷰 재현: 프로세스 간 동시 실행(심각3) -----------------------------
    def test_second_sync_while_first_is_posting_is_busy_and_makes_one_parent(self):
        api, inside, release = Stub(), threading.Event(), threading.Event()
        real_post = api.post

        def slow_post(*a, **k):
            inside.set()
            release.wait(5)
            return real_post(*a, **k)

        api.post = slow_post
        first = []
        t = threading.Thread(target=lambda: first.append(self.run_sync(api)))
        t.start()
        self.assertTrue(inside.wait(5))
        other = Stub()
        self.assertEqual(self.run_sync(other), {"status": "busy", "calls": 0, "replies": 0})
        self.assertEqual(other.calls, [])
        release.set()
        t.join(5)
        self.assertEqual((first[0]["status"], self.parents(api), self.parents(other)), ("synced", 1, 0))

    def test_lock_held_by_another_process_makes_sync_busy(self):
        code = ("import fcntl,sys,time;f=open(sys.argv[1]+'.lock','a');fcntl.flock(f,fcntl.LOCK_EX);"
                "print('ready',flush=True);time.sleep(30)")
        proc = subprocess.Popen([sys.executable, "-I", "-c", code, self.state], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            api = Stub()
            self.assertEqual(self.run_sync(api)["status"], "busy")
            self.assertEqual(api.calls, [])
        finally:
            proc.kill()
            proc.wait()
        self.assertEqual(self.run_sync(Stub())["status"], "synced")  # 쥔 프로세스가 죽으면 잠금 풀림

    def test_dry_run_ignores_lock_and_never_writes_lock_file(self):
        res = self.run_sync(ls.DryApi(), dry=True)
        self.assertEqual(res["status"], "synced")
        self.assertFalse(os.path.exists(self.state + ".lock"))

    # ---- 리뷰 재현: 증거 없는 완료(중간2) ------------------------------------
    def test_hand_edited_done_without_evidence_is_rejected_before_any_slack_call(self):
        self.run_sync(Stub())
        before = self.st()
        self.write(row("F1", "완료", "첫째", "분석 중") + row("F2", "접수", "둘째") + row("F3", "진행", "셋째"))
        api = Stub()
        with self.assertRaises(ledger.LedgerError) as cm:
            self.run_sync(api)
        self.assertIn("F1: 증거 없는 완료", str(cm.exception))
        self.assertEqual((api.calls, self.st()), ([], before))
        self.write(row("F1", "완료", "첫째", "분석 중 · 증거: https://example.com/e") + row("F2", "접수", "둘째") + row("F3", "진행", "셋째"))
        self.assertEqual(self.run_sync(Stub())["status"], "synced")  # 증거를 채우면 재개

    def test_dry_run_also_rejects_done_without_evidence(self):
        self.write(row("F1", "완료", "첫째"))
        with self.assertRaises(ledger.LedgerError):
            self.run_sync(ls.DryApi(), dry=True)

    # ---- 리뷰 재현: 요청·담당만 바뀐 항목(중간3) ------------------------------
    def test_request_or_owner_change_alone_gets_a_reply(self):
        self.run_sync(Stub())
        self.write(row("F1", "진행", "첫째 수정", "분석 중") + row("F2", "접수", "둘째").replace("개인설정", "UI팀") + row("F3", "진행", "셋째"))
        api = Stub()
        res = self.run_sync(api)
        texts = [c["text"] for n, c in api.calls if n == "post"]
        self.assertEqual(res["replies"], 2)
        self.assertEqual(texts, ["🔄 첫째 수정 — 요청 내용 변경", "📥 둘째 — 담당 변경 (UI팀)"])

    def test_old_state_snapshot_without_request_owner_is_not_treated_as_change(self):
        self.run_sync(Stub())
        st = self.st()
        for v in st["snapshot"].values():
            del v["request"], v["owner"]
        ls.save_state(self.state, st)
        self.write(row("F1", "진행", "첫째", "분석 중") + row("F2", "접수", "둘째") + row("F3", "진행", "셋째") + row("F4", "접수", "넷째"))
        api = Stub()
        self.run_sync(api)
        self.assertEqual([c["text"] for n, c in api.calls if n == "post"], ["📥 새로 접수 · 넷째"])

    def test_state_and_owner_change_in_one_line(self):
        self.run_sync(Stub())
        self.write(row("F1", "진행", "첫째", "분석 중") + row("F2", "진행", "둘째").replace("개인설정", "UI팀") + row("F3", "진행", "셋째"))
        api = Stub()
        self.run_sync(api)
        self.assertEqual([c["text"] for n, c in api.calls if n == "post"], ["🔄 둘째 — 작업 시작 · 담당 변경 (UI팀)"])

    # ---- 리뷰 재현: 사용자 본문의 내부 표기(낮음) ------------------------------
    def test_slack_text_hides_internal_ids_prefixes_branches_hashes_and_arrows(self):
        self.write(row("F1", "진행", "머리말 라우팅(ui:/vfx:/qa:/data:/config:)", "fix/decide-card-answers 3f2a9c1 반영")
                   + row("P8", "접수", "P8 Orca 재시작 내성 (결정 D-HUD1)")
                   + row("F3", "진행", "메시지 카드", "분석"))
        self.run_sync(Stub())
        self.write(row("F1", "완료", "머리말 라우팅(ui:/vfx:/qa:/data:/config:)", "fix/decide-card-answers 3f2a9c1 · 증거: fix/decide-card-answers 3f2a9c1")
                   + row("P8", "진행", "P8 Orca 재시작 내성 (결정 D-HUD1)", "feat/main-terminal-rediscover 로 이어감")
                   + row("F3", "진행", "메시지 카드", "분석"))
        api = Stub()
        self.run_sync(api)
        text = "\n".join(c["text"] for n, c in api.calls if n in ("update", "post"))
        for bad in ("F1", "P8", "F3", "D-HUD1", "ui:", "vfx:", "feat/", "fix/", "3f2a9c1", "→", "증거:"):
            self.assertNotIn(bad, text)
        self.assertIn("✅ 머리말 라우팅 — 완료", text)
        self.assertIn("🔄 Orca 재시작 내성 (결정) — 작업 시작 · 로 이어감", text)

    def test_url_evidence_becomes_a_readable_link_in_reply_and_parent(self):
        self.run_sync(Stub())
        self.write(row("F1", "완료", "첫째", "x · 증거: https://example.com/e/1") + row("F2", "접수", "둘째") + row("F3", "진행", "셋째"))
        api = Stub()
        self.run_sync(api)
        self.assertIn("✅ 첫째 — 완료 · <https://example.com/e/1|증거 보기>", api.calls[1][1]["text"])
        self.assertIn("✅ 첫째 · <https://example.com/e/1|증거 보기>", api.calls[0][1]["text"])


if __name__ == "__main__":
    unittest.main()
