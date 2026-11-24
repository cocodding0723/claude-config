#!/usr/bin/env bash
# board-ready 시험: 임시 beads 작업판만 씀. 의존 미완료·서로 겹침·진행 중 겹침·file 표시 없음·epic 아님.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
BIN="$HERE/../../board/bin/board-ready"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/bready-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" BD_LAST_TOUCHED_FALLBACK=0
mkdir -p "$BOARD_DIR" && cd "$BOARD_DIR" && git init -q && bd init --quiet >/dev/null 2>&1 || { echo "bd init 실패"; exit 1; }
id() { python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }
expect() { local name=$1 want=$2; shift 2; "$@" >"$SB/out" 2>&1; local got=$?; [ "$got" = "$want" ] && ok "$name" || ko "$name" "종료 $got(기대 $want): $(head -c 300 "$SB/out")"; }
# 목록 이름·id 로 JSON 결과 검사: in <ready|waiting|needs_files> <id> → 있으면 0
in_() { python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));sys.exit(0 if sys.argv[3] in [x["id"] for x in d[sys.argv[2]]] else 1)' "$SB/json" "$1" "$2"; }

ep=$(bd create 기능 -t epic --labels branch:feat/f --json | id)
kid() { bd create "$1" --parent "$2" -p "$3" ${4:+--labels "$4"} --json | id; }
a=$(kid a "$ep" 1 "file:/abs/x/")          # ready
b=$(kid b "$ep" 2 "file:/abs/x/b.cs")      # a 와 겹침 → 기다림
c=$(kid c "$ep" 1 "file:/abs/y/")          # 의존 d 미완료 → 제외
d=$(kid d "$ep" 1 "file:/abs/z/")          # ready
bd dep add "$c" "$d" >/dev/null
n=$(kid n "$ep" 1 "")                      # file 표시 없음
g=$(kid g "$n" 1 "file:/abs/g/")           # 손자, ready(bd 가 부모 label 을 물려주므로 file 없는 부모 아래)
p=$(bd create "다른 일" --labels "file:/abs/busy/" --json | id); bd update "$p" --status in_progress >/dev/null
q=$(kid q "$ep" 0 "file:/abs/busy/q.cs")   # 진행 중 p 와 겹침

expect "정상 실행 0" 0 "$BIN" "$ep" --json
cp "$SB/out" "$SB/json"
in_ ready "$a" && in_ ready "$d" && ok "의존 없는 카드는 ready" || ko "ready" "$(cat "$SB/json")"
in_ ready "$g" && ok "손자 카드도 ready" || ko "손자" ""
! in_ ready "$c" && ! in_ waiting "$c" && ok "의존 미완료 카드는 제외" || ko "의존 미완료" ""
in_ waiting "$b" && ! in_ ready "$b" && ok "서로 겹치는 두 카드 중 하나만 ready" || ko "서로 겹침" ""
in_ waiting "$q" && grep -q "$p" "$SB/json" && ok "진행 중 카드와 겹치면 기다림" || ko "진행 중 겹침" ""
in_ needs_files "$n" && grep -q "bd label add $n file:" "$SB/json" && ok "file 표시 없으면 표시 필요 + 명령" || ko "표시 필요" ""
grep -q "feat/f-${a##*.}" "$SB/json" && grep -q "board-claim $a" "$SB/json" && ok "브랜치 제안·집기 명령" || ko "명령" ""

expect "사람용 출력 0" 0 "$BIN" "$ep"
grep -q "기다림: $b .*$a" "$SB/out" && grep -q "create_worker_ws.sh" "$SB/out" && ok "사람용 출력에 기다림·작업 공간 명령" || ko "사람용" "$(cat "$SB/out")"

bd close "$d" >/dev/null
"$BIN" "$ep" --json >"$SB/json" 2>&1; in_ ready "$c" && ok "의존 닫히면 ready" || ko "의존 닫힘" ""

expect "epic 아님은 2" 2 "$BIN" "$a"
grep -q '거부:.*조치:' "$SB/out" && ok "거부 문구에 조치" || ko "거부 문구" "$(cat "$SB/out")"
expect "카드 없음은 2" 2 "$BIN" no-such-1
expect "인자 없음 2" 2 "$BIN"
expect "--help 0" 0 "$BIN" --help
BOARD_DIR="$SB/none" expect "작업판 폴더 없음 4" 4 "$BIN" "$ep"

echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
