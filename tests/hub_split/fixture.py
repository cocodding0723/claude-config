"""hub_split 시험 공용 준비: 임시 작업 폴더·허브 표·가짜 orca. 환경 변수로 라이브 경로를 막음"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hubs", "scripts"))
import hub_split as hs  # noqa: E402

os.environ["LRM_PUSH_CONFIRM"] = "1"  # 화면 확인 대기 1초(시험 시간 단축)
NOW = 1_800_000_000.0
DAY = 86400


class FakeOrca:
    """orca 호출 기록 + worktree list 응답. kids={허브 slug: 하위 수}"""

    def __init__(self, root, kids, fail=False):
        self.calls, self.fail, wts = [], fail, []
        for slug, n in kids.items():
            hub = os.path.join(root, f"coord-{slug}-hub")
            ids = [f"{slug}-k{i}" for i in range(n)]
            wts.append({"id": slug, "path": hub, "childWorktreeIds": ids + ["gone-id"]})
            wts += [{"id": i, "path": os.path.join(root, i)} for i in ids]
        self.worktrees = wts
        self.replies = {}

    def __call__(self, args, timeout):
        self.calls.append(args)
        if self.fail:
            return 1, None
        key = " ".join(args[:2])
        if key in self.replies:
            r = self.replies[key]
            return r(args) if callable(r) else r
        if args[:2] == ["worktree", "list"]:
            return 0, {"ok": True, "result": {"worktrees": self.worktrees}}
        return 0, {"ok": True, "result": {}}


class Base(unittest.TestCase):
    HUBS = [("ui-layer", "UI", "~/r/role-ui.txt", "-", "-", ""), ("vfx", "VFX", "~/r/role-vfx.txt", "-", "-", "")]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.root = os.path.join(self.d, "ws")
        env = {"LRM_HUB_DIR": self.d, "LRM_WS_ROOT": self.root, "LRM_HUBS_TSV": "", "LRM_INTAKE_ROUTES": "",
               "LRM_INTAKE_LOG": "", "LRM_HUB_SPLIT_STATE": "", "LRM_WS_GC": os.path.join(self.d, "no-gc"),
               "LRM_LEDGER": os.path.join(self.d, "ledger.tsv"), "LRM_REPORT_OUTBOX": os.path.join(self.d, "outbox.jsonl"),
               "LRM_SLACK_INBOX": os.path.join(self.d, "slack-inbox.jsonl")}
        self._old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.write_hubs(self.HUBS)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def write_hubs(self, rows):
        with open(os.path.join(self.d, "hubs.tsv"), "w") as f:
            f.write("# slug\tname\trole\tws_root\trepo\tparent\n")
            for r in rows:
                cells = list(r[:5]) + ([r[5]] if r[5] else [])
                f.write("\t".join(cells) + "\n")

    def intake(self, hub, ago_days):
        with open(os.path.join(self.d, "intake-routes.jsonl"), "a") as f:
            f.write(json.dumps({"id": "C9", "hub": hub, "text": "t", "at": NOW - ago_days * DAY}) + "\n")

    def cli(self, argv, run):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = hs.main(argv, run=run)
        return rc, out.getvalue(), err.getvalue()

