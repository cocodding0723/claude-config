#!/usr/bin/env bash
# board-done 시험: 임시 beads 작업판만 씀. 실제 ~/orca/board 는 건드리지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
BIN="$REPO/board/bin/board-done"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/bdone-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" BD_LAST_TOUCHED_FALLBACK=0
mkdir -p "$BOARD_DIR" "$SB/ev" && cd "$BOARD_DIR" && git init -q && bd init --quiet >/dev/null 2>&1 \
  || { echo "bd init 실패"; exit 1; }

new_card() { bd create "$1" ${2:+--labels "$2"} --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }
claim() { BD_ACTOR="$2" bd update "$1" --claim --json >/dev/null; sleep 1; }
ev() { bd comments add "$1" "evidence:$2" >/dev/null; }
status() { bd show "$1" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print((d[0] if isinstance(d,list) else d)["status"])'; }
# expect <이름> <기대 종료 코드> <명령...>
expect() {
  local name=$1 want=$2; shift 2
  "$@" >"$SB/out" 2>&1; local got=$?
  [ "$got" = "$want" ] && ok "$name" || ko "$name" "종료 $got(기대 $want): $(head -c 300 "$SB/out")"
}

good_compile() { printf 'Compilation succeeded\n' > "$1"; }
good_xml() { printf '<?xml version="1.0"?>\n<test-run id="2" total="3" passed="3" failed="0" result="Passed">\n</test-run>\n' > "$1"; }
map_row() { printf '| PSD 레이어 | 프리팹 노드 경로 | 값(위치·크기·색) | 연결 기능(코드 클래스·메서드) | 데이터 출처 |\n|---|---|---|---|---|\n| 예) a | b | c | d | e |\n| 상단/코인 | Canvas/Coin | (1,2) | %s | 지갑 |\n' "$2" > "$1"; }

# 일반 카드
t=$(new_card "일반" kind:task)
expect "집지 않은 카드 거부" 4 "$BIN" "$t" w1
claim "$t" w1
expect "증거 없음 거부" 6 "$BIN" "$t" w1
expect "작업자 불일치 거부" 5 "$BIN" "$t" w2
touch -t 202001010000 "$SB/ev/old.log"; printf 'x' > "$SB/ev/old.log"; touch -t 202001010000 "$SB/ev/old.log"
ev "$t" "compile=$SB/ev/old.log"
good_xml "$SB/ev/t.xml"; ev "$t" "editmode=$SB/ev/t.xml"
expect "claim 이전 증거 거부" 6 "$BIN" "$t" w1
: > "$SB/ev/empty.log"; ev "$t" "compile=$SB/ev/empty.log"
expect "빈 파일 거부" 6 "$BIN" "$t" w1

t2=$(new_card "일반2"); claim "$t2" w1
printf 'Foo.cs(3,1): error CS1002: ; expected\n' > "$SB/ev/bad.log"; ev "$t2" "compile=$SB/ev/bad.log"
good_xml "$SB/ev/t2.xml"; ev "$t2" "editmode=$SB/ev/t2.xml"
expect "컴파일 오류 로그 거부" 7 "$BIN" "$t2" w1
t3=$(new_card "일반3"); claim "$t3" w1
good_compile "$SB/ev/c3.log"; ev "$t3" "compile=$SB/ev/c3.log"
printf '<test-run total="2" failed="1" result="Failed(Child)"></test-run>\n' > "$SB/ev/f.xml"; ev "$t3" "editmode=$SB/ev/f.xml"
expect "EditMode 실패 거부" 7 "$BIN" "$t3" w1

t4=$(new_card "일반4"); claim "$t4" w1
good_compile "$SB/ev/compile4.log"; ev "$t4" "$SB/ev/compile4.log"
good_xml "$SB/ev/editmode4.xml"; bd update "$t4" --notes "evidence:$SB/ev/editmode4.xml" >/dev/null
expect "dry-run 통과" 0 "$BIN" "$t4" w1 --dry-run
[ "$(status "$t4")" = in_progress ] && ok "dry-run 은 닫지 않음" || ko "dry-run 은 닫지 않음"
"$BIN" "$t4" w1 --json > "$SB/j" 2>/dev/null
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d["ok"] and d["closed"] and d["kind"]=="task"' "$SB/j" \
  && ok "종류 추정·notes 증거로 닫힘(--json)" || ko "종류 추정·notes 증거로 닫힘(--json)" "$(cat "$SB/j")"
[ "$(status "$t4")" = closed ] && ok "bd 상태 closed" || ko "bd 상태 closed"
expect "이미 닫힌 카드는 0(반복 안전)" 0 "$BIN" "$t4" w1

# 시안 카드
m=$(new_card "시안" kind:mockup); claim "$m" w1
good_compile "$SB/ev/mc.log"; ev "$m" "compile=$SB/ev/mc.log"
good_xml "$SB/ev/mx.xml"; ev "$m" "editmode=$SB/ev/mx.xml"
expect "시안 카드 대응표 없음 거부" 6 "$BIN" "$m" w1
map_row "$SB/ev/map.md" "미정"; ev "$m" "mapping=$SB/ev/map.md"
printf '{"mismatches":0}\n' > "$SB/ev/pd.json"; ev "$m" "prefab-diff=$SB/ev/pd.json"
expect "연결 기능 빈 칸 거부" 7 "$BIN" "$m" w1
"$BIN" "$m" w1 --json > "$SB/j" 2>/dev/null
grep -q '연결 기능 빈 행 1개: 상단/코인' "$SB/j" && ok "원인에 빈 행 이름" || ko "원인에 빈 행 이름" "$(cat "$SB/j")"
map_row "$SB/ev/map.md" "WalletPresenter.OnCoinChanged"
printf '{"mismatches":[{"node":"Canvas/Coin"}]}\n' > "$SB/ev/pd.json"
expect "프리팹 값 불일치 거부" 7 "$BIN" "$m" w1
printf '{"mismatches":0}\n' > "$SB/ev/pd.json"
expect "시안 카드 닫힘" 0 "$BIN" "$m" w1

# epic
e=$(bd create "묶음" -t epic --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
k=$(bd create "하위" --parent "$e" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
expect "epic 을 --epic 없이 거부" 4 "$BIN" "$e" lead
expect "하위 카드 열림 거부" 4 "$BIN" --epic "$e" lead
claim "$k" w1; good_compile "$SB/ev/kc.log"; ev "$k" "compile=$SB/ev/kc.log"; good_xml "$SB/ev/kx.xml"; ev "$k" "editmode=$SB/ev/kx.xml"
"$BIN" "$k" w1 >/dev/null 2>&1
expect "촬영·녹화 없음 거부" 6 "$BIN" --epic "$e" lead
printf 'png' > "$SB/ev/s.png"; printf 'mp4' > "$SB/ev/r.mp4"; ev "$e" "$SB/ev/s.png"; ev "$e" "$SB/ev/r.mp4"
expect "epic 닫힘" 0 "$BIN" --epic "$e" lead

expect "인자 부족은 2" 2 "$BIN" onlyid
expect "--help 는 0" 0 "$BIN" --help
expect "없는 카드는 4" 4 "$BIN" nope-1 w1
expect "작업판 폴더 없음은 3" 3 env BOARD_DIR="$SB/none" "$BIN" x w1

echo "통과 $PASS, 실패 $FAIL"; [ "$FAIL" = 0 ]
