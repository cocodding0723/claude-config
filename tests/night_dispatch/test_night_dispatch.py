"""night_dispatch.py 시험: 판정 순서(gate·승인·상한·선행·한가·자원·밤 상한), mode off·dry 무전달·기록, STAGE=1 on 거부, on 경로(STAGE 2 가정). 가짜 측정만 씀(허브·orca·Slack 없음)."""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "hubs", "scripts"))
import night_dispatch as nd  # noqa: E402
import task_ledger as tl  # noqa: E402

POLICY = os.path.join(REPO, "hubs", "night_policy.tsv")
NIGHT = datetime.datetime(2026, 11, 17, 23, 30).timestamp()
HUBS = "# slug\t이름\tcwd\ndocs\t문서\t-\nui\tUI\t-\n"


def quiet(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*a, **kw)
    return rc, out.getvalue(), err.getvalue()


class FakeProbe:
    """측정 값을 시험이 정함. 호출한 전달은 sent 에 쌓임"""

    def __init__(self):
        idle = {"found": True, "connected": True, "output_age_min": 30, "reason": ""}
        self.hubs = {"docs": dict(idle), "ui": dict(idle)}
        self.res = {"free_pct": 63, "load1": 3.0, "disk_free_gb": 100.0}
        self.workers, self.unity, self.prs, self.decisions, self.sent = 0, False, {}, set(), []

    def hub(self, slug, now):
        return self.hubs.get(slug, {"found": False, "reason": "허브 없음"})

    def workers_active(self, now, fresh_min):
        return self.workers

    def resources(self):
        return dict(self.res)

    def unity_busy(self):
        return self.unity

    def pr_merged(self, ref):
        return self.prs.get(ref)

    def decision_answered(self, did):
        return did in self.decisions

    def deliver(self, slug, text):
        self.sent.append((slug, text))
        return "sent"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.d = self.tmp.name
        self.tasks, self.log, self.mode = (os.path.join(d, n) for n in ("tasks.jsonl", "night-dispatch.jsonl", "night.mode"))
        with open(os.path.join(d, "hubs.tsv"), "w", encoding="utf-8") as f:
            f.write(HUBS)
        self.env = mock.patch.dict(os.environ, {"LRM_HUB_DIR": d, "LRM_TASKS": self.tasks, "LRM_NIGHT_POLICY": POLICY, "LRM_NIGHT_LOG": self.log,
                                                "LRM_NIGHT_MODE": self.mode, "LRM_NIGHT_NOW": str(NIGHT), "LRM_HUBS_TABLE": os.path.join(d, "hubs.tsv")})
        self.env.start()
        self.probe = FakeProbe()
        self.set_mode("dry")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def set_mode(self, value):
        with open(self.mode, "w", encoding="utf-8") as f:
            f.write(value + "\n")

    def add(self, *args):
        rc, out, err = quiet(tl.main, ["add", "--json", *args])
        self.assertEqual(rc, 0, err)
        return json.loads(out)["id"]

    def item(self, text="야간: 문서 정리", *extra, hub="docs", approve=True, gate="auto"):
        rid = self.add("--text", text, "--hub", hub, "--state", "배정", "--gate", gate, "--goal", "목표", "--done-when", "draft PR 게시", "--night", *extra)
        if approve:
            self.assertEqual(quiet(tl.main, ["approve-night", rid, "--card", "D-NQ1"])[0], 0)
        return rid

    def go(self, now=NIGHT, *args):
        with mock.patch.dict(os.environ, {"LRM_NIGHT_NOW": str(now)}):
            rc, out, err = quiet(nd.main, ["--once", "--json", *args], self.probe)
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def reasons(self, res):
        return {i["task"]: (i["verdict"], i["reason"]) for i in res["items"]}

    def records(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                return [json.loads(x) for x in f if x.strip()]
        except FileNotFoundError:
            return []

    def ledger_text(self):
        with open(self.tasks, encoding="utf-8") as f:
            return f.read()


class ModeTest(Base):
    def test_off_and_missing_and_garbage_do_nothing(self):
        self.item()
        for value in ("off", "켜짐", ""):
            self.set_mode(value)
            self.assertEqual(self.go()["result"], "mode_off")
        os.remove(self.mode)
        self.assertEqual(self.go()["result"], "mode_off")
        self.assertEqual(self.records(), [])
        self.assertEqual(self.probe.sent, [])

    def test_dry_logs_would_send_without_delivery_or_ledger_change(self):
        rid = self.item()
        before = self.ledger_text()
        res = self.go()
        self.assertEqual(self.reasons(res), {rid: ("would_send", "")})
        self.assertEqual(self.probe.sent, [])
        self.assertEqual(self.ledger_text(), before)
        kinds = [r["event"] for r in self.records()]
        self.assertEqual(kinds, ["resources", "item"])
        rec = self.records()
        self.assertEqual(rec[0]["free_pct"], 63)
        self.assertEqual(rec[0]["unchecked"], list(nd.UNCHECKED))
        self.assertIn("[야간 T0001] 목표", rec[1]["text"])
        self.assertIn("D-NQ1", rec[1]["text"])
        self.assertEqual(rec[1]["night"], "2026-11-17")

    def test_stage1_refuses_on_mode_even_if_file_says_on(self):
        self.item()
        self.set_mode("on")
        before = self.ledger_text()
        res = self.go()
        self.assertEqual(res["result"], "refused_on_stage_1")
        self.assertEqual(nd.STAGE, 1)
        self.assertEqual(self.probe.sent, [])
        self.assertEqual(self.ledger_text(), before)
        self.assertEqual([r["event"] for r in self.records()], ["refused"])

    def test_on_path_exists_when_stage_2(self):
        rid = self.item()
        self.set_mode("on")
        with mock.patch.object(nd, "STAGE", 2):
            res = self.go()
        self.assertEqual(self.reasons(res)[rid][0], "sent")
        self.assertEqual(self.probe.sent[0][0], "docs")
        self.assertIn("야간 규칙", self.probe.sent[0][1])
        night = tl.read_rows(self.tasks)[0]["night"]
        self.assertEqual(night["tries"], 1)  # 전달 직전 횟수 기록
        self.assertTrue(night["sent_at"])

    def test_outside_window_does_nothing_unless_forced(self):
        self.item()
        noon = datetime.datetime(2026, 11, 17, 12, 0).timestamp()
        self.assertEqual(self.go(noon)["result"], "outside_window")
        self.assertEqual(self.records(), [])
        self.assertEqual(self.reasons(self.go(noon, "--ignore-window"))["T0001"][0], "would_send")

    def test_after_midnight_belongs_to_previous_night(self):
        rid = self.item()
        self.go(NIGHT)
        after_midnight = datetime.datetime(2026, 11, 18, 1, 0).timestamp()
        res = self.go(after_midnight)
        self.assertEqual(res["night"], "2026-11-17")
        self.assertEqual(self.reasons(res)[rid], ("skip", "이미 '보냈다면' 기록함"))


class OrderTest(Base):
    def test_gate_before_everything(self):
        rid = self.item(gate="morning", approve=False)
        self.probe.res["free_pct"] = 1
        self.assertEqual(self.reasons(self.go())[rid], ("morning", "gate=morning"))

    def test_forbidden_keyword_sends_to_morning(self):
        a = self.item("야간: 이 PR 병합까지 진행")
        b = self.item("야간: deliver 관련 문서 정리")  # 'live' 가 단어 일부라 걸리지 않음
        res = self.reasons(self.go())
        self.assertEqual(res[a][0], "morning")
        self.assertIn("병합", res[a][1])
        self.assertEqual(res[b][0], "would_send")

    def test_unapproved_waits_before_probing(self):
        rid = self.item(approve=False)
        self.probe.hubs["docs"]["connected"] = False
        self.assertEqual(self.reasons(self.go())[rid], ("wait", "사용자 승인 없음"))

    def test_item_tries_limit_before_needs(self):
        rid = self.item()
        rows = tl.read_rows(self.tasks)
        rows[0]["night"]["tries"] = 2
        tl.write_rows(self.tasks, rows)
        self.assertEqual(self.reasons(self.go())[rid], ("skip", "항목 전달 상한"))

    def test_needs_and_after(self):
        base = self.item("야간: 선행 작업")
        rid = self.item("야간: 후속", "--needs", base, "--after", "pr:#12,decision:D-X1")
        res = self.reasons(self.go())
        self.assertEqual(res[rid], ("wait", f"선행 조건 needs {base}"))
        self.assertEqual(quiet(tl.main, ["set", base, "--state", "완료", "--evidence", "PR #1"])[0], 0)
        self.assertEqual(self.reasons(self.go(NIGHT + 10))[rid], ("wait", "선행 조건 after pr:#12 조회 실패"))  # 조회 실패(None)는 풀리지 않음
        self.probe.prs["#12"] = False
        self.assertEqual(self.reasons(self.go(NIGHT + 20))[rid][1], "선행 조건 after pr:#12")
        self.probe.prs["#12"] = True
        self.assertEqual(self.reasons(self.go(NIGHT + 30))[rid][1], "선행 조건 after decision:D-X1")
        self.probe.decisions.add("D-X1")
        self.assertEqual(self.reasons(self.go(NIGHT + 40))[rid][0], "would_send")

    def test_hub_idle_checks(self):
        rid = self.item()
        cases = ((dict(found=False, reason="코디네이터 터미널 없음"), "허브 터미널 없음"), (dict(found=True, connected=False, output_age_min=60), "허브 연결 끊김"),
                 (dict(found=True, connected=True, output_age_min=3), "허브 최근 출력"), (dict(found=True, connected=True, output_age_min=None), "허브 최근 출력"))
        for i, (hub, want) in enumerate(cases):
            self.probe.hubs["docs"] = hub
            verdict, why = self.reasons(self.go(NIGHT + i))[rid]
            self.assertEqual(verdict, "skip")
            self.assertIn(want, why)

    def test_one_item_per_hub_per_round_and_other_hub_goes(self):
        a, b, c = self.item("야간: 하나"), self.item("야간: 둘"), self.item("야간: UI", hub="ui")
        res = self.reasons(self.go())
        self.assertEqual(res[a][0], "would_send")
        self.assertEqual(res[b], ("skip", "이번 회차에 이 허브에 이미 보냄"))
        self.assertEqual(res[c][0], "would_send")

    def test_active_workers_cap(self):
        rid = self.item()
        self.probe.workers = 3
        self.assertIn("활성 워커 3", self.reasons(self.go())[rid][1])
        self.probe.workers = None
        self.assertIn("활성 워커 None", self.reasons(self.go(NIGHT + 1))[rid][1])

    def test_resource_limits(self):
        rid = self.item()
        for i, (key, val, want) in enumerate((("free_pct", 29, "메모리"), ("load1", 12.5, "부하"), ("disk_free_gb", 5.0, "디스크"), ("free_pct", None, "메모리"))):
            self.probe.res = {"free_pct": 63, "load1": 3.0, "disk_free_gb": 100.0, key: val}
            verdict, why = self.reasons(self.go(NIGHT + i))[rid]
            self.assertEqual(verdict, "skip")
            self.assertIn(want, why)

    def test_unity_slot_only_for_unity_items(self):
        plain, unity = self.item("야간: 일반"), self.item("야간: 유니티", "--uses", "unity", hub="ui")
        self.probe.unity = True
        res = self.reasons(self.go())
        self.assertEqual(res[plain][0], "would_send")
        self.assertIn("Unity 슬롯", res[unity][1])
        self.probe.unity = None  # 조회 실패도 보내지 않음
        self.assertIn("Unity 슬롯", self.reasons(self.go(NIGHT + 1))[unity][1])
        self.probe.unity = False
        self.assertEqual(self.reasons(self.go(NIGHT + 2))[unity][0], "would_send")

    def test_total_night_cap_stops_round(self):
        policy = nd.Policy(POLICY)
        ids = [self.item(f"야간: 작업 {i}", hub=("docs" if i % 2 == 0 else "ui")) for i in range(4)]
        with open(POLICY, encoding="utf-8") as src, open(os.path.join(self.d, "policy.tsv"), "w", encoding="utf-8") as f:
            f.write(src.read().replace("night_total_max\t12", "night_total_max\t3"))
        with mock.patch.dict(os.environ, {"LRM_NIGHT_POLICY": os.path.join(self.d, "policy.tsv")}):
            r1 = self.go(NIGHT)  # docs·ui 한 건씩 = 2건
            r2 = self.go(NIGHT + 1800)  # 한 건 더 = 3건, 다음 항목에서 정지
        self.assertEqual(sum(1 for i in r1["items"] if i["verdict"] == "would_send"), 2)
        self.assertEqual(r2["stopped"], "하룻밤 전달 상한 3건")
        self.assertEqual(sum(1 for r in self.records() if r["event"] == "item" and r["verdict"] == "would_send"), 3)
        self.assertEqual(len(ids), 4)
        self.assertEqual(policy.night_total_max, 12)

    def test_non_night_and_non_queued_rows_ignored(self):
        self.add("--text", "낮 지시", "--hub", "docs", "--state", "배정", "--gate", "auto")
        done = self.item("야간: 끝난 것")
        self.assertEqual(quiet(tl.main, ["set", done, "--state", "완료", "--evidence", "PR #3"])[0], 0)
        self.assertEqual(self.go()["items"], [])


class PolicyTest(Base):
    def test_missing_key_or_file_is_error(self):
        bad = os.path.join(self.d, "p.tsv")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("window_start\t23\n")
        with self.assertRaises(tl.LedgerError):
            nd.Policy(bad)
        with self.assertRaises(tl.LedgerError):
            nd.Policy(os.path.join(self.d, "없음.tsv"))

    def test_real_resources_probe_shape(self):
        res = nd.Probe().resources()
        self.assertEqual(set(res), {"free_pct", "load1", "disk_free_gb"})
        self.assertIsInstance(res["load1"], float)


if __name__ == "__main__":
    unittest.main()
