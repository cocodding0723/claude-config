"""3단계: Claude 세션의 Bash 호출에서 메인↔허브 전송·Unity 자리 요청·대기 문구 집계 → msg.json"""
import collections as C
import json
import re

from common import args, claude_base

a = args(__doc__)
with open(a.path("sess2.json")) as fh:
    S = json.load(fh)
SEND = re.compile(r"orca (terminal send|orchestration send)")
SLOT = re.compile(r"unity_slot\.sh acquire|unity-run\b")
WAIT = re.compile(r"(대기[^\"]{0,60}슬롯|슬롯[^\"]{0,60}대기|queue position|순번 ?\d+)")
sends, acq, wait = [], [], []
for x in S:
    if x["src"] != "claude" or "act" not in x:
        continue
    with open(f"{claude_base()}/{x['dir']}/{x['f']}", "rb") as fh:
        for line in fh:
            if b"tool_use" not in line and b"tool_result" not in line:
                continue
            try:
                j = json.loads(line)
            except ValueError:
                continue
            ts, c = j.get("timestamp", ""), j.get("message", {}).get("content")
            if not isinstance(c, list):
                continue
            for b in c:
                if b.get("type") == "tool_use" and b.get("name") == "Bash":
                    cmd = b["input"].get("command", "")
                    if SEND.search(cmd):
                        sends.append((x["c"], x["dir"][-40:], ts, cmd[:600]))
                    if SLOT.search(cmd):
                        acq.append((x["c"], x["dir"][-50:], ts, x["f"]))
                if b.get("type") == "tool_result":
                    t = json.dumps(b.get("content"), ensure_ascii=False)
                    m = WAIT.search(t)
                    if m and "unity" in t.lower():
                        wait.append((x["c"], x["dir"][-50:], ts, m.group(0)[:80]))
with open(a.path("msg.json"), "w") as fh:
    json.dump(dict(sends=sends, acq=acq, wait=wait), fh, ensure_ascii=False)
print("전송", len(sends), dict(C.Counter(s[0] for s in sends)))
print("Unity 자리 요청", len(acq), dict(C.Counter(q[0] for q in acq)))
print("자리 대기 문구", len(wait))
