#!/usr/bin/env bash
# unity-run·unity_slot.sh 시험: 종료 코드 전달·반납·사용법 오류·부하 대기 초과·동시 2개. 임시 슬롯 폴더만 씀.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
UR="$HERE/../../hubs/scripts/unity-run"; US="$HERE/../../hubs/scripts/unity_slot.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-unity-run.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export LRM_UNITY_SLOT_DIR="$SB/slots" LRM_LOADAVG_CMD='echo 1' LRM_UNITY_SLOT_SH="$US"
unset LRM_UNITY_SLOTS
export BOARD_DIR="$SB/board"; mkdir -p "$BOARD_DIR/bin"; ln -s "$HERE/../../board/bin/board-incident" "$BOARD_DIR/bin/board-incident"

bash "$UR" --name t:rc -- sh -c 'exit 7' 2>/dev/null; rc=$?
[ "$rc" = 7 ] && ok "명령 종료 코드 그대로 전달" || ko "종료 코드" "$rc"
[ -z "$(ls -d "$SB"/slots/slot-* 2>/dev/null)" ] && ok "끝나면 슬롯 반납" || ko "반납" "$(ls "$SB/slots")"

bash "$UR" --name t:x 2>/dev/null; [ $? = 2 ] && ok "명령 없으면 종료 2" || ko "명령 없음"
bash "$UR" --bogus -- true 2>/dev/null; [ $? = 2 ] && ok "모르는 인자는 종료 2" || ko "모르는 인자"
bash "$UR" --help | grep -q '사용: unity-run' && ok "--help 사용법 출력" || ko "--help"

LRM_LOADAVG_CMD='echo 99' LRM_LOAD_POLL=1 bash "$UR" --name t:load --load-timeout 1 -- true 2>"$SB/err"; rc=$?
grep -q '"kind": "unity_wait"' "$BOARD_DIR/incidents.jsonl" 2>/dev/null && ok "Unity 대기 초과는 사건 기록(원인 포함)" || ko "Unity 대기 사건 기록" "$(cat "$BOARD_DIR/incidents.jsonl" 2>/dev/null)"
[ "$rc" = 3 ] && grep -q '부하' "$SB/err" && ok "부하 대기 초과는 종료 3·원인 안내" || ko "부하 초과" "$rc $(cat "$SB/err")"

LRM_LOADAVG_CMD='echo 99' LRM_LOAD_POLL=1 bash "$UR" --name t:keep --load-timeout 1 --keep-waiting -- true 2>"$SB/err" & kp=$!
sleep 3; kill "$kp" 2>/dev/null; wait "$kp" 2>/dev/null
grep -q '계속 기다림' "$SB/err" && ok "--keep-waiting 은 대기 초과에도 경고 뒤 계속 대기" || ko "keep-waiting" "$(cat "$SB/err")"
cores=$(sysctl -n hw.ncpu); want=$(awk -v c="$cores" 'BEGIN{printf "%.1f", c*3}')
LRM_LOADAVG_CMD="echo $want" LRM_LOAD_POLL=1 bash "$UR" --name t:edge --load-timeout 1 -- true 2>/dev/null && ok "기본 기준 = 코어×3($want) 이하면 실행" || ko "기본 기준" "$want"
LRM_LOADAVG_CMD="echo $(awk -v w="$want" 'BEGIN{print w+0.5}')" LRM_LOAD_POLL=1 bash "$UR" --name t:edge2 --load-timeout 1 -- true 2>/dev/null; [ $? = 3 ] && ok "코어×3 초과면 대기" || ko "기준 초과"
st=$(bash "$US" status); case "$st" in *"slot-1 비어 있음"*) [[ "$st" == *"slot-2 비어 있음"* && "$st" != *slot-3* ]] && ok "기본 슬롯 수 2개" || ko "기본 슬롯 수" "$st";; *) ko "기본 슬롯 수" "$st";; esac

# 동시 2개: 셋을 함께 띄우면 둘은 겹치고 셋째는 하나가 끝난 뒤 시작해야 함
run_one() { bash "$UR" --name "t:$1" -- sh -c "echo start $1 \$(date +%s) >> '$SB/log'; sleep 2; echo end $1 \$(date +%s) >> '$SB/log'" 2>/dev/null; }
run_one a & p1=$!; sleep 0.5; run_one b & p2=$!; sleep 0.5; run_one c & p3=$!; wait "$p1" "$p2" "$p3"
order=$(awk '{print $1}' "$SB/log" | tr '\n' ' ')
peak=$(awk '$1=="start"{n++; if(n>m)m=n} $1=="end"{n--} END{print m+0}' "$SB/log")
[ "$peak" = 2 ] && [ "$(grep -c '^end' "$SB/log")" = 3 ] && ok "동시 실행 최대 2개" || ko "동시 실행" "$order"

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
