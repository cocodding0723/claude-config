#!/usr/bin/env python3
"""stall_watch 시험용 가짜 orca. 응답은 $FAKE_DIR 의 JSON 파일, 호출은 calls.log 에 한 줄씩, terminal send 는 sends.log 에 '핸들<TAB>문구'.
FAKE_ORCA_FAIL=1 이면 모든 호출 실패. FAKE_SEND_HANG="핸들,..." 이면 그 핸들 전송은 기록 뒤 5초 지연(시간 초과 재현). terminal list 는 호출 n번째에 terminals_n.json 이 있으면 그 응답.
FAKE_SEND_FAIL="핸들:오류코드,..." 이면 그 핸들로의 terminal send 만 오류 JSON 과 함께 실패(rc 1).
FAKE_SEND_BLOCK="핸들:N,..." 이면 그 핸들의 앞 N번 terminal send 는 agent_prompt_blocked 로 실패, 이후 성공(일시 막힘 재현. 횟수는 block_<핸들>.cnt)."""
import json
import os
import sys
import time

d = os.environ["FAKE_DIR"]
args = sys.argv[1:]
with open(os.path.join(d, "calls.log"), "a", encoding="utf-8") as f:
    f.write(" ".join(args) + "\n")
if os.environ.get("FAKE_ORCA_FAIL") == "1":
    print("boom", file=sys.stderr)
    sys.exit(1)


def load(name):
    with open(os.path.join(d, name), encoding="utf-8") as f:
        return json.load(f)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


if args[:2] == ["orchestration", "worker-list"]:
    out = {"workers": load("workers_%s.json" % opt("--terminal-state"))}
elif args[:2] == ["orchestration", "task-list"]:
    out = {"runId": opt("--run"), "tasks": load("tasks.json").get(opt("--run"), [])}
elif args[:2] == ["terminal", "list"]:
    n = sum(1 for ln in open(os.path.join(d, "calls.log"), encoding="utf-8") if ln.startswith("terminal list"))
    out = {"terminals": load("terminals_%d.json" % n if os.path.exists(os.path.join(d, "terminals_%d.json" % n)) else "terminals.json")}
elif args[:2] == ["orchestration", "worker-read"]:
    out = {"terminal": {"tail": load("read_%s.json" % opt("--dispatch"))}}
elif args[:2] == ["terminal", "send"]:
    bad = dict(p.split(":", 1) for p in os.environ.get("FAKE_SEND_FAIL", "").split(",") if ":" in p)
    if opt("--terminal") in bad:
        with open(os.path.join(d, "failed.log"), "a", encoding="utf-8") as f:
            f.write("%s\n" % opt("--terminal"))
        print(json.dumps({"ok": False, "error": {"code": bad[opt("--terminal")], "message": "fake"}}))
        sys.exit(1)
    blk = dict(p.split(":", 1) for p in os.environ.get("FAKE_SEND_BLOCK", "").split(",") if ":" in p)
    if opt("--terminal") in blk:
        cnt = os.path.join(d, "block_%s.cnt" % opt("--terminal"))
        n = int(open(cnt).read()) if os.path.exists(cnt) else 0
        if n < int(blk[opt("--terminal")]):
            open(cnt, "w").write(str(n + 1))
            with open(os.path.join(d, "failed.log"), "a", encoding="utf-8") as f:
                f.write("%s\n" % opt("--terminal"))
            print(json.dumps({"ok": False, "error": {"code": "agent_prompt_blocked", "message": "fake"}}))
            sys.exit(1)
    with open(os.path.join(d, "sends.log"), "a", encoding="utf-8") as f:
        f.write("%s\t%s\n" % (opt("--terminal"), opt("--text")))
    if opt("--terminal") in os.environ.get("FAKE_SEND_HANG", "").split(","):
        time.sleep(5)  # 전달은 됐지만 응답이 늦는 상황(호출 쪽 시간 초과)
    out = {"sent": True}
else:
    sys.exit(2)
print(json.dumps({"ok": True, "result": out}, ensure_ascii=False))
