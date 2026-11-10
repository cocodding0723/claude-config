#!/bin/bash
# 허브 코디네이터 터미널 순찰표: 허브·에이전트·마지막 출력 나이·막힘·중복.
# 사용: patrol.sh [--json]   설정: ORCA_MAIN_CONFIG (HUB_ROOT, STALE_MINUTES)
# 종료 코드: 0 정상, 1 orca 조회 실패, 2 설정 오류
set -eu
CONF="${ORCA_MAIN_CONFIG:-$HOME/.config/lrm-hubs/main.env}"
[ -f "$CONF" ] || { echo "설정 없음: $CONF (references/main.env.example 참고)" >&2; exit 2; }
. "$CONF"
OUT=$(orca terminal list --json 2>/dev/null) || { echo "orca terminal list 실패" >&2; exit 1; }
printf '%s' "$OUT" | HUB_ROOT="$HUB_ROOT" STALE="${STALE_MINUTES:-60}" MODE="${1:-}" python3 -c '
import json,sys,os,time,collections
ts=json.load(sys.stdin)["result"]["terminals"]
root=os.environ["HUB_ROOT"]; stale=int(os.environ["STALE"]); now=time.time()*1000
rows=[]
for t in ts:
  p=t.get("worktreePath") or ""
  b=os.path.basename(p)
  if not (p.startswith(root) and b.startswith("coord-")) or not t.get("agentIdentity"): continue
  age=int((now-(t.get("lastOutputAt") or 0))/60000)
  pv=(t.get("preview") or "").lower()
  blocked=any(k in pv for k in ("do you want","allow","승인","(y/n)","❯ 1."))
  rows.append(dict(hub=b,agent=t["agentIdentity"],term=t["handle"][:13],age=age,
    stale=age>=stale,blocked=blocked,connected=t.get("connected",False)))
cnt=collections.Counter(r["hub"] for r in rows)
for r in rows: r["dup"]=cnt[r["hub"]]>1
if os.environ["MODE"]=="--json": print(json.dumps(rows,ensure_ascii=False)); sys.exit()
for r in sorted(rows,key=lambda r:r["hub"]):
  flags=" ".join(f for f,c in (("⚠️막힘",r["blocked"]),("💤정지",r["stale"]),("👥중복",r["dup"]),("🔌끊김",not r["connected"])) if c)
  print("%-28s %-7s %s %5d분 %s"%(r["hub"],r["agent"],r["term"],r["age"],flags))
print("허브 터미널 %d개"%len(rows))
'
