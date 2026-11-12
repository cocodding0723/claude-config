#!/usr/bin/env python3
"""session_gc 시험용 가짜 orca. 응답은 $FAKE_DIR 의 JSON 파일, 호출은 calls.log 에 한 줄씩.
FAKE_ORCA_FAIL=1 이면 모든 호출 실패, FAKE_ORCA_FAIL_ON=<하위 명령> 이면 그 명령만 실패.
transcript_<dispatch>.json 이 있으면 worker-read --source transcript 성공."""
import json
import os
import sys

d = os.environ["FAKE_DIR"]
args = sys.argv[1:]
with open(os.path.join(d, "calls.log"), "a", encoding="utf-8") as f:
    f.write(" ".join(args) + "\n")
if os.environ.get("FAKE_ORCA_FAIL") == "1" or (os.environ.get("FAKE_ORCA_FAIL_ON") and os.environ["FAKE_ORCA_FAIL_ON"] in args[:2]):
    print("boom", file=sys.stderr)
    sys.exit(1)


def load(name):
    with open(os.path.join(d, name), encoding="utf-8") as f:
        return json.load(f)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


if args[:2] == ["orchestration", "worker-list"]:
    out = {"workers": load("workers_%s.json" % opt("--terminal-state"))}
elif args[:2] == ["terminal", "list"]:
    out = {"terminals": load("terminals.json")}
elif args[:2] == ["orchestration", "worker-read"]:
    if not os.path.exists(os.path.join(d, "transcript_%s.json" % opt("--dispatch"))):
        print(json.dumps({"ok": False, "error": {"code": "transcript_required"}}))
        sys.exit(1)
    out = {"rows": [{"text": "x"}]}
elif args[:2] in (["terminal", "send"], ["terminal", "close"], ["orchestration", "worker-release"]):
    out = {"done": True}
else:
    sys.exit(2)
print(json.dumps({"ok": True, "result": out}, ensure_ascii=False))
