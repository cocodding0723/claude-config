import json
import os

from fixture import Base, FakeOrca, hs


class Propose(Base):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.d, "intake-routes.tsv"), "w") as f:
            f.write("# 머리말\t대상\nui\tui\nvfx\tvfx\n*\tmain\n")
        self.doc = os.path.join(self.d, "split.html")
        open(self.doc, "w").write("<p>근거</p>")
        self.run = FakeOrca(self.root, {})
        self.outbox = os.environ["LRM_REPORT_OUTBOX"]

    def split(self, *extra):
        return self.cli(["propose", "--kind", "split", "--hub", "ui-layer", "--child", "ui-popup", "--child-name", "UI 팝업",
                         "--prefix", "ui팝업", "--decision-id", "D-SPLIT9", "--reason", "정리 후 작업 공간 11개", "--file", self.doc, "--json", *extra], self.run)

    def test_default_checks_only(self):
        rc, out, err = self.split()
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["mode"], "dry-run")
        self.assertFalse(os.path.exists(self.outbox))
        self.assertFalse(os.path.exists(hs.state_path()))

    def test_record_writes_card_and_proposal(self):
        rc, out, err = self.split("--record")
        self.assertEqual(rc, 0, err)
        card = json.loads(open(self.outbox).read().splitlines()[-1])
        self.assertEqual((card["kind"], card["decision_id"], card["recommend"]), ("decide", "D-SPLIT9", "1"))
        self.assertTrue(card["text"].startswith("허브 분할 승인: ui-layer → ui-popup"))
        self.assertEqual(json.load(open(hs.state_path()))["proposals"]["D-SPLIT9"]["child"], "ui-popup")

    def test_rejects_existing_prefix_and_slug(self):
        rc, _, err = self.cli(["propose", "--kind", "split", "--hub", "ui-layer", "--child", "x", "--child-name", "X", "--prefix", "ui",
                               "--decision-id", "D-1", "--no-doc"], self.run)
        self.assertEqual(rc, 2)
        self.assertIn("이미 접수 경로 표", err)
        rc, _, err = self.cli(["propose", "--kind", "split", "--hub", "ui-layer", "--child", "vfx", "--child-name", "X", "--prefix", "z",
                               "--decision-id", "D-1", "--no-doc"], self.run)
        self.assertEqual(rc, 2)

    def test_child_cannot_be_split_again(self):
        self.write_hubs(self.HUBS + [("ui-popup", "팝업", "~/r/role-ui-popup.txt", "-", "-", "ui-layer")])
        rc, _, err = self.cli(["propose", "--kind", "split", "--hub", "ui-popup", "--child", "z", "--child-name", "Z", "--prefix", "z",
                               "--decision-id", "D-1", "--no-doc"], self.run)
        self.assertEqual(rc, 2)
        self.assertIn("한 층", err)

    def test_merge_card_resolves_parent(self):
        self.write_hubs(self.HUBS + [("ui-popup", "팝업", "~/r/role-ui-popup.txt", "-", "-", "ui-layer")])
        rc, out, err = self.cli(["propose", "--kind", "merge", "--child", "ui-popup", "--decision-id", "D-MERGE1", "--no-doc", "--json"], self.run)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["plan"]["parent"], "ui-layer")
        rc, _, _ = self.cli(["propose", "--kind", "merge", "--child", "vfx", "--decision-id", "D-M2", "--no-doc"], self.run)
        self.assertEqual(rc, 2)

    def test_needs_doc_or_no_doc_and_valid_id(self):
        rc, _, err = self.cli(["propose", "--kind", "split", "--hub", "ui-layer", "--child", "p", "--child-name", "P", "--prefix", "p",
                               "--decision-id", "D-1"], self.run)
        self.assertEqual(rc, 2)
        rc, _, err = self.split("--decision-id", "bad id")
        self.assertEqual(rc, 2)
