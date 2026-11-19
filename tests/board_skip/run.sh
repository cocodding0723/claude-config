#!/usr/bin/env bash
# board-skip 시험: 임시 beads 작업판과 임시 세션 기록 폴더만 씀. 글 판정 경우는 tests/board_flow/test_skip.py 의 표, 여기는 도구가 카드에 실제로 적는 것과 집기·단계 도구가 그것을 읽는 것만 봄.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
BIN="$HERE/../../board/bin/board-skip"
STAGE="$HERE/../../board/bin/board-stage"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/bskip-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" BOARD_CLAUDE_DIRS="$SB/claude" LRM_HUB_DIR="$SB/hub" BD_LAST_TOUCHED_FALLBACK=0
WS="/w/coord-feat-x-hub"; PROJ="$SB/claude/projects/-w-coord-feat-x-hub"
mkdir -p "$BOARD_DIR" "$PROJ" "$LRM_HUB_DIR" && cd "$BOARD_DIR" && git init -q && bd init --quiet >/dev/null 2>&1 || { echo "bd init 실패"; exit 1; }
expect() { local name=$1 want=$2; shift 2; "$@" >"$SB/out" 2>&1; local got=$?; [ "$got" = "$want" ] && ok "$name" || ko "$name" "종료 $got(기대 $want): $(head -c 300 "$SB/out")"; }
labels() { bd show "$1" --json | jq -r '(if type=="array" then .[0] else . end).labels // [] | map(select(startswith("skip:"))) | sort | join(",")'; }
notes() { bd comments "$1" --json | jq -r '.[].text'; }
say() { # say <몇 초 뒤> <글> : 사용자가 친 글 한 줄을 세션 기록에 더함
  python3 -c 'import json,sys,datetime,time; at=time.time()+float(sys.argv[1]); print(json.dumps({"type":"user","timestamp":datetime.datetime.fromtimestamp(at,datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),"message":{"role":"user","content":sys.argv[2]}},ensure_ascii=False))' "$1" "$2" >> "$PROJ/s.jsonl"
}
DESC=$'- 목표: 불꽃이 오른다\n- 확인 방법: 화면 캡처\n- 기능 코디네이터 허브: '"$WS"

E=$(bd create "[LRM-1] 불꽃 연출" -t epic --labels "feature:x,flow:v1,stage:intake" -d "$DESC" --json | jq -r .id)
T=$(bd create "하위 작업" --parent "$E" --json | jq -r .id)
expect "건너뛴 것이 없으면 없음이라고 보여 줌" 0 "$BIN" "$E"
grep -q '건너뛰기: 없음' "$SB/out" && ok "조회 출력" || ko "조회 출력" "$(cat "$SB/out")"
expect "근거 없이 항목만 주면 거부(2)" 2 "$BIN" "$E" co 급함
grep -q -- '--user-said' "$SB/out" && grep -q -- '--sync' "$SB/out" && ok "거부 안내가 근거 주는 법 두 가지를 알려 줌" || ko "근거 안내" "$(cat "$SB/out")"
expect "건너뛸 수 없는 항목은 거부(2)" 2 "$BIN" "$E" co 다른모델검수 --user-said "검수 건너뛰어"
grep -q '준비' "$SB/out" && ok "거부 안내가 건너뛸 수 있는 범위를 알려 줌" || ko "범위 안내" "$(cat "$SB/out")"
expect "모르는 항목은 거부(2)" 2 "$BIN" "$E" co 아무거나 --user-said "x"
expect "작업자 이름 없이 바꾸면 거부(2)" 2 "$BIN" "$E" --sync
[ -z "$(labels "$E")" ] && ok "거부된 호출은 카드를 바꾸지 않음" || ko "거부 뒤 label" "$(labels "$E")"

