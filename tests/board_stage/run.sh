#!/usr/bin/env bash
# board-stage 시험: 임시 beads 작업판과 임시 기록 폴더만 씀. 판정 경우는 tests/board_flow 의 표, 여기는 도구가 카드에 실제로 적는 것만 봄.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
BIN="$HERE/../../board/bin/board-stage"
LIB="$HERE/../../board/lib"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/bstage-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" LRM_HUB_DIR="$SB/hub" BD_LAST_TOUCHED_FALLBACK=0
mkdir -p "$BOARD_DIR" "$LRM_HUB_DIR" && cd "$BOARD_DIR" && git init -q && bd init --quiet >/dev/null 2>&1 || { echo "bd init 실패"; exit 1; }
expect() { local name=$1 want=$2; shift 2; "$@" >"$SB/out" 2>&1; local got=$?; [ "$got" = "$want" ] && ok "$name" || ko "$name" "종료 $got(기대 $want): $(head -c 300 "$SB/out")"; }
labels() { bd show "$1" --json | jq -r '(if type=="array" then .[0] else . end).labels // [] | sort | join(",")'; }
planid() { python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import board_flow; print(board_flow.plan_id(sys.argv[2]))' "$LIB" "$1"; }

E=$(bd create "[LRM-1] 결말 개선" -t epic --labels "feature:end,flow:v1" --json | jq -r .id)
expect "접수 칸이 비면 이해로 못 넘어감(2)" 2 "$BIN" "$E" understand co
grep -q '목표' "$SB/out" && grep -q '확인 방법' "$SB/out" && ok "거부 안내가 비어 있는 칸을 알려 줌" || ko "접수 안내" "$(cat "$SB/out")"
bd update "$E" -d $'- 목표: 마지막 날 시작해도 결말이 열린다\n- 확인 방법: 마지막 날 세이브로 시작해 그 장면이 열리는 캡처\n- 범위 밖: 다른 결말' >/dev/null
expect "크기를 안 정하면 못 넘어감(2)" 2 "$BIN" "$E" understand co
expect "--size 로 크기를 정하며 이해로" 0 "$BIN" "$E" understand co --size normal --note "기획 대조 시작"
[ "$(labels "$E")" = "feature:end,flow:v1,size:normal,stage:understand" ] && ok "카드에 크기와 단계 label 이 붙음" || ko "label" "$(labels "$E")"
bd comments "$E" --json | jq -r '.[].text' | grep -q '단계: 1 이해 — 기획 대조 시작' && ok "단계가 바뀐 것과 한 줄 메모가 코멘트로 남음" || ko "코멘트" "$(bd comments "$E" --json | jq -r '.[].text')"
expect "번호로도 옮김(2 = 설계)" 0 "$BIN" "$E" 2 co
[ "$(labels "$E")" = "feature:end,flow:v1,size:normal,stage:design" ] && ok "단계 label 은 하나만 남음" || ko "단계 하나" "$(labels "$E")"
expect "계획 승인 전에는 구현으로 못 넘어감(15)" 15 "$BIN" "$E" build co
grep -q "$(planid "$E")" "$SB/out" && ok "거부 안내에 올릴 계획 승인 결정 번호가 있음" || ko "승인 안내" "$(cat "$SB/out")"
printf '{"decision_id":"%s","ts":"9.1","form_kind":"decide","options":{"1":"승인","2":"수정 요청"}}\n' "$(planid "$E")" > "$LRM_HUB_DIR/slack-sent.jsonl"
printf '{"type":"button","decision_id":"%s","parent_ts":"9.1","action":"pick","pick":1,"source":"slack"}\n' "$(planid "$E")" > "$LRM_HUB_DIR/slack-inbox.jsonl"
expect "사용자가 승인하면 구현으로" 0 "$BIN" "$E" 구현 co
expect "검수 단계는 손으로 못 옮김(2)" 2 "$BIN" "$E" review co
# 크기를 낮추면 계획 승인을 건너뛰게 되므로 코디네이터 혼자 못 낮춤. 사용자 원문을 함께 줄 때만
expect "보통에서 작음으로 낮추기는 사용자 원문 없이 거부(2)" 2 "$BIN" "$E" build co --size small
grep -q -- '--user-said' "$SB/out" && ok "거부 안내가 낮추는 법을 알려 줌" || ko "낮추기 안내" "$(cat "$SB/out")"
expect "사용자 원문을 함께 주면 낮춤" 0 "$BIN" "$E" build co --size small --user-said "이건 문구만 바꾸는 거라 작게 가자"
bd comments "$E" --json | jq -r '.[].text' | grep -q '크기 변경: 보통 → 작음 (사용자 원문: 이건 문구만 바꾸는 거라 작게 가자)' && ok "크기를 낮춘 근거가 코멘트로 남음" || ko "낮춘 근거" "$(bd comments "$E" --json | jq -r '.[].text' | tail -2)"
expect "작음에서 보통으로 올리기는 그냥 됨" 0 "$BIN" "$E" build co --size normal
"$BIN" "$E" >"$SB/out" 2>&1; RC=$?
[ "$RC" = 0 ] && grep -q '3 구현' "$SB/out" && grep -q '보통' "$SB/out" && grep -q '계획 승인: 됨' "$SB/out" && ok "단계만 물으면 지금 단계·크기·계획 승인 여부를 보여 줌" || ko "조회" "rc=$RC $(cat "$SB/out")"
"$BIN" "$E" --json >"$SB/out" 2>&1
[ "$(jq -r '[.stage,.size,(.plan_approved|tostring),.plan_id]|join(",")' "$SB/out")" = "build,normal,true,$(planid "$E")" ] && ok "--json 조회" || ko "json" "$(cat "$SB/out")"
bd label add "$E" stage:review >/dev/null 2>&1; bd label remove "$E" stage:build >/dev/null 2>&1
expect "검수 대기 중인 기능의 단계는 건드리지 않음(2)" 2 "$BIN" "$E" build co
S=$(bd create "[LRM-2] 문구 수정" -t epic --labels "feature:txt,flow:v1" -d $'- 목표: 문구 오타가 고쳐진다\n- 확인 방법: 화면 캡처' --json | jq -r .id)
expect "작은 일은 접수에서 바로 구현으로" 0 "$BIN" "$S" build co --size small
expect "모르는 단계는 2" 2 "$BIN" "$S" coding co
expect "모르는 크기는 2" 2 "$BIN" "$S" build co --size huge
expect "없는 카드는 2" 2 "$BIN" "no-such-card" build co
T=$(bd create "하위 작업" --parent "$S" --json | jq -r .id)
expect "기능 카드가 아니면 2" 2 "$BIN" "$T" build co

# 사용자가 코디네이터 창에 직접 친 승인·건너뛰기: Slack 버튼 없이도 구현으로 넘어감(글 판정 경우는 tests/board_flow/test_said.py)
export BOARD_CLAUDE_DIRS="$SB/claude"; TDIR="$SB/claude/projects/-w-coord-feat-typed-hub"; mkdir -p "$TDIR"
typed() { python3 -c 'import json,sys,datetime,time; print(json.dumps({"type":"user","timestamp":datetime.datetime.fromtimestamp(time.time()+float(sys.argv[1]),datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),"message":{"role":"user","content":sys.argv[2]}},ensure_ascii=False))' "$1" "$2" >> "$TDIR/s.jsonl"; }
Y=$(bd create "[LRM-3] 창에서 승인" -t epic --labels "feature:typed,flow:v1" -d $'- 목표: 창에서 승인한다\n- 확인 방법: 단계가 구현으로 넘어간다\n- 기능 코디네이터 허브: /w/coord-feat-typed-hub' --json | jq -r .id)
expect "창 승인: 설계까지는 그냥 감" 0 "$BIN" "$Y" design co --size normal
typed 5 "작업 빠르게 진행 ㄱㄱ"
expect "'진행 ㄱㄱ'만으로는 구현으로 못 넘어감(15)" 15 "$BIN" "$Y" build co
grep -q "스킵" "$SB/out" && ok "거부 안내가 창에서 승인·스킵하는 길을 알려 줌" || ko "창 승인 안내" "$(cat "$SB/out")"
typed 10 "스킵하고 진행"
expect "사용자가 창에 '스킵하고 진행'이라고 치면 구현으로" 0 "$BIN" "$Y" build co
unset BOARD_CLAUDE_DIRS

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
