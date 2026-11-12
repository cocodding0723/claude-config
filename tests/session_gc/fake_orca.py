#!/usr/bin/env python3
"""session_gc 시험용 가짜 orca. 응답은 $FAKE_DIR 의 JSON 파일, 호출은 calls.log 에 한 줄씩.
FAKE_ORCA_FAIL=1 이면 모든 호출 실패, FAKE_ORCA_FAIL_ON=<하위 명령> 이면 그 명령만 실패.
FAKE_PAGES=<n> 이면 worker-list 가 n 쪽까지 nextCursor 를 돌려줌(쪽 상한 시험), FAKE_TERM_CURSOR=1 이면 terminal list 에 nextCursor.
after.json 은 '수집 뒤 상태 변화': 첫 worker-show 이후의 worker-show·terminal show·task-list 응답에 덮어씀
  {"terminals": {핸들: {필드}}, "tasks": {taskId: 상태}, "workers": {dispatchId: {resource 필드}}}
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


def load(name, default=None):
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


calls = open(os.path.join(d, "calls.log"), encoding="utf-8").read().splitlines()
after = load("after.json", {}) if any(c.startswith("orchestration worker-show") for c in calls[:-1]) or args[:2] == ["orchestration", "worker-show"] else {}
terminals = load("terminals.json")
for t in terminals:
    t.update((after.get("terminals") or {}).get(t["handle"], {}))
workers = load("workers_retained.json") + load("workers_active.json")

if args[:2] == ["orchestration", "worker-list"]:
    out = {"workers": load("workers_%s.json" % opt("--terminal-state"))}
    pages = int(os.environ.get("FAKE_PAGES", "1"))
    cur = int(opt("--cursor") or 1)
    if cur < pages:
        out["nextCursor"] = str(cur + 1)
elif args[:2] == ["orchestration", "task-list"]:
    tasks = load("tasks.json").get(opt("--run"), [])
    for t in tasks:
        if t["id"] in (after.get("tasks") or {}):
            t["status"] = after["tasks"][t["id"]]
    out = {"runId": opt("--run"), "tasks": tasks}
elif args[:2] == ["terminal", "list"]:
    out = {"terminals": terminals}
    if os.environ.get("FAKE_TERM_CURSOR") == "1":
        out["nextCursor"] = "x"
elif args[:2] == ["terminal", "show"]:
    out = {"terminal": next((t for t in terminals if t["handle"] == opt("--terminal")), None)}
elif args[:2] == ["orchestration", "worker-show"]:
    w = next(w for w in workers if w["dispatchId"] == opt("--dispatch"))
    res = dict(w["resource"] or {})
    res.update((after.get("workers") or {}).get(w["dispatchId"], {}))
    out = {"dispatch": {"id": w["dispatchId"], "taskId": w["taskId"], "status": w["dispatchStatus"]},
           "worker": {"state": w["workerState"], "agentTerminalHandle": w["agentTerminalHandle"]},
           "projection": {"resource": {"terminalState": w["terminalState"]}}, "terminalResource": res}
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
