#!/usr/bin/env bash
# unity-run·unity_slot.sh 시험: 종료 코드 전달·반납·사용법 오류·부하 대기 초과·동시 1개. 임시 슬롯 폴더만 씀.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
UR="$HERE/../../hubs/scripts/unity-run"; US="$HERE/../../hubs/scripts/unity_slot.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-unity-run.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export LRM_UNITY_SLOT_DIR="$SB/slots" LRM_LOADAVG_CMD='echo 1' LRM_UNITY_SLOT_SH="$US"
unset LRM_UNITY_SLOTS

bash "$UR" --name t:rc -- sh -c 'exit 7' 2>/dev/null; rc=$?
[ "$rc" = 7 ] && ok "명령 종료 코드 그대로 전달" || ko "종료 코드" "$rc"
[ -z "$(ls -d "$SB"/slots/slot-* 2>/dev/null)" ] && ok "끝나면 슬롯 반납" || ko "반납" "$(ls "$SB/slots")"

bash "$UR" --name t:x 2>/dev/null; [ $? = 2 ] && ok "명령 없으면 종료 2" || ko "명령 없음"
bash "$UR" --bogus -- true 2>/dev/null; [ $? = 2 ] && ok "모르는 인자는 종료 2" || ko "모르는 인자"
bash "$UR" --help | grep -q '사용: unity-run' && ok "--help 사용법 출력" || ko "--help"

LRM_LOADAVG_CMD='echo 99' LRM_LOAD_POLL=1 bash "$UR" --name t:load --load-timeout 1 -- true 2>"$SB/err"; rc=$?
[ "$rc" = 3 ] && grep -q '부하' "$SB/err" && ok "부하 대기 초과는 종료 3·원인 안내" || ko "부하 초과" "$rc $(cat "$SB/err")"

st=$(bash "$US" status); case "$st" in *"slot-1 비어 있음"*) [[ "$st" != *slot-2* ]] && ok "기본 슬롯 수 1개" || ko "기본 슬롯 수" "$st";; *) ko "기본 슬롯 수" "$st";; esac

# 동시 1개: 두 실행의 구간이 겹치지 않아야 함
run_one() { bash "$UR" --name "t:$1" -- sh -c "echo start $1 \$(date +%s) >> '$SB/log'; sleep 2; echo end $1 \$(date +%s) >> '$SB/log'" 2>/dev/null; }
run_one a & p1=$!; sleep 0.5; run_one b & p2=$!; wait "$p1" "$p2"
order=$(awk '{print $1}' "$SB/log" | tr '\n' ' ')
[ "$order" = "start end start end " ] && ok "동시 실행 1개로 직렬화" || ko "직렬화" "$order"

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
