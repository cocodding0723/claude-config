#!/bin/bash
# 허브 코디네이터 터미널 순찰표: 허브·에이전트·마지막 출력 나이·막힘·중복.
# 사용: patrol.sh [--json]   설정: ORCA_MAIN_CONFIG (HUB_ROOT, STALE_MINUTES)
#       patrol.sh --inbox [--json] [--peek]  받은 편지함(hub-inbox/main.jsonl)의 읽은 위치 이후 새 줄. 자동 알림은 터미널 입력 대신 여기로 옴.
#         읽은 위치(inode+오프셋)는 main-inbox.offset 에 기록(--peek 은 기록 안 함). 회전·잘림이면 옛 파일 잔여분 또는 처음부터. 허브 조회(orca)는 하지 않음
# 워커 소멸: 지난 순찰에 있던 허브 하위 워커 터미널이 사라지면 브랜치·마지막 커밋과 함께 1회 표시(상태 PATROL_STATE)
# 병합 PR → 장부 자동 완료: 표 순찰 끝에 ledger_merged.py --apply(확실한 연결만 완료, 애매하면 후보). 실패해도 순찰은 정상 종료(메인 승인 11-16 추천 A)
# 종료 코드: 0 정상, 1 orca 조회 실패(--inbox 는 편지함 읽기·쓰기 실패), 2 설정 오류(--inbox 는 인자 오류)
set -eu
CONF="${ORCA_MAIN_CONFIG:-$HOME/.config/lrm-hubs/main.env}"
[ -f "$CONF" ] || { echo "설정 없음: $CONF (references/main.env.example 참고)" >&2; exit 2; }
. "$CONF"
if [ "${1:-}" = "--inbox" ]; then
  shift
  export LRM_HUB_DIR="${LRM_HUB_DIR:-${HUB_SCRIPTS:-$HOME/.config/lrm-hubs}}"  # 받은 편지함·읽은 위치 파일이 있는 작업 폴더
  exec python3 -I "$LRM_HUB_DIR/main_inbox.py" read --since-offset "$@"
fi
OUT=$(orca terminal list --json 2>/dev/null) || { echo "orca terminal list 실패" >&2; exit 1; }
printf '%s' "$OUT" | PATROL_STATE="${PATROL_STATE:-$HOME/.config/lrm-hubs/patrol-workers.json}" HUB_ROOT="$HUB_ROOT" STALE="${STALE_MINUTES:-60}" MODE="${1:-}" python3 -c '
import json,sys,os,time,collections,subprocess
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
# 워커 = HUB_ROOT 아래 coord- 가 아닌 에이전트 터미널. 이번 목록과 지난 상태를 대조
workers={t["handle"]:dict(path=t.get("worktreePath") or "",branch=(t.get("branch") or "").replace("refs/heads/",""),agent=t.get("agentIdentity"))
  for t in ts if (t.get("worktreePath") or "").startswith(root) and not os.path.basename(t.get("worktreePath") or "").startswith("coord-") and t.get("agentIdentity")}
sp=os.environ["PATROL_STATE"]
try: prev=json.load(open(sp))
except (OSError,ValueError): prev={}
gone=[]
for h,w in prev.items():
  if h in workers: continue
  try: last=subprocess.run(["git","-C",w["path"],"log","-1","--format=%h %cr %s",w["branch"]],capture_output=True,text=True,timeout=10).stdout.strip()
  except (OSError,subprocess.TimeoutExpired): last=""
  gone.append(dict(term=h[:13],ws=os.path.basename(w["path"]),branch=w["branch"],last=last or "커밋 확인 불가"))
try:
  tmp=sp+".tmp"; json.dump(workers,open(tmp,"w")); os.replace(tmp,sp)
except OSError: pass
cnt=collections.Counter(r["hub"] for r in rows)
for r in rows: r["dup"]=cnt[r["hub"]]>1
if os.environ["MODE"]=="--json": print(json.dumps(dict(hubs=rows,gone=gone),ensure_ascii=False)); sys.exit()
for r in sorted(rows,key=lambda r:r["hub"]):
  flags=" ".join(f for f,c in (("⚠️막힘",r["blocked"]),("💤정지",r["stale"]),("👥중복",r["dup"]),("🔌끊김",not r["connected"])) if c)
  print("%-28s %-7s %s %5d분 %s"%(r["hub"],r["agent"],r["term"],r["age"],flags))
for g in gone:
  print("🫥 워커 사라짐 %s %s 브랜치 %s · 마지막 커밋 %s"%(g["ws"],g["term"],g["branch"],g["last"]))
print("허브 터미널 %d개 · 워커 %d개 · 사라진 워커 %d개"%(len(rows),len(workers),len(gone)))
'
if [ "${1:-}" != "--json" ]; then
  LM="${LRM_HUB_DIR:-$HOME/.config/lrm-hubs}/ledger_merged.py"
  if [ -f "$LM" ]; then
    if out=$(python3 -I "$LM" --apply 2>&1); then printf '%s\n' "$out" | grep -E '^(완료|후보|건너뜀)' | head -3 || true
    else echo "장부 병합 반영 실패(순찰은 계속): $(printf '%s' "$out" | tail -1)"; fi
  fi
fi
