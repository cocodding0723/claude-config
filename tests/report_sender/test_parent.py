"""카드 주제 부모 메시지(양식 ①~⑥) 시험: 양식 생성·칸 빔 거부·열린 스레드 고쳐 쓰기(바뀐 때만)·기존 작업 번호 주제 유지. 가짜 전송·가짜 bd 만 씀."""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hubs", "scripts"))
import hub_report  # noqa: E402
import report_sender  # noqa: E402

FAKE = os.path.join(HERE, "fake_notify.py")
BD = os.path.join(HERE, "fake_bd.py")
PARENT = "1699999999.000001"
FULL = "쉬운 이름: 구조 개선: Slack 봇·작업판 정상화\n무엇: Slack 봇과 작업판이 제대로 이어지게 고친다.\n왜: 사용자 요청(11-17) 바로바로 소통"


class ParentTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.p = lambda n: os.path.join(self.dir.name, n)
        p = self.p
        self.conf = report_sender.Conf(outbox=p("box.jsonl"), state=p("state.json"), notify=FAKE, ledger=p("ledger.tsv"), sync_state=p("sync.json"),
                                       sent=p("sent.jsonl"), log=p("log.txt"), backoff_s=0, bd=BD, board=p("board"))
        os.environ["FAKE_DIR"] = self.dir.name
        self.addCleanup(os.environ.pop, "FAKE_DIR", None)
        with open(p("sync.json"), "w") as f:
            json.dump({"ts": PARENT, "channel": "D1"}, f)
        with open(p("ledger.tsv"), "w", encoding="utf-8") as f:
            f.write("id\t접수\t요청(원문 요약)\t상태\t담당\t진행·증거\t갱신\nC0012\t11-12\t알림 시각 통일\t진행\tui\t\t2026-11-12 09:00\n")
        self.card(FULL, ["closed", "closed", "open"])

    def card(self, desc, kids, title="구조 개선: 기능 팀 구조 전환"):
        with open(self.p("cards.json"), "w", encoding="utf-8") as f:
            json.dump({"lrm-q5o": {"title": title, "description": desc, "children": kids}}, f, ensure_ascii=False)
        self.conf.cards.clear()

    def report(self, task="lrm-q5o", text="진행 보고", kind="info"):
        return hub_report.add(self.conf.outbox, hub_report.make_report("structure-v2", task, kind, text, []), time.time(), False)[0]

    def calls(self):
        try:
            with open(self.p("calls.jsonl"), encoding="utf-8") as f:
                return [json.loads(ln)["args"] for ln in f]
        except FileNotFoundError:
            return []

    def topics(self):
        with open(self.p("state.json"), encoding="utf-8") as f:
            return json.load(f)["topics"]

    def run_round(self):
        self.conf.cards.clear()
        return report_sender.run(self.conf)

    def test_card_topic_parent_uses_easy_format(self):
        self.report()
        self.assertEqual(self.run_round()["sent"], 1)
        text = self.calls()[0][-1]
        self.assertEqual(text.splitlines()[0], "구조 개선: Slack 봇·작업판 정상화")  # ① 카드 번호·허브 이름 대신 쉬운 이름
        self.assertIn("- 무엇: Slack 봇과 작업판이 제대로 이어지게 고친다.", text)
        self.assertIn("- 왜: 사용자 요청(11-17) 바로바로 소통", text)
        self.assertIn("- 지금: 하위 작업 2/3 완료", text)
        self.assertIn("- 답하는 법: " + report_sender.PARENT_HOW_TO, text)
        self.assertEqual(text.splitlines()[-1], "참고: lrm-q5o · 허브 structure-v2")  # 내부 기호는 맨 아래 참고 줄에만
        self.assertNotIn("lrm-q5o", "\n".join(text.splitlines()[:-1]))
        self.assertTrue(self.topics()["lrm-q5o"]["fmt"].startswith(report_sender.PARENT_FORMAT + ":"))

    def test_missing_easy_name_falls_back_to_card_title(self):
        self.card("무엇: 고친다.\n왜: 요청", [], title="구조 개선")
        self.report()
        self.run_round()
        text = self.calls()[0][-1]
        self.assertEqual(text.splitlines()[0], "구조 개선")
        self.assertIn("- 지금: 하위 작업 없음", text)

    def test_blank_what_after_title_fallback_holds_report_with_fix_hint(self):
        self.card("쉬운 이름: x\n왜: 요청", [], title="[impl] lrm-q5o.4")  # 설명·제목 모두 무엇 재료 없음
        rep = self.report(text="보고 무엇")
        res = self.run_round()
        self.assertEqual((res["sent"], res.get("stopped"), res.get("held")), (0, None, 1))  # 영구 중단이 아니라 보류
        warn = self.calls()[-1][-1]
        self.assertIn("설명 칸 비어 있음(무엇)", warn)
        self.assertIn("카드 설명에 '무엇:'·'왜:' 줄 추가(bd update lrm-q5o -d ...). 추가하면 다음 회차에 자동 전송", warn)
        self.assertNotIn(rep["id"], self.state()["bad"])
        self.assertEqual(self.topics(), {})  # 부모(주제 스레드)는 만들지 않음

    def test_title_only_card_sends_at_once_with_cleaned_title_as_what(self):
        self.card("", [], title="[impl] 카드 lrm-q5o.4: main 을 브랜치에 합친다")
        self.report(text="제목만 있는 카드")
        res = self.run_round()
        self.assertEqual((res["sent"], res.get("held")), (1, None))
        text = next(c for c in self.calls() if "progress" in c and "--thread" not in c)[-1]
        self.assertIn("- 무엇: main 을 브랜치에 합친다", text)  # 머리표·번호·'카드' 머리말 제거
        self.assertEqual(text.splitlines()[0], "main 을 브랜치에 합친다")
        self.assertIn("- 왜: " + report_sender.WHY_MISSING, text)
        self.assertNotIn("lrm-q5o.4", "\n".join(text.splitlines()[:-1]))

    def test_missing_why_sends_with_marker_line_and_warns_once_a_day(self):
        self.card("쉬운 이름: x\n무엇: 고친다", [])
        self.report(text="왜 없음")
        res = self.run_round()
        self.assertEqual((res["sent"], res.get("held")), (1, None))
        text = next(c for c in self.calls() if "progress" in c and "--thread" not in c)[-1]
        self.assertIn("- 왜: 카드에 이유가 안 적혀 있음 — 담당 허브가 채울 예정", text)
        warns = [c for c in self.calls() if "warn" in c]
        self.assertEqual(len(warns), 1)  # 보내면서 허브에 알림
        self.assertIn("설명 칸 비어 있음(왜)", warns[0][-1])
        self.assertIn("structure-v2", warns[0])
        self.assertEqual(self.state()["bad"], {})
        self.card("쉬운 이름: x\n무엇: 고친다", [])
        self.report(text="둘째", task="lrm-q5o")
        self.run_round()
        self.assertEqual(len([c for c in self.calls() if "warn" in c]), 1)  # 열린 스레드·하루 1회라 다시 알리지 않음

    def test_missing_why_notice_waits_for_successful_send(self):
        self.card("쉬운 이름: x\n무엇: 고친다", [])
        self.report(text="왜 없음")
        open(self.p("fail.all"), "w").write("1")  # 전송이 계속 실패
        res = self.run_round()
        self.assertEqual(res["sent"], 0)
        self.assertEqual([c for c in self.calls() if "warn" in c], [], "전송 실패 회차에는 안내를 보내지 않음")
        os.remove(self.p("fail.all"))
        for _ in range(3):  # 재시도 간격(backoff_s=0)이라 바로 다시 시도됨
            self.run_round()
        self.assertEqual(len([c for c in self.calls() if "warn" in c]), 1, "성공한 회차에 한 번")

    def test_hold_older_than_limit_sends_with_available_fields(self):
        self.card("", [], title="[impl]")
        self.report(text="오래 막힌 보고")
        self.assertEqual(self.run_round()["sent"], 0)
        with open(self.conf.outbox, encoding="utf-8") as f:
            rows = [json.loads(ln) for ln in f]
        for r in rows:
            r["at"] -= int(self.conf.hold_max_s) + 10  # 접수 뒤 상한 경과
        with open(self.conf.outbox, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        res = self.run_round()
        self.assertEqual((res["sent"], res.get("held")), (1, None))
        self.assertIn("lrm-q5o", self.topics())

    def test_title_what_cleans_tag_id_and_prefix(self):
        for raw, want in (("[impl] 카드 lrm-q5o.4: 합친다", "합친다"), ("[분석][impl] lrm-q5o.13 고친다", "고친다"), ("그냥 제목", "그냥 제목"), ("[impl]", "")):
            self.assertEqual(report_sender.title_what(raw), want, raw)

    def test_held_report_goes_out_next_round_after_card_fixed(self):
        self.card("", [], title="[impl]")
        self.report(text="막힌 보고")
        self.assertEqual(self.run_round()["sent"], 0)
        self.assertEqual(self.run_round()["sent"], 0)  # 계속 대기
        self.card(FULL, [])
        res = self.run_round()
        self.assertEqual((res["sent"], res.get("held")), (1, None))
        self.assertIn("구조 개선: Slack 봇·작업판 정상화", [c[-1].splitlines()[0] for c in self.calls()])  # 카드를 고친 뒤 같은 보고가 그 양식으로 나감
        self.assertIn("lrm-q5o", self.topics())
        self.assertEqual(self.state()["bad"], {})

    def test_hold_warns_once_per_day_per_card(self):
        self.card("", [], title="[impl]")
        self.report(text="첫 보고")
        for _ in range(3):
            self.run_round()
        warns = [c for c in self.calls() if "warn" in c]
        self.assertEqual(len(warns), 1)  # 회차마다가 아니라 하루 1회
        self.assertIn("structure-v2", warns[0])  # 그 카드 주인 허브 이름으로
        st = self.state()
        st["card_warn"]["lrm-q5o"] -= report_sender.CARD_WARN_INTERVAL_S + 1  # 하루 경과
        report_sender.save_state(self.conf.state, st)
        self.run_round()
        self.assertEqual(len([c for c in self.calls() if "warn" in c]), 2)

    def test_fallback_material_builds_parent(self):
        desc = ("# 작업 카드: 로그인 오류 고치기\n\n순서: 독립(lrm-q5o.4 보다 먼저)\n\n- 로그인 화면이 두 번 뜨는 문제를 고친다. 원인은 중복 호출이다.\n\n"
                "## 지시(사용자 원문)\n\n• 원문(11-17, 캡처): \"로그인이 두 번 떠서 불편함\"\n")
        self.card(desc, [], title="로그인 오류")
        self.report()
        self.assertEqual(self.run_round()["sent"], 1)
        text = self.calls()[0][-1]
        self.assertIn("- 무엇: 로그인 화면이 두 번 뜨는 문제를 고친다.", text)  # 첫 문장, 머리줄·메타 줄·목록 기호 제외
        self.assertIn("- 왜: 로그인이 두 번 떠서 불편함", text)  # 사용자 원문 줄
        self.assertNotIn("lrm-q5o.4", "\n".join(text.splitlines()[:-1]))

    def test_fallback_why_from_instruction_section(self):
        self.card("무엇: 고친다\n\n## 지시\n\n- 결제 오류를 없앤다.\n## 완료\n- 끝", [])
        self.report()
        self.assertEqual(self.run_round()["sent"], 1)
        self.assertIn("- 왜: 결제 오류를 없앤다.", self.calls()[0][-1])

    def test_child_lookup_failure_omits_progress_line(self):
        info = {"id": "x-1", "title": "t", "fields": {"무엇": "a", "왜": "b"}, "closed": 0, "total": None}  # None = 하위 조회 실패
        self.assertNotIn("- 지금", report_sender.card_parent_text(info, "h"))
        self.assertIn("- 지금: 하위 작업 없음", report_sender.card_parent_text({**info, "total": 0}, "h"))

    def test_dead_parent_is_not_retried(self):
        self.report(text="첫 보고")
        self.run_round()
        self.card(FULL, ["closed", "closed", "closed"])
        with open(self.p("fail.n"), "w") as f:
            f.write("1")
        with open(self.p("fail.msg"), "w") as f:
            f.write("message_not_found")
        self.report(text="둘째 보고")
        self.run_round()
        self.assertTrue(self.topics()["lrm-q5o"].get("update_dead"))
        self.report(text="셋째 보고")
        self.run_round()
        self.assertEqual(len([c for c in self.calls() if "--update" in c]), 1)  # 더는 고쳐 쓰기를 시도하지 않음

    def state(self):
        with open(self.p("state.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_gaps_judge_is_pure(self):
        info = {"id": "x-1", "fields": {"무엇": "", "왜": "b"}}
        self.assertEqual(report_sender.card_gaps(info), ["무엇"])
        self.assertEqual(report_sender.card_gaps({"id": "x-1", "fields": {"무엇": "a", "왜": ""}}), [])  # 왜는 보류 사유가 아님
        self.assertEqual(report_sender.card_gaps({"id": "x-1", "fields": {"무엇": "a", "왜": "b"}}), [])

    def test_open_thread_parent_is_updated_only_when_changed(self):
        self.report(text="첫 보고")
        self.run_round()
        self.assertEqual([c for c in self.calls() if "--update" in c], [])  # 새로 열린 스레드는 바로 새 양식
        self.report(text="둘째 보고")
        self.run_round()
        self.assertEqual([c for c in self.calls() if "--update" in c], [])  # 진행률·내용이 그대로면 고치지 않음
        self.card(FULL, ["closed", "closed", "closed"])
        self.report(text="셋째 보고")
        self.run_round()
        upd, = [c for c in self.calls() if "--update" in c]  # 진행률이 바뀌면 갱신
        self.assertEqual(upd[upd.index("--update") + 1], "1700000000.000001")
        self.assertIn("- 지금: 하위 작업 3/3 완료", upd[-1])
        self.report(text="넷째 보고")
        self.run_round()
        self.assertEqual(len([c for c in self.calls() if "--update" in c]), 1)

    def test_legacy_open_thread_without_fmt_gets_new_format_once(self):
        self.report(text="첫 보고")
        self.run_round()
        st = self.state()
        del st["topics"]["lrm-q5o"]["fmt"]  # 양식 도입 전에 열린 스레드
        report_sender.save_state(self.conf.state, st)
        self.report(text="둘째 보고")
        self.run_round()
        self.assertEqual(len([c for c in self.calls() if "--update" in c]), 1)
        self.assertIn("fmt", self.topics()["lrm-q5o"])

    def test_update_failure_does_not_block_report_and_retries_next_time(self):
        self.report(text="첫 보고")
        self.run_round()
        old = self.topics()["lrm-q5o"]["fmt"]
        self.card(FULL, ["closed", "closed", "closed"])
        with open(self.p("fail.n"), "w") as f:
            f.write("1")  # 다음 알림 호출 1건 실패 = 부모 고쳐 쓰기
        self.report(text="둘째 보고")
        res = self.run_round()
        self.assertEqual((res["sent"], res["failed"]), (1, 0))
        self.assertEqual(self.topics()["lrm-q5o"]["fmt"], old)  # 실패하면 기록을 바꾸지 않아 다음에 다시 시도
        self.report(text="셋째 보고")
        self.run_round()
        self.assertEqual(len([c for c in self.calls() if "--update" in c]), 2)  # 실패 1 + 재시도 성공 1

    def test_blank_card_does_not_update_open_thread_but_report_goes(self):
        self.report(text="첫 보고")
        self.run_round()
        self.card("", [], title="[impl]")
        self.report(text="둘째 보고")
        res = self.run_round()
        self.assertEqual(res["sent"], 1)
        self.assertEqual([c for c in self.calls() if "--update" in c], [])
        with open(self.p("log.txt"), encoding="utf-8") as f:
            self.assertIn("부모를 고치지 못함", f.read())

    def test_legacy_task_number_topic_keeps_old_text(self):
        self.report(task="C0012", text="수정 완료", kind="done")
        self.run_round()
        self.assertEqual(self.calls()[0][-1], "C0012 보고 스레드\n\n- 허브: structure-v2\n- 요청: 알림 시각 통일")
        self.assertNotIn("fmt", self.topics()["C0012"])
        self.assertFalse(os.path.exists(self.p("bd_calls.jsonl")))  # 카드 번호가 아니면 bd 를 부르지 않음

    def test_legacy_topic_without_request_warns_in_log(self):
        self.report(task="C0099", text="수정 완료", kind="done")
        self.run_round()
        with open(self.p("log.txt"), encoding="utf-8") as f:
            self.assertIn("'무엇' 줄 없음", f.read())

    def test_unreadable_card_falls_back_to_legacy_text(self):
        os.remove(self.p("cards.json"))
        self.report()
        self.assertEqual(self.run_round()["sent"], 1)
        self.assertTrue(self.calls()[0][-1].startswith("lrm-q5o 보고 스레드"))
        self.assertNotIn("fmt", self.topics()["lrm-q5o"])  # 나중에 카드를 읽으면 새 양식으로 고쳐 씀


if __name__ == "__main__":
    unittest.main()
