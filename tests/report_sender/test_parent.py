"""카드 주제 부모 메시지(양식 ①~⑥) 시험: 양식 생성·칸 빔 거부·열린 스레드 고쳐 쓰기(바뀐 때만)·기존 작업 번호 주제 유지. 가짜 전송·가짜 bd 만 씀."""
import json
import os
import sys
import tempfile
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
        return hub_report.add(self.conf.outbox, hub_report.make_report("structure-v2", task, kind, text, []), 1.0, False)[0]

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

    def test_blank_what_or_why_blocks_report_with_fix_hint(self):
        for desc, gap in (("쉬운 이름: x\n왜: 요청", "무엇"), ("쉬운 이름: x\n무엇: 고친다", "왜"), ("", "무엇·왜")):
            with self.subTest(gap=gap):
                self.card(desc, [])
                rep = self.report(text=f"보고 {gap}")
                res = self.run_round()
                self.assertEqual((res["sent"], res.get("stopped")), (0, 1))
                warn = self.calls()[-1][-1]
                self.assertIn(f"설명 칸 비어 있음({gap})", warn)
                self.assertIn("카드 설명에 '무엇:'·'왜:' 줄 추가(bd update lrm-q5o -d ...)", warn)
                self.assertIn(rep["id"], self.state()["bad"])
                self.assertEqual(self.topics(), {})  # 부모(주제 스레드)는 만들지 않음
                os.remove(self.p("state.json"))
                os.remove(self.conf.outbox)
                os.remove(self.p("calls.jsonl"))

    def state(self):
        with open(self.p("state.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_gaps_judge_is_pure(self):
        info = {"id": "x-1", "fields": {"무엇": "a", "왜": ""}}
        self.assertEqual(report_sender.card_gaps(info), ["왜"])
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
        self.card("", [])
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