expect "사용자 원문과 함께 급함을 걸면 둘 다 걸림" 0 "$BIN" "$E" co 급함 --user-said "/orca-feature skip x 급함"
[ "$(labels "$E")" = "skip:plan,skip:understand" ] && ok "카드에 건너뛰기 표시 둘이 붙음" || ko "label" "$(labels "$E")"
notes "$E" | grep -q '^건너뛰기: 이해, 계획 (사용자 원문: /orca-feature skip x 급함)$' && ok "건너뛴 항목과 사용자 원문이 코멘트로 남음" || ko "코멘트" "$(notes "$E")"
expect "같은 항목을 다시 걸어도 성공(바뀌는 것 없음)" 0 "$BIN" "$E" co 계획 --user-said "다시"
[ "$(notes "$E" | grep -c '^건너뛰기: ')" = 1 ] && ok "이미 걸린 항목은 코멘트를 더 남기지 않음" || ko "중복 코멘트" "$(notes "$E")"
expect "건너뛴 기능은 계획 승인 없이 접수에서 구현으로" 0 "$STAGE" "$E" build co --size normal
"$STAGE" "$E" >"$SB/out" 2>&1; grep -q '건너뛰기: 이해, 계획' "$SB/out" && ok "단계 조회가 건너뛴 것을 보여 줌" || ko "단계 조회" "$(cat "$SB/out")"
expect "이미 지난 항목의 취소는 바꾸지 않고 알려 줌" 0 "$BIN" "$E" co 계획 --undo
grep -q '이미 지난 단계' "$SB/out" && [ "$(labels "$E")" = "skip:plan,skip:understand" ] && ok "지난 항목은 그대로" || ko "지난 항목 취소" "$(cat "$SB/out") / $(labels "$E")"

# 취소: 아직 지나지 않은 항목
U=$(bd create "[LRM-2] 취소" -t epic --labels "feature:u,flow:v1,stage:intake" -d "$DESC" --json | jq -r .id)
"$BIN" "$U" co 급함 --user-said "급함" >/dev/null 2>&1
expect "아직 지나지 않은 항목은 취소됨" 0 "$BIN" "$U" co 계획 --undo
[ "$(labels "$U")" = "skip:understand" ] && notes "$U" | grep -q '^건너뛰기 취소: 계획$' && ok "취소한 항목만 떼고 코멘트를 남김" || ko "취소" "$(labels "$U") / $(notes "$U")"
expect "계획 건너뛰기를 취소하면 다시 계획 승인이 필요(15)" 15 "$STAGE" "$U" build co --size normal
expect "--undo 에 항목이 없으면 거부(2)" 2 "$BIN" "$U" co --undo

# --sync: 코디네이터 창에 친 글을 읽어 적음
S=$(bd create "[LRM-3] 창에서" -t epic --labels "feature:s,flow:v1,stage:intake" -d "$DESC" --json | jq -r .id)
say 5 "높이 조금만 낮추자"; say 10 "급함 아님, 천천히 해"
expect "건너뛰기 글이 없으면 --sync 는 아무것도 걸지 않음" 0 "$BIN" "$S" co --sync
[ -z "$(labels "$S")" ] && ok "다른 말이 붙은 급함은 건너뛰기로 읽지 않음" || ko "오독" "$(labels "$S")"
say 20 "급함"
expect "창에 급함이라고 치면 --sync 가 둘 다 검" 0 "$BIN" "$S" co --sync
[ "$(labels "$S")" = "skip:plan,skip:understand" ] && notes "$S" | grep -q '^건너뛰기: 이해, 계획 (사용자 원문: 급함) \[skip:[0-9]*\]$' && ok "원문과 시각 표시가 코멘트로 남음" || ko "sync 기록" "$(labels "$S") / $(notes "$S")"
"$BIN" "$S" co 계획 --undo >/dev/null 2>&1
expect "취소한 뒤 다시 --sync 해도 같은 글로 다시 걸리지 않음" 0 "$BIN" "$S" co --sync
[ "$(labels "$S")" = "skip:understand" ] && ok "이미 적은 글은 다시 읽지 않음" || ko "재적용" "$(labels "$S")"
say 30 "스킵 다른모델검수"
"$BIN" "$S" co --sync >"$SB/out" 2>&1; RC=$?
[ "$RC" = 0 ] && grep -q '건너뛸 수 없는 것' "$SB/out" && grep -q '다른모델검수' "$SB/out" && [ "$(labels "$S")" = "skip:understand" ] && ok "건너뛸 수 없는 것을 건너뛰라는 글은 걸지 않고 알려 줌" || ko "거절 안내" "rc=$RC $(cat "$SB/out")"
"$BIN" "$S" --json >"$SB/out" 2>&1
[ "$(jq -r '.skips | join(",")' "$SB/out")" = "understand" ] && ok "--json 조회" || ko "json" "$(cat "$SB/out")"

N=$(bd create "허브 줄 없는 기능" -t epic --json | jq -r .id)
expect "코디네이터 폴더 줄이 없으면 --sync 는 2" 2 "$BIN" "$N" co --sync
expect "기능 카드가 아니면 2" 2 "$BIN" "$T"
expect "없는 카드는 2" 2 "$BIN" "no-such-card"

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
