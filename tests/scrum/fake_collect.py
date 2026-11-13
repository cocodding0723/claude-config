import json
import os
import sys

# FAKE_DIR/plan-collect: ok(기본)|media(첨부 후보 파일 만듦)|err-all(prs·commits 오류)|err-prs(prs 만 오류, commits 있음)|err-only(전부 비고 hub 오류)|badjson|rc1
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
if mode in ("media", "media-bad"):
    mdir = os.path.join(os.environ["FAKE_DIR"], "media")
    os.makedirs(mdir, exist_ok=True)
    names = ["a.png", "b-fail-me.mp4"] if mode == "media" else ["a.png", ".env.png", "big.mp4"]
    files = []
    for n in names:
        path = os.path.join(mdir, n)
        with open(path, "wb") as f:
            f.write(b"x" * 10)
        if n == "big.mp4":
            os.truncate(path, 51 * 1024 * 1024)  # 50MB 상한 초과(희소 파일)
        files.append({"path": path, "name": n, "size": 10, "kind": "video" if n.endswith("mp4") else "image", "source": "시험"})
    data["attachments"] = {"files": files, "skipped": []}
print(json.dumps(data))
