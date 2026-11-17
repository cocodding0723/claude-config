#!/usr/bin/env bash
# board-claim 시험: 임시 beads 작업판만 씀. 겹침·경로 정규화·'/' 경계·동시 집기·해제 후보 경고.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
BIN="$HERE/../../board/bin/board-claim"
BEAT="$HERE/../../board/bin/board-beat"
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

# 검수 대기 상한: stage:review 3개면 새 착수만 거부
for i in 1 2 3; do bd create "rv$i" -t epic --labels stage:review --json >/dev/null; done
rvp=$(bd list --label stage:review --json | python3 -c 'import json,sys;print(json.load(sys.stdin)[0]["id"])')
nw=$(new_card nw "file:/abs/new1/")
expect "검수 대기 3개면 새 착수 14" 14 "$BIN" "$nw" w1
expect "이미 집은 카드 다시 집기는 계속" 0 "$BIN" "$a" w1
fx=$(bd create fx --parent "$rvp" --labels "file:/abs/fix1/" -d '사용자 원문: "고쳐"' --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
expect "검수 대기 기능의 수정 카드는 집음" 0 "$BIN" "$fx" w1
expect "상한을 올리면 집음" 0 env REVIEW_WIP_MAX=4 "$BIN" "$nw" w1
for id in $(bd list --label stage:review --json | python3 -c 'import json,sys;print(" ".join(x["id"] for x in json.load(sys.stdin) if x.get("issue_type")=="epic"))'); do bd label remove "$id" stage:review >/dev/null; done

n=$(new_card n "")
expect "file: label 없으면 2" 2 "$BIN" "$n" w1
expect "--help 0" 0 "$BIN" --help

# 동시 집기: 같은 파일 카드 둘을 동시에 집으면 하나만 성공
p=$(new_card p "file:/abs/race/"); q=$(new_card q "file:/abs/race/f.cs")
"$BIN" "$p" r1 >/dev/null 2>&1 & p1=$!; "$BIN" "$q" r2 >/dev/null 2>&1 & p2=$!
wait "$p1"; r1=$?; wait "$p2"; r2=$?
[ $(( (r1 == 0) + (r2 == 0) )) = 1 ] && ok "동시 집기 중 하나만 성공" || ko "동시 집기" "rc $r1 $r2"

# heartbeat: board-beat 는 담당만 갱신하고 메모는 metadata 에 남김(코멘트 안 늘림). 집기·다시 집기도 갱신 시각을 찍음
meta() { bd show "$1" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);d=d[0] if isinstance(d,list) else d;print((d.get("metadata") or {}).get(sys.argv[1],""))' "$2"; }
field() { bd show "$1" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);d=d[0] if isinstance(d,list) else d;print(d.get(sys.argv[1],""))' "$2"; }
h=$(new_card h "file:/abs/beat/")
expect "집지 않은 카드 heartbeat 는 13" 13 "$BEAT" "$h" w1
expect "집기" 0 "$BIN" "$h" w1
t0=$(field "$h" heartbeat_at); sleep 1
expect "담당 heartbeat 는 0" 0 "$BEAT" "$h" w1 "시험 작성 중"
t1=$(field "$h" heartbeat_at)
[ -n "$t0" ] && [ "$t1" \> "$t0" ] && ok "heartbeat 가 갱신 시각을 앞으로 보냄" || ko "heartbeat 시각" "$t0 → $t1"
[ "$(meta "$h" beat_note)" = "시험 작성 중" ] && [ "$(bd show "$h" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);d=d[0] if isinstance(d,list) else d;print(d.get("comment_count",0))')" = 0 ] && ok "한 줄 메모는 metadata 에, 코멘트는 안 만듦" || ko "메모 저장" "$(meta "$h" beat_note)"
expect "다른 작업자 heartbeat 는 13" 13 "$BEAT" "$h" w2
$BEAT "$h" w2 >/dev/null 2>"$SB/err"; grep -q '조치' "$SB/err" && ok "heartbeat 거부 문구에 조치" || ko "거부 문구" "$(cat "$SB/err")"
expect "반복 실행 안전(메모 없이도 0)" 0 "$BEAT" "$h" w1
$BEAT "$h" w1 "$(printf 'a%.0s' $(seq 1 200))
둘째 줄" >/dev/null; nl=$(meta "$h" beat_note); [ "$(printf %s "$nl" | python3 -c 'import sys;print(len(sys.stdin.read()))')" = 80 ] && [ "$(printf %s "$nl" | wc -l | tr -d ' ')" = 0 ] && ok "메모는 한 줄 80자로 자름" || ko "메모 길이" ""
expect "증거 형식 오류는 2" 2 "$BEAT" "$h" w1 --evidence "te st=zzz"
expect "카드 없음은 2" 2 "$BEAT" no-such-card w1
expect "--help 0" 0 "$BEAT" --help
expect "인자 없음 2" 2 "$BEAT"
t2=$(field "$h" heartbeat_at); sleep 1
expect "이미 집은 카드 다시 집기도 성공" 0 "$BIN" "$h" w1
t3=$(field "$h" heartbeat_at)
[ "$t3" \> "$t2" ] && ok "다시 집기가 heartbeat 를 갱신" || ko "다시 집기 갱신" "$t2 → $t3"
# 해제 후보 판정: updated_at 이 오래돼도 heartbeat 가 최근이면 낡지 않음
python3 - "$HERE/../../board/bin/board-claim" <<'PY' && ok "age_hours 는 heartbeat_at 을 반영" || ko "age_hours" ""
import importlib.machinery, importlib.util, sys
from datetime import datetime, timedelta, timezone
m = importlib.machinery.SourceFileLoader("bc", sys.argv[1]).load_module()
f = lambda d: (datetime.now(timezone.utc) - timedelta(hours=d)).strftime("%Y-%m-%dT%H:%M:%SZ")
assert m.age_hours({"updated_at": f(5), "heartbeat_at": f(0.1)}) < 0.2
assert 4.9 < m.age_hours({"updated_at": f(5)}) < 5.1
assert m.age_hours({}) == 0.0
PY

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
