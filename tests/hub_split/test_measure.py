import json
import os

from fixture import DAY, NOW, Base, FakeOrca, hs


class Measure(Base):
    def run_at(self, run, now, *extra):
        rc, out, err = self.cli(["measure", "--no-gc", "--json", "--now", str(now), *extra], run)
        self.assertEqual(rc, 0, err)
        return {h["slug"]: h for h in json.loads(out)["hubs"]}

    def test_workspace_only_is_watch_not_split(self):
        r = self.run_at(FakeOrca(self.root, {"ui-layer": 9, "vfx": 3}), NOW)
        self.assertEqual(r["ui-layer"]["verdict"], "watch")
        self.assertIn("--topic ui-layer=N", " ".join(r["ui-layer"]["reasons"]))
        self.assertEqual(r["ui-layer"]["effective"], 9)  # 지워진 하위 id 는 셈에서 뺌
        self.assertEqual(r["vfx"]["verdict"], "none")

    def test_split_needs_both_signals_and_two_records(self):
        run = FakeOrca(self.root, {"ui-layer": 10, "vfx": 1})
        r = self.run_at(run, NOW, "--topic", "ui-layer=4")
        self.assertEqual(r["ui-layer"]["verdict"], "watch")  # 주제 작업 4건 < 5
        r = self.run_at(run, NOW, "--topic", "ui-layer=5", "--record")
        self.assertEqual(r["ui-layer"]["verdict"], "watch")  # 연속 1/2
        r = self.run_at(run, NOW + 1800, "--topic", "ui-layer=5", "--record")
        self.assertEqual(r["ui-layer"]["verdict"], "split")
        # 간격이 1시간을 넘으면 연속이 끊김
        r = self.run_at(run, NOW + 1800 + 7200, "--topic", "ui-layer=5")
        self.assertEqual(r["ui-layer"]["verdict"], "watch")

    def test_read_only_writes_nothing(self):
        self.run_at(FakeOrca(self.root, {"ui-layer": 10}), NOW, "--topic", "ui-layer=6")
        self.assertFalse(os.path.exists(hs.state_path()))

    def test_gc_candidates_are_subtracted(self):
        run = FakeOrca(self.root, {"ui-layer": 9})
        gc = os.path.join(self.d, "gc.json")
        with open(gc, "w") as f:
            json.dump({"items": [{"path": os.path.join(self.root, "ui-layer-k0"), "verdict": "candidate"},
                                 {"path": os.path.join(self.root, "ui-layer-k1"), "verdict": "skip"}]}, f)
        rc, out, err = self.cli(["measure", "--gc-json", gc, "--json", "--now", str(NOW)], run)
        h = json.loads(out)["hubs"][0]
        self.assertEqual((h["workspaces"], h["gc_candidates"], h["effective"], h["verdict"]), (9, 1, 8, "none"))

    def test_child_hub_never_splits(self):
        self.write_hubs(self.HUBS + [("ui-popup", "팝업", "~/r/role-ui-popup.txt", "-", "-", "ui-layer")])
        r = self.run_at(FakeOrca(self.root, {"ui-layer": 1, "ui-popup": 12}), NOW, "--topic", "ui-popup=9")
        self.assertEqual(r["ui-popup"]["verdict"], "none")
        self.assertIn("한 층", " ".join(r["ui-popup"]["reasons"]))

    def test_merge_needs_sum_idle_and_three_days(self):
        self.write_hubs(self.HUBS + [("ui-popup", "팝업", "~/r/role-ui-popup.txt", "-", "-", "ui-layer")])
        run = FakeOrca(self.root, {"ui-layer": 3, "ui-popup": 2})
        self.intake("ui-popup", 8)  # 7일 밖 접수는 세지 않음
        verdicts = []
        for i, gap in enumerate([0, 3600, DAY, 2 * DAY]):  # 같은 날 재점검은 하루로 침
            verdicts.append(self.run_at(run, NOW + gap, "--record")["ui-popup"]["verdict"])
        self.assertEqual(verdicts, ["watch", "watch", "watch", "merge"])
        self.intake("ui-popup", -2.5)  # 최근 접수 1건이면 병합 안 함
        self.assertEqual(self.run_at(run, NOW + 3 * DAY)["ui-popup"]["verdict"], "none")

    def test_merge_blocked_when_sum_over(self):
        self.write_hubs(self.HUBS + [("ui-popup", "팝업", "~/r/role-ui-popup.txt", "-", "-", "ui-layer")])
        r = self.run_at(FakeOrca(self.root, {"ui-layer": 4, "ui-popup": 2}), NOW)
        self.assertEqual(r["ui-popup"]["verdict"], "none")

    def test_cooldown_blocks_proposal(self):
        run = FakeOrca(self.root, {"ui-layer": 10})
        with open(hs.state_path(), "w") as f:
            json.dump({"events": {"ui-layer": {"merge_at": NOW - 3 * DAY}},
                       "streaks": {"ui-layer": {"split": {"n": 5, "at": NOW - 1800}}}}, f)
        r = self.run_at(run, NOW, "--topic", "ui-layer=7")
        self.assertEqual(r["ui-layer"]["verdict"], "cooldown")
        r = self.run_at(run, NOW + 5 * DAY, "--topic", "ui-layer=7")
        self.assertEqual(r["ui-layer"]["verdict"], "watch")  # 냉각 끝, 연속 간격이 끊겨 1부터

    def test_orca_down_is_env_error(self):
        rc, _, err = self.cli(["measure", "--no-gc"], FakeOrca(self.root, {}, fail=True))
        self.assertEqual(rc, 3)
        self.assertIn("Orca 실행 여부", err)

    def test_usage_errors(self):
        run = FakeOrca(self.root, {})
        self.assertEqual(self.cli(["measure", "--no-gc", "--topic", "ui-layer"], run)[0], 2)
        self.assertEqual(self.cli(["measure", "--no-gc", "--topic", "nope=3"], run)[0], 2)
        self.assertEqual(self.cli(["measure", "--bogus"], run)[0], 2)

    def test_help_has_examples_and_exit_codes(self):
        p = hs.build_parser()
        self.assertIn("종료 코드", p.format_help())
        self.assertIn("--topic", p._subparsers._group_actions[0].choices["measure"].format_help())

    def test_six_column_parent_parsed(self):
        self.write_hubs(self.HUBS + [("ui-popup", "팝업", "~/r/role-ui-popup.txt", "-", "-", "ui-layer")])
        hubs = {h.slug: h for h in hs.read_hubs()}
        self.assertEqual((hubs["ui-popup"].parent, hubs["ui-layer"].parent, hubs["ui-popup"].intake_name), ("ui-layer", "", "ui-popup"))
