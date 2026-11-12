import json
import os
import sys

# FAKE_DIR/plan-collect: ok(기본)|err-all(prs·commits 오류)|err-prs(prs 만 오류, commits 있음)|err-only(전부 비고 hub 오류)|badjson|rc1
mode = "ok"
try:
    mode = open(os.path.join(os.environ["FAKE_DIR"], "plan-collect"), encoding="utf-8").read().strip()
except OSError:
    pass
data = {"prs": [], "commits": [], "hub_reports": [], "handoffs": [], "instructions": [], "errors": []}
if mode == "err-all":
    data["errors"] = ["prs: gh: keyring unavailable", "commits: git: fatal"]
elif mode == "err-prs":
    data["errors"] = ["prs: gh: keyring unavailable"]
    data["commits"] = [{"sha": "abc", "subject": "x"}]
elif mode == "err-only":
    data["errors"] = ["hub_reports: OSError"]
elif mode == "badjson":
    print("not json")
    sys.exit(0)
elif mode == "rc1":
    print("boom", file=sys.stderr)
    sys.exit(1)
print(json.dumps(data))
