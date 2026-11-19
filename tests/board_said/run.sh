#!/usr/bin/env bash
# board-said 시험: 임시 beads 작업판과 임시 세션 기록 폴더만 씀. 글 판정 경우는 tests/board_flow/test_said.py 의 표, 여기는 도구가 카드에 실제로 적는 것만 봄.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
BIN="$HERE/../../board/bin/board-said"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/bsaid-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" BOARD_CLAUDE_DIRS="$SB/claude" BD_LAST_TOUCHED_FALLBACK=0
WS="/w/coord-feat-x-hub"; PROJ="$SB/claude/projects/-w-coord-feat-x-hub"
mkdir -p "$BOARD_DIR" "$PROJ" && cd "$BOARD_DIR" && git init -q && bd init --quiet >/dev/null 2>&1 || { echo "bd init 실패"; exit 1; }
expect() { local name=$1 want=$2; shift 2; "$@" >"$SB/out" 2>&1; local got=$?; [ "$got" = "$want" ] && ok "$name" || ko "$name" "종료 $got(기대 $want): $(head -c 300 "$SB/out")"; }
notes() { bd comments "$1" --json | jq -r '.[].text'; }
say() { # say <몇 초 뒤> <글> : 사용자가 친 글 한 줄을 세션 기록에 더함
  python3 -c 'import json,sys,datetime,time; at=time.time()+float(sys.argv[1]); print(json.dumps({"type":"user","timestamp":datetime.datetime.fromtimestamp(at,datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),"message":{"role":"user","content":sys.argv[2]}},ensure_ascii=False))' "$1" "$2" >> "$PROJ/s.jsonl"
}

E=$(bd create "[LRM-1] 불꽃 연출" -t epic --labels "feature:x" -d $'- 목표: 불꽃\n- 기능 코디네이터 허브: '"$WS" --json | jq -r .id)
N=$(bd create "허브 줄 없는 기능" -t epic --json | jq -r .id)
T=$(bd create "하위 작업" --parent "$E" --json | jq -r .id)
expect "친 글이 없으면 --check 통과" 0 "$BIN" "$E" --check
say 10 "높이 조금만 더 낮추자"; say 20 "이거 왜 이렇게 했어?"; say 30 "승인"
printf '%s\n' '{"type":"user","timestamp":"2099-01-01T00:00:00.000Z","message":{"role":"user","content":"You have 1 orchestration message. Run `orca orchestration check --run r`."}}' >> "$PROJ/s.jsonl"
expect "장부에 없는 글이 있으면 --check 는 6" 6 "$BIN" "$E" --check
expect "--sync 는 작업자 이름이 필요(2)" 2 "$BIN" "$E" --sync
expect "--sync 로 새 글을 장부에 적음" 0 "$BIN" "$E" co --sync
[ "$(notes "$E" | grep -c '^지시 #')" = 2 ] && notes "$E" | grep -q '^지시 #1 (.*): 높이 조금만 더 낮추자 \[said:[0-9]*\]$' && ok "원문 그대로 번호를 붙여 적음(승인 글·알림 줄은 빼고 2건)" || ko "장부 기록" "$(notes "$E")"
expect "--sync 를 다시 해도 같은 글을 또 적지 않음" 0 "$BIN" "$E" co --sync
[ "$(notes "$E" | grep -c '^지시 #')" = 2 ] && ok "다시 실행해도 2건 그대로" || ko "중복 기록" "$(notes "$E")"
expect "처리가 안 적혔으면 --check 는 6" 6 "$BIN" "$E" --check
grep -q '#1, #2' "$SB/out" && ok "거부 안내에 처리 안 된 번호" || ko "거부 안내" "$(cat "$SB/out")"
expect "모르는 처리 종류는 2" 2 "$BIN" "$E" co --mark 1 완료 "근거"
expect "장부에 없는 번호는 2" 2 "$BIN" "$E" co --mark 9 반영 "근거"
expect "--mark 로 처리 기록" 0 "$BIN" "$E" co --mark 1 반영 "카드 t-1.5, 커밋 e59c842"
expect "되물음은 아직 미처리(6)" 6 "$BIN" "$E" co --mark 2 되물음 "어느 쪽인지 물음" --check
expect "답함으로 바꾸면 --check 통과" 0 "$BIN" "$E" co --mark 2 답함 "원인 설명함" --check
"$BIN" "$E" --json >"$SB/out" 2>&1
[ "$(jq -r '[.count, .unrecorded, (.unsettled|length), .rows[0].state, .rows[1].state] | map(tostring) | join(",")' "$SB/out")" = "2,0,0,반영,답함" ] && ok "--json 조회" || ko "json" "$(cat "$SB/out")"
say 40 "불꽃을 VS HUD 앞으로"
expect "새 글이 생기면 다시 6" 6 "$BIN" "$E" --check
expect "여러 번호를 한 번에 처리" 0 "$BIN" "$E" co --sync --mark 3 반영 "카드 t-1.4"
expect "전부 처리되면 통과" 0 "$BIN" "$E" --check
expect "코디네이터 폴더 줄이 없는 기능은 --sync 거부(2)" 2 "$BIN" "$N" co --sync
expect "기능 카드가 아니면 2" 2 "$BIN" "$T" --check
expect "없는 카드는 2" 2 "$BIN" "no-such" --check

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
