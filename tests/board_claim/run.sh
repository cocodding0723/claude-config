#!/usr/bin/env bash
# board-claim 시험: 임시 beads 작업판만 씀. 겹침·경로 정규화·'/' 경계·동시 집기·해제 후보 경고.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
BIN="$HERE/../../board/bin/board-claim"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/bclaim-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" BD_LAST_TOUCHED_FALLBACK=0
mkdir -p "$BOARD_DIR" && cd "$BOARD_DIR" && git init -q && bd init --quiet >/dev/null 2>&1 || { echo "bd init 실패"; exit 1; }
new_card() { bd create "$1" --labels "$2" -d "${3-$'## 지시(사용자 원문 + 뒤 지시)\n- 원문: "고쳐줘"'}" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }
expect() { local name=$1 want=$2; shift 2; "$@" >"$SB/out" 2>&1; local got=$?; [ "$got" = "$want" ] && ok "$name" || ko "$name" "종료 $got(기대 $want): $(head -c 300 "$SB/out")"; }

a=$(new_card a "file:src/a/,repo:$SB/r")
expect "file: label 있으면 집음" 0 "$BIN" "$a" w1
b=$(new_card b "file:$SB/r/src/a/x.cs")
expect "절대 경로 표기도 겹침으로 거부" 3 "$BIN" "$b" w2 --repo "$SB/r"
c=$(new_card c "file:./src/ab/,repo:$SB/r")
expect "src/a/ 와 src/ab/ 는 안 겹침" 0 "$BIN" "$c" w3
d=$(new_card d "file:~/no-such-x/")
e=$(new_card e "file:$HOME/no-such-x/sub")
expect "~ 표기 집음" 0 "$BIN" "$d" w4
expect "~ 와 \$HOME 표기 겹침 거부" 3 "$BIN" "$e" w5
STALE_HOURS=0 "$BIN" "$e" w5 >/dev/null 2>"$SB/err"; grep -q '해제 후보' "$SB/err" && ok "갱신 없는 겹침 카드는 해제 후보 경고" || ko "해제 후보 경고" "$(cat "$SB/err")"
expect "이미 집힌 카드를 다른 작업자가 집으면 13" 13 "$BIN" "$a" w9
# 같은 저장소의 다른 작업 공간(worktree)·다른 clone 은 같은 파일로 봄. 다른 저장소의 같은 상대 경로는 안 겹침
G="$SB/main"; git init -q -b dev "$G" && git -C "$G" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
git -C "$G" worktree add -q "$SB/wsA" -b wa && git -C "$G" worktree add -q "$SB/wsB" -b wb
wa=$(new_card wa "file:Assets/UI/,repo:$SB/wsA"); wb=$(new_card wb "file:Assets/UI/Panel.cs,repo:$SB/wsB")
expect "작업 공간 A 카드 집음" 0 "$BIN" "$wa" wA
expect "repo: 가 다른 작업 공간이어도 같은 저장소의 같은 파일은 겹침 거부" 3 "$BIN" "$wb" wB
wc_=$(new_card wc "file:$SB/wsB/Assets/UI/Other.cs")
expect "다른 작업 공간의 절대 경로도 겹침 거부" 3 "$BIN" "$wc_" wC
O="$SB/other"; git init -q -b dev "$O"; git -C "$O" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
wo=$(new_card wo "file:Assets/UI/,repo:$O")
expect "다른 저장소의 같은 상대 경로는 안 겹침" 0 "$BIN" "$wo" wO
git clone -q "$O" "$SB/clone1" 2>/dev/null; git clone -q "$O" "$SB/clone2" 2>/dev/null
k1=$(new_card k1 "file:src/k/,repo:$SB/clone1"); k2=$(new_card k2 "file:src/k/x.cs,repo:$SB/clone2")
expect "같은 원격 clone 첫째 집음" 0 "$BIN" "$k1" wK1
expect "같은 원격 clone 두 번째는 3" 3 "$BIN" "$k2" wK2
r=$(new_card r "file:src/rel/")
expect "상대 경로인데 repo: 없으면 2" 2 "$BIN" "$r" w1

wide=$(new_card wide "file:.,repo:$G")
expect "file:. (저장소 전체) 범위는 2" 2 "$BIN" "$wide" w1

# 지시 되말하기: 사용자 원문 칸이 비면 거부
e1=$(new_card e1 "file:/abs/dir1/" "")
expect "지시 칸 없는 카드는 2" 2 "$BIN" "$e1" w1
e2=$(new_card e2 "file:/abs/dir2/" $'## 지시(사용자 원문 + 뒤 지시)\n- 원문(<날짜>): "<사용자 말 그대로>"')
expect "자리표시만 있는 지시 칸은 2" 2 "$BIN" "$e2" w1
e3=$(new_card e3 "file:/abs/dir3/" '사용자 11-16 원문: "그럼 위 구조 추천대로 반영해줘"')
expect "사용자 원문 한 줄 형식은 집음" 0 "$BIN" "$e3" w1

n=$(new_card n "")
expect "file: label 없으면 2" 2 "$BIN" "$n" w1
expect "--help 0" 0 "$BIN" --help

# 동시 집기: 같은 파일 카드 둘을 동시에 집으면 하나만 성공
p=$(new_card p "file:/abs/race/"); q=$(new_card q "file:/abs/race/f.cs")
"$BIN" "$p" r1 >/dev/null 2>&1 & p1=$!; "$BIN" "$q" r2 >/dev/null 2>&1 & p2=$!
wait "$p1"; r1=$?; wait "$p2"; r2=$?
[ $(( (r1 == 0) + (r2 == 0) )) = 1 ] && ok "동시 집기 중 하나만 성공" || ko "동시 집기" "rc $r1 $r2"

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
