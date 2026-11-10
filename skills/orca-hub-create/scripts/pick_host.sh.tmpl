#!/usr/bin/env bash
# 새 워커를 어디서 띄울지 판정: local | remote. 근거를 stderr 로 출력
set -uo pipefail
REMOTE="${REMOTE_ENV:-REMOTE_MAC_HOST}"
NCPU=$(sysctl -n hw.ncpu)
LOAD=$(sysctl -n vm.loadavg | awk '{print $2}')
FREE=$(memory_pressure 2>/dev/null | awk -F': ' '/free percentage/{gsub("%","",$2);print $2}')
UNITY=$(pgrep -f 'Unity.app/Contents/MacOS/Unity' | wc -l | tr -d ' ')
HEAVY="${1:-}"   # 'unity' 를 주면 Unity 작업
reason="load=$LOAD/$NCPU free=${FREE:-?}% unity=$UNITY"
busy=0
awk -v l="$LOAD" -v n="$NCPU" 'BEGIN{exit !(l>=n)}' && busy=1
[ -n "${FREE:-}" ] && [ "$FREE" -lt 20 ] && busy=1
[ "$HEAVY" = unity ] && [ "$UNITY" -ge 1 ] && busy=1
if [ "$busy" = 1 ] && orca --environment "$REMOTE" status --json 2>/dev/null | grep -q '"reachable": true'; then
  echo "remote"; echo "$reason → 원격($REMOTE)" >&2
else
  echo "local"; echo "$reason → 로컬" >&2
fi
