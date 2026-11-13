"""결정 답 전달 경로(answer_route) 시험. 허브 전송은 ①주입한 가짜 함수 ②실제 card_actions.deliver + 가짜 orca 두 가지로 시험함.
임시 폴더만 쓰며 실제 ~/.config/lrm-hubs·Orca·터미널은 건드리지 않음."""
import json
import os
import shutil
import sys
import tempfile
import unittest
import warnings

warnings.simplefilter("ignore", ResourceWarning)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "hubs", "scripts"))
FAKE_ORCA = os.path.join(ROOT, "tests", "stall_watch", "fake_orca.py")
MODS = ("answer_route", "answer_store", "card_actions", "main_terminal", "hub_dups", "ci_review", "todo_facts", "panel_collect", "progress_collect")
MAINH = "term_0000aaaa-0000-4000-8000-000000000001"
BODY = "결정 D-0001 = 선택 2(B 안) (버튼)"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cc-route-")
        self.hub, self.fake = os.path.join(self.tmp.name, "hub"), os.path.join(self.tmp.name, "fake")
        bin_dir = os.path.join(self.tmp.name, "bin")
        for d in (self.hub, self.fake, bin_dir):
            os.makedirs(d)
        shutil.copy(FAKE_ORCA, os.path.join(bin_dir, "orca"))
        os.chmod(os.path.join(bin_dir, "orca"), 0o755)
        self.old = dict(os.environ)
        os.environ.update({"LRM_HUB_DIR": self.hub, "FAKE_DIR": self.fake, "LRM_ORCA": os.path.join(bin_dir, "orca"),
                           "HUB_PROC_TABLE": os.path.join(self.tmp.name, "no-proc-table"), "LRM_MAIN_ENV": os.path.join(self.hub, "main.env")})
        for m in [m for m in sys.modules if m in MODS]:
            del sys.modules[m]
        import answer_route
        import answer_store
        import card_actions
        self.route_mod, self.st, self.actions = answer_route, answer_store, card_actions
        self.store = answer_store.AnswerStore()
        self.write("hubs.tsv", "alpha\t알파 허브\t-\t-\t-\nbeta\t베타 허브\t-\t-\t-\n")
        self.write("main.env", f"MAIN_TERMINAL={MAINH}\n")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)
        self.tmp.cleanup()

    def write(self, name, text, folder=None):
        with open(os.path.join(folder or self.hub, name), "w", encoding="utf-8") as f:
            f.write(text)

    def jsonl(self, name, rows):
        self.write(name, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    def map_decision(self, did="D-0001", hub="alpha"):
        self.jsonl("hub-reports.jsonl", [{"decision_id": did, "hub": hub}])


class UnitRoute(Base):
    """허브 전송을 가짜 함수로 바꿔 분기만 시험"""

    def router(self, status, codes=(), main_ok=True):
        self.hub_calls, self.main_calls = [], []

        def deliver(slug, text):
            self.hub_calls.append((slug, text))
            return status, 1, list(codes), 1

        def to_main(text):
            self.main_calls.append(text)
            return main_ok
        return self.route_mod.Router(self.store, to_main, deliver)

    def test_no_mapping_goes_to_main_as_before(self):
        out = self.router("sent").route("D-0001", "1.1", "slack", BODY)
        self.assertEqual((out.ok, out.via, self.hub_calls), (True, "main", []))
        self.assertEqual(self.main_calls, [f"[Slack→메인] {BODY} (1.1)"])

    def test_mapped_hub_gets_original_text_with_source_and_main_gets_reference(self):
        self.map_decision()
        out = self.router("sent").route("D-0001", "1.1", "docsite", BODY)
        self.assertEqual((out.ok, out.via, out.hub), (True, "hub", "alpha"))
        self.assertEqual(self.hub_calls, [("alpha", f"사용자 원문: {BODY} (출처: 문서 사이트)")])
        self.assertEqual(len(self.main_calls), 1)
        self.assertTrue(self.main_calls[0].startswith("[문서 사이트→메인 참고] alpha 허브에 직접 전달함"))

    def test_same_decision_reported_by_two_hubs_goes_to_main(self):
        self.jsonl("hub-reports.jsonl", [{"decision_id": "D-0001", "hub": "alpha"}, {"decision_id": "D-0001", "hub": "beta"}])
        out = self.router("sent").route("D-0001", "1.1", "slack", BODY)
        self.assertEqual((out.via, self.hub_calls), ("main", []))

    def test_same_hub_reporting_twice_is_not_ambiguous(self):
        self.jsonl("hub-reports.jsonl", [{"decision_id": "D-0001", "hub": "alpha"}, {"decision_id": "D-0001", "hub": "alpha"}])
        self.assertEqual(self.router("sent").route("D-0001", "1.1", "slack", BODY).via, "hub")

    def test_outcome_is_recorded_for_late_click_judgement(self):
        self.map_decision()
        self.router("sent").route("D-0001", "1.1", "slack", BODY)
        self.assertEqual(self.store.deliveries("D-0001", "1.1")[-1]["state"], "hub")
        self.router("failed", ("no_terminal",), main_ok=False).route("D-0001", "2.2", "slack", BODY)
        last = self.store.deliveries("D-0001", "2.2")[-1]
        self.assertEqual((last["state"], last["ok"]), ("lost", False))

    def test_hub_from_sent_card_tag_when_no_report(self):
        self.jsonl("slack-sent.jsonl", [{"ts": "1.1", "form_kind": "decide", "decision_id": "D-0001", "tag": "beta", "options": {"1": "가"}},
                                        {"ts": "2.2", "form_kind": "decide", "decision_id": "D-0002", "tag": "ci-watch", "options": {"1": "가"}}])
        r = self.router("sent")
        self.assertEqual(r.hub_of("D-0001"), "beta")
        self.assertEqual(r.hub_of("D-0002"), "")  # 허브 이름이 아닌 tag 는 근거가 아님

    def test_send_failure_falls_back_to_main_proxy_with_original_text(self):
        self.map_decision()
        out = self.router("failed", ["terminal_handle_stale"]).route("D-0001", "1.1", "slack", BODY)
        self.assertEqual((out.ok, out.via), (True, "proxy"))
        self.assertEqual(len(self.main_calls), 1)
        self.assertIn("alpha 허브에 전달하지 못해 대리 전달: terminal_handle_stale", self.main_calls[0])
        self.assertIn(f"사용자 원문: {BODY} (출처: Slack)", self.main_calls[0])

    def test_proxy_also_failing_is_lost(self):
        self.map_decision()
        out = self.router("failed", main_ok=False).route("D-0001", "1.1", "slack", BODY)
        self.assertEqual((out.ok, out.via), (False, "lost"))

    def test_timeout_is_unknown_and_never_resent_to_hub_or_as_original_to_main(self):
        self.map_decision()
        out = self.router("unknown", ["timeout"]).route("D-0001", "1.1", "slack", BODY)
        self.assertEqual((out.ok, out.via), (False, "unknown"))
        self.assertEqual(len(self.hub_calls), 1)
        self.assertEqual(len(self.main_calls), 1)
        self.assertIn("확인 필요", self.main_calls[0])
        self.assertNotIn("사용자 원문", self.main_calls[0])

    def test_reference_failure_does_not_fail_the_answer(self):
        self.map_decision()
        out = self.router("sent", main_ok=False).route("D-0001", "1.1", "slack", BODY)
        self.assertTrue(out.ok)
        self.assertIn("참고 한 줄만 실패", out.note)

    def test_newlines_in_text_are_flattened(self):
        self.map_decision()
        self.router("sent").route("D-0001", "1.1", "slack", "결정 D-0001 답(자유 글): 첫 줄\n둘째 줄")
        self.assertNotIn("\n", self.hub_calls[0][1])

    def test_unreadable_mapping_files_mean_main(self):
        os.makedirs(os.path.join(self.hub, "hub-reports.jsonl"))  # 파일 자리에 폴더: 읽기 오류
        out = self.router("sent").route("D-0001", "1.1", "slack", BODY)
        self.assertEqual((out.via, self.hub_calls), ("main", []))


class FakeOrcaRoute(Base):
    """실제 card_actions.deliver 를 가짜 orca 로 돌림(재전송 금지·시간 초과·마감 규칙을 재사용하는지)"""

    def setUp(self):
        super().setUp()
        for name in ("workers_retained", "workers_active"):
            self.write(name + ".json", "[]", self.fake)
        self.terms([("term_a", 900), ("term_b", 500)])
        self.map_decision()
        for log in ("sends.log", "calls.log"):
            self.write(log, "", self.fake)

    def terms(self, rows):
        self.write("terminals.json", json.dumps([{"handle": h, "title": "알파 코디", "worktreePath": "/x/coord-alpha-hub", "agentIdentity": "claude",
                                                  "connected": True, "writable": True, "orphaned": False, "lastOutputAt": out} for h, out in rows]), self.fake)

    def sends(self):
        with open(os.path.join(self.fake, "sends.log"), encoding="utf-8") as f:
            return [ln.rstrip("\n").split("\t", 1) for ln in f if ln.strip()]

    def router(self):
        return self.route_mod.Router(self.store, self.route_mod.main_line_sender())

    def test_sent_to_latest_hub_terminal_then_reference_to_main(self):
        out = self.router().route("D-0001", "1.1", "docsite", BODY)
        self.assertEqual((out.ok, out.via), (True, "hub"))
        rows = self.sends()
        self.assertEqual(rows[0], ["term_a", f"사용자 원문: {BODY} (출처: 문서 사이트)"])
        self.assertEqual(rows[1][0], MAINH)
        self.assertIn("참고", rows[1][1])
        self.assertEqual(len(rows), 2)

    def test_failed_on_all_candidates_goes_to_main_without_resending_failed_handles(self):
        os.environ["FAKE_SEND_FAIL"] = "term_a:terminal_handle_stale,term_b:terminal_handle_stale"
        out = self.router().route("D-0001", "1.1", "slack", BODY)
        self.assertEqual((out.ok, out.via), (True, "proxy"))
        rows = self.sends()
        self.assertEqual([r[0] for r in rows], [MAINH])  # 허브 두 곳은 실패해 기록 없음, 메인 대리만 도착
        self.assertIn(f"사용자 원문: {BODY} (출처: Slack)", rows[0][1])
        with open(os.path.join(self.fake, "calls.log"), encoding="utf-8") as f:
            self.assertEqual(sum(1 for ln in f if ln.startswith("terminal send --terminal term_a")), 1)

    def test_timeout_does_not_resend_to_other_candidate_or_main_original(self):
        os.environ["FAKE_SEND_HANG"] = "term_a"
        self.actions.ORCA_TIMEOUT_S = 1
        out = self.router().route("D-0001", "1.1", "slack", BODY)
        self.assertEqual((out.ok, out.via), (False, "unknown"))
        rows = self.sends()
        self.assertEqual([r[0] for r in rows].count("term_a"), 1)
        self.assertNotIn("term_b", [r[0] for r in rows])
        main_rows = [r for r in rows if r[0] == MAINH]
        self.assertEqual(len(main_rows), 1)
        self.assertIn("확인 필요", main_rows[0][1])
        self.assertNotIn("사용자 원문", main_rows[0][1])

    def test_no_hub_terminal_goes_to_main(self):
        self.terms([])
        out = self.router().route("D-0001", "1.1", "slack", BODY)
        self.assertEqual((out.ok, out.via), (True, "proxy"))
        self.assertIn("코디네이터 터미널 없음", self.sends()[0][1])

    def test_no_mapping_never_touches_hub_terminals(self):
        os.remove(os.path.join(self.hub, "hub-reports.jsonl"))
        out = self.router().route("D-0001", "1.1", "slack", BODY)
        self.assertEqual((out.ok, out.via), (True, "main"))
        self.assertEqual([r[0] for r in self.sends()], [MAINH])
        with open(os.path.join(self.fake, "calls.log"), encoding="utf-8") as f:
            self.assertNotIn("terminal list", f.read())


class SlugFolder(Base):
    """hubs.tsv slug(끝이 -hub 인 것·옛 coord-*-hub 형식 포함) → 전달 slug → 실제 폴더 in_hub 일치"""
    CASES = (("automa-hub", "automa-hub", "/w/coord-automa-hub-hub"), ("data-source-hub", "data-source-hub", "/w/coord-data-source-hub-hub"),
             ("coord-server-hub", "coord-server-hub", "/w/coord-coord-server-hub-hub"), ("ui-layer", "ui-layer", "/w/coord-ui-layer-hub"))

    def test_decision_to_delivery_slug_matches_hub_folder(self):
        import hub_dups
        self.write("hubs.tsv", "".join(f"{raw}\tN\t-\t-\t-\n" for raw, _, _ in self.CASES))
        for raw, want, folder in self.CASES:
            with self.subTest(hub=raw):
                self.map_decision("D-1", raw)
                slug = self.route_mod.Router(self.store, lambda t: True).hub_of("D-1")
                self.assertEqual(slug, want)
                self.assertTrue(hub_dups.in_hub({"worktreePath": folder}, slug=slug))

    def test_server_hub_every_spelling_reaches_same_folder(self):
        """hubs.tsv slug coord-server-hub: 상태 파일 이름(server)·slug·폴더 이름 모두 → 전달 대상 폴더 coord-coord-server-hub-hub"""
        import hub_dups
        import progress_collect as pc
        slugs = {"coord-server-hub", "automa-hub", "data-source-hub", "ui-layer"}
        for value in ("server", "coord-server-hub", "coord-coord-server-hub-hub"):
            with self.subTest(value=value):
                slug = pc.hub_slug(value, slugs)
                self.assertEqual(slug, "coord-server-hub")
                self.assertTrue(hub_dups.in_hub({"worktreePath": "/w/coord-coord-server-hub-hub"}, slug=slug))
        for value in ("automa-hub", "data-source-hub", "ui-layer"):  # 회귀 없음
            self.assertEqual(pc.hub_slug(value, slugs), value)
        self.assertEqual(pc.hub_slug("coord-automa-hub-hub", slugs), "automa-hub")
        self.assertEqual(pc.hub_slug("automa", slugs), "automa-hub")  # 옛 규칙 별칭

    def test_without_hubs_table_folder_rule_only(self):
        import progress_collect as pc
        import panel_collect
        for value, want in (("coord-automa-hub-hub", "automa-hub"), ("coord-ui-layer-hub", "ui-layer"), ("automa-hub", "automa-hub"), ("ui-layer", "ui-layer")):
            self.assertEqual(pc.hub_slug(value), want)
            self.assertEqual(pc.hub_slug(value, set()), want)
        self.assertEqual(panel_collect.hub_name("coord-automa-hub-hub"), "automa-hub")

    def test_display_key_is_status_file_name(self):
        """전달 slug 와 상태 파일 이름이 다른 허브: 표시용(status_key)은 server, 전달용은 coord-server-hub"""
        import progress_collect as pc
        import panel_collect
        slugs = {"coord-server-hub", "automa-hub"}
        self.assertEqual(panel_collect.hub_name("coord-coord-server-hub-hub", slugs), "server")
        self.assertEqual(panel_collect.hub_name("coord-automa-hub-hub", slugs), "automa-hub")
        self.assertEqual(pc.status_key("coord-server-hub"), "server")

    def test_workspace_counts_key_matches_status_file_slug(self):
        import progress_collect as pc
        wts = [{"id": "h", "path": "/w/coord-automa-hub-hub", "childWorktreeIds": ["c"]}, {"id": "c", "path": "/w/c"},
               {"id": "s", "path": "/w/coord-coord-server-hub-hub"}, {"id": "u", "path": "/w/coord-ui-layer-hub", "childWorktreeIds": ["c"]}]
        out = pc.workspace_counts(wts, [{"worktreeId": "c"}], {"coord-server-hub", "automa-hub", "ui-layer"})
        self.assertEqual(out["automa-hub"], {"children": 1, "active": 1})  # hub-status/automa-hub.json 과 같은 키
        self.assertEqual(set(out), {"automa-hub", "server", "ui-layer"})


if __name__ == "__main__":
    unittest.main()
