"""2단계: 세션을 메인·허브·워커로 나누고 분 단위 동시 실행·허브 유휴 집계 → sess2.json
분류는 폴더 이름 기준(메인 폴더라도 메모리 플러그인 자동 호출은 auto 로 따로 셈): coord-*-hub = 허브, coord-feat-* = 기능 코디네이터(허브로 셈), orca-workspaces 하위 = 워커."""
import collections as C
import datetime as D
import json
import re

from common import args, kst

a = args(__doc__)
with open(a.path("sess.json")) as fh:
    S = json.load(fh)


def cls(x):
    d = x["dir"]
    if x["src"] == "codex":
        return "worker" if "workspaces" in d else "other"
    if "scratchpad" in d or "private-tmp" in d:
        return "other"
    if re.search(r"coord-[a-z0-9-]+-hub(-hub)?$", d) or re.search(r"coord-feat-[a-z0-9-]+$", d):
        return "hub"
    if d.endswith("-orca-LoveRevolutionMaker"):
        return "auto" if x.get("auto") else "main"
    if "orca-workspaces" in d:
        return "worker"
    return "other"


st = C.Counter()
mins = C.defaultdict(lambda: C.defaultdict(set))
for i, x in enumerate(S):
    c = cls(x)
    x["c"] = c
    st[c] += 1
    t = sorted(a2 for a2 in (kst(s) for s in x["ts"]) if a.start <= a2 < a.end)
    if not t:
        continue
    act = set()
    for t1 in t:
        m = t1.replace(second=0)
        act.update(m + D.timedelta(minutes=k) for k in range(5))  # 기록 뒤 5분을 활동으로 봄
    for m in act:
        mins[c][m].add(i)
    x["act"], x["first"], x["last"] = len(act), t[0].isoformat(), t[-1].isoformat()
    x["span"] = (t[-1] - t[0]).total_seconds() / 60
print("분류", dict(st))

hr = C.defaultdict(list)
for m, s in mins["worker"].items():
    hr[m.strftime("%m-%d %H")].append(len(s))
day = C.defaultdict(lambda: [0, 0.0])
for h, v in hr.items():
    d = h[:5]
    day[d][0] = max(day[d][0], max(v))
    day[d][1] += sum(v) / 60
for d in sorted(day):
    print(d, "동시 워커 최고", day[d][0], "워커-시간", round(day[d][1], 1))

TOK_KEYS = ("in", "out", "cache_read", "cache_write")
H = C.defaultdict(lambda: [0, 0, 0.0, dict.fromkeys(TOK_KEYS, 0)])
for x in S:
    if x.get("c") == "hub" and "act" in x:
        k = re.sub(r".*coord-", "", x["dir"])
        H[k][0] += 1
        H[k][1] += x["act"]
        H[k][2] += x["span"]
        for t in TOK_KEYS:
            H[k][3][t] += x["tok"].get(t, 0)
print("허브 세션 활동분 생존분 토큰(M): 입력 출력 캐시읽기 캐시쓰기")
for k, v in sorted(H.items(), key=lambda z: -z[1][2]):
    print(k, v[0], v[1], round(v[2]), *(round(v[3][t] / 1e6, 1) for t in TOK_KEYS))
W = [x for x in S if x.get("c") == "worker" and "act" in x]
print("워커", len(W), "5분 이하", sum(1 for x in W if x["act"] <= 5))
with open(a.path("sess2.json"), "w") as fh:
    json.dump(S, fh)
