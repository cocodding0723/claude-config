"""4단계: Unity 자리 명령(unity_slot.sh·unity-run)의 호출→결과 시간과, Unity 실행이 없는 자리 확보 수 집계"""
import collections as C
import json
import re
import statistics as st

from common import args, claude_base, kst

a = args(__doc__)
with open(a.path("sess2.json")) as fh:
    S = json.load(fh)
CMD = re.compile(r"unity_slot\.sh (acquire|status|release)|unity-run\b")
UNITY = re.compile(r"editmode|playmode|verify|record|녹화|smoke|스모크|runTests|Unity -|batchmode|unity-run|local-gate|gate", re.I)
res = []
for x in S:
    if x["src"] != "claude" or "act" not in x:
        continue
    with open(f"{claude_base()}/{x['dir']}/{x['f']}", "rb") as fh:
        raw = fh.read()
    if not CMD.search(raw.decode("utf-8", "replace")):
        continue
    pend = {}
    for line in raw.split(b"\n"):
        try:
            j = json.loads(line)
        except ValueError:
            continue
        c = j.get("message", {}).get("content") if isinstance(j, dict) else None
        if "timestamp" not in j or not isinstance(c, list):
            continue
        for b in c:
            if b.get("type") == "tool_use" and b.get("name") == "Bash" and CMD.search(b["input"].get("command", "")):
                pend[b["id"]] = (j["timestamp"], b["input"]["command"])
            if b.get("type") == "tool_result" and b.get("tool_use_id") in pend:
                t0, cmd = pend.pop(b["tool_use_id"])
                lab = re.search(r"(?:acquire|--name)\s+[\"']?([^\"'\s)]+)", cmd)
                res.append((x["c"], kst(t0), (kst(j["timestamp"]) - kst(t0)).total_seconds(), lab.group(1)[:60] if lab else "", cmd[:400]))
res = [r for r in res if a.start <= r[1] < a.end]
w = sorted(r[2] for r in res)
if w:
    print("자리 명령", len(w), "중앙(초)", st.median(w), "p90", w[int(len(w) * 0.9)], "합(시간)", round(sum(w) / 3600, 1))
else:
    print("자리 명령 0")
print("Unity 실행 없는 자리 명령", sum(1 for r in res if not UNITY.search(r[4])))
print("일별", dict(sorted(C.Counter(r[1].strftime("%m-%d") for r in res).items())))
