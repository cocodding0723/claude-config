#!/usr/bin/env bash
# board-done 시험: 임시 beads 작업판만 씀. 실제 ~/orca/board 는 건드리지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
BIN="$REPO/board/bin/board-done"
RUN="$REPO/board/bin/board-run"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/bdone-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" BD_LAST_TOUCHED_FALLBACK=0
mkdir -p "$BOARD_DIR" "$SB/ev" && cd "$BOARD_DIR" && git init -q && bd init --quiet >/dev/null 2>&1 \
  || { echo "bd init 실패"; exit 1; }

# 작업자 브랜치 w 가 있는 임시 저장소. 증거 줄은 commit=<w 끝> 로 묶음
GR="$SB/repo"; git init -q -b main "$GR" && git -C "$GR" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init && git -C "$GR" branch w
HEAD_W=$(git -C "$GR" rev-parse w); sleep 1
BL="branch:w,repo:$GR"
new_card() { bd create "$1" --labels "${2:+$2,}$BL" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }
claim() { BD_ACTOR="$2" bd update "$1" --claim --json >/dev/null; sleep 1; }
ev() { bd comments add "$1" "evidence:$2 commit=$HEAD_W" >/dev/null; }
status() { bd show "$1" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print((d[0] if isinstance(d,list) else d)["status"])'; }
# expect <이름> <기대 종료 코드> <명령...>
expect() {
  local name=$1 want=$2; shift 2
  "$@" >"$SB/out" 2>&1; local got=$?
  [ "$got" = "$want" ] && ok "$name" || ko "$name" "종료 $got(기대 $want): $(head -c 300 "$SB/out")"
}

good_compile() { (cd "$GR" && "$RUN" compile --out "$1" -- echo Compilation succeeded >/dev/null 2>&1); }
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
good_xml "$SB/ev/editmode4.xml"; bd update "$t4" --notes "evidence:$SB/ev/editmode4.xml commit=$HEAD_W" >/dev/null
expect "dry-run 통과" 0 "$BIN" "$t4" w1 --dry-run
[ "$(status "$t4")" = in_progress ] && ok "dry-run 은 닫지 않음" || ko "dry-run 은 닫지 않음"
"$BIN" "$t4" w1 --json > "$SB/j" 2>/dev/null
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d["ok"] and d["closed"] and d["kind"]=="task"' "$SB/j" \
  && ok "종류 추정·notes 증거로 닫힘(--json)" || ko "종류 추정·notes 증거로 닫힘(--json)" "$(cat "$SB/j")"
[ "$(status "$t4")" = closed ] && ok "bd 상태 closed" || ko "bd 상태 closed"
expect "이미 닫힌 카드는 0(반복 안전)" 0 "$BIN" "$t4" w1

# 증거 내용: 형식만 맞춘 로그·끊긴 실행·다른 내용·전부 건너뜀 거부
q=$(new_card "내용"); claim "$q" w1; good_xml "$SB/ev/q.xml"; ev "$q" "editmode=$SB/ev/q.xml"
printf 'Compilation succeeded\n' > "$SB/ev/hand.log"; ev "$q" "compile=$SB/ev/hand.log"
expect "손으로 쓴 한 줄 컴파일 로그 거부" 7 "$BIN" "$q" w1 --dry-run
(cd "$GR" && "$RUN" compile --out "$SB/ev/q1.log" -- false >/dev/null 2>&1); ev "$q" "compile=$SB/ev/q1.log"
expect "board-run 종료 1 로그 거부" 7 "$BIN" "$q" w1 --dry-run
(cd "$GR" && echo x > dirty.txt && "$RUN" compile --out "$SB/ev/q2.log" -- true >/dev/null 2>&1; rm -f dirty.txt); ev "$q" "compile=$SB/ev/q2.log"
expect "브랜치 끝과 다른 내용에서 돌린 로그 거부" 7 "$BIN" "$q" w1 --dry-run
good_compile "$SB/ev/q3.log"; ev "$q" "compile=$SB/ev/q3.log"
printf '<test-run>\n' > "$SB/ev/q.xml"
expect "<test-run> 한 줄 xml 거부" 7 "$BIN" "$q" w1 --dry-run
printf '<test-run total="5" passed="0" failed="0" skipped="5" result="Skipped">\n' > "$SB/ev/q.xml"
expect "전부 건너뛴 xml 거부" 7 "$BIN" "$q" w1 --dry-run
good_xml "$SB/ev/qr.xml"; (cd "$GR" && "$RUN" editmode --result "$SB/ev/qr.xml" --out "$SB/ev/qe.log" -- true >/dev/null 2>&1); ev "$q" "editmode=$SB/ev/qe.log"
expect "board-run editmode --result 로그 통과" 0 "$BIN" "$q" w1 --dry-run
printf '<test-run total="9" passed="9" failed="0">\n' > "$SB/ev/qr.xml"
expect "실행 뒤 바뀐 결과 xml 거부" 7 "$BIN" "$q" w1 --dry-run
# 시험 → 커밋 순서: 커밋 전 작업 트리에서 만든 board-run 로그도 같은 내용으로 커밋하면 인정
git -C "$GR" checkout -q w; echo v > "$GR/pre.txt"
(cd "$GR" && "$RUN" compile --out "$SB/ev/pre.log" -- true >/dev/null 2>&1; "$RUN" editmode --result "$SB/ev/qr.xml" --out "$SB/ev/pre-e.log" -- true >/dev/null 2>&1)
sleep 1; git -C "$GR" add pre.txt; git -C "$GR" -c user.name=t -c user.email=t@t commit -q -m pre; NEWW=$(git -C "$GR" rev-parse w); git -C "$GR" checkout -q main
pc=$(new_card "시험 먼저"); claim "$pc" w1
bd comments add "$pc" "evidence:compile=$SB/ev/pre.log commit=$NEWW" >/dev/null; bd comments add "$pc" "evidence:editmode=$SB/ev/pre-e.log commit=$NEWW" >/dev/null
expect "커밋 전에 만든 board-run 증거도 내용이 같으면 통과" 0 "$BIN" "$pc" w1 --dry-run
# 도구 카드(kind:tool): test 증거
tl=$(new_card "도구" kind:tool); claim "$tl" w1
expect "도구 카드 test 증거 없음 거부" 6 "$BIN" "$tl" w1 --dry-run
git -C "$GR" checkout -q w; (cd "$GR" && "$RUN" test --out "$SB/ev/tl.log" -- true >/dev/null 2>&1); git -C "$GR" checkout -q main
bd comments add "$tl" "evidence:test=$SB/ev/tl.log commit=$NEWW" >/dev/null
expect "도구 카드 test 증거 통과" 0 "$BIN" "$tl" w1 --dry-run
git -C "$GR" branch -f w "$HEAD_W"

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
e=$(bd create "묶음" -t epic --labels "$BL" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
k=$(bd create "하위" --parent "$e" --labels "$BL" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
expect "epic 을 --epic 없이 거부" 4 "$BIN" "$e" lead
expect "하위 카드 열림 거부" 4 "$BIN" --epic "$e" lead
claim "$k" w1; good_compile "$SB/ev/kc.log"; ev "$k" "compile=$SB/ev/kc.log"; good_xml "$SB/ev/kx.xml"; ev "$k" "editmode=$SB/ev/kx.xml"
"$BIN" "$k" w1 >/dev/null 2>&1
expect "촬영·녹화 없음 거부" 6 "$BIN" --epic "$e" lead
printf 'png' > "$SB/ev/s.png"; printf 'mp4' > "$SB/ev/r.mp4"; ev "$e" "$SB/ev/s.png"; ev "$e" "$SB/ev/r.mp4"
expect "검수 판정 없음 거부" 6 "$BIN" --epic "$e" lead
printf '{"verdict":"pass","reviewer_model":"gpt-6-astra","author_model":"GPT-6-astra"}' > "$SB/ev/rv.json"; ev "$e" "review=$SB/ev/rv.json"
expect "검수 모델 = 구현 모델 거부" 7 "$BIN" --epic "$e" lead
printf '{"verdict":"fail","reviewer_model":"claude-fable-5","author_model":"gpt-6-astra","reasons":"버튼 미연결"}' > "$SB/ev/rv.json"
expect "검수 불합격 판정 거부" 7 "$BIN" --epic "$e" lead
printf '{"verdict":"pass","reviewer_model":"claude-fable-5","author_model":"gpt-6-astra"}' > "$SB/ev/rv.json"
expect "epic 닫힘" 0 "$BIN" --epic "$e" lead
bd show "$e" --json | grep -q '"stage:review"' && ok "닫힌 epic 은 사용자 검수 대기 label" || ko "검수 대기 label"
expect "--user-ok 원문 없으면 2" 2 "$BIN" --epic "$e" lead --user-ok ""
expect "--user-ok 로 검수 대기 해제" 0 "$BIN" --epic "$e" lead --user-ok "좋아 반영해"
bd show "$e" --json | grep -q '"stage:review"' && ko "검수 대기 해제" || ok "검수 대기 label 떼짐"

# epic 게이트: 직접 닫은 하위 카드·board-merge 기록 없는 병합 거부
ME=$(git -C "$GR" rev-parse w)
de=$(bd create "직접 우회" -t epic --labels "branch:w,repo:$GR,base:main" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
dk=$(bd create "하위" --parent "$de" --labels "$BL" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
BD_ACTOR=w1 bd close "$dk" --reason 직접 --force >/dev/null 2>&1
printf 'png' > "$SB/ev/ds.png"; printf 'mp4' > "$SB/ev/dr.mp4"; cp "$SB/ev/rv.json" "$SB/ev/drv.json"; ev "$de" "$SB/ev/ds.png"; ev "$de" "$SB/ev/dr.mp4"; ev "$de" "review=$SB/ev/drv.json"
expect "bd close 로 직접 닫은 하위 카드가 있으면 epic 거부" 4 "$BIN" --epic "$de" lead --dry-run
bd reopen "$dk" >/dev/null 2>&1; claim "$dk" w1; good_compile "$SB/ev/dkc.log"; ev "$dk" "compile=$SB/ev/dkc.log"; good_xml "$SB/ev/dkx.xml"; ev "$dk" "editmode=$SB/ev/dkx.xml"
"$BIN" "$dk" w1 >/dev/null 2>&1
git -C "$GR" checkout -q -b side main; git -C "$GR" -c user.name=t -c user.email=t@t commit -q --allow-empty -m side
git -C "$GR" checkout -q w; git -C "$GR" -c user.name=t -c user.email=t@t merge -q --no-ff -m direct side; git -C "$GR" checkout -q main
HEAD_W=$(git -C "$GR" rev-parse w)
for f in ds.png dr.mp4; do touch "$SB/ev/$f"; done; touch "$SB/ev/drv.json"
ev "$de" "$SB/ev/ds.png"; ev "$de" "$SB/ev/dr.mp4"; ev "$de" "review=$SB/ev/drv.json"
expect "통합 브랜치에 board-merge 기록 없는 병합이 있으면 epic 거부" 4 "$BIN" --epic "$de" lead --dry-run
bd comments add "$dk" "board-merge: $HEAD_W side → w" >/dev/null
expect "병합이 board-merge 로 기록돼 있으면 epic 통과" 0 "$BIN" --epic "$de" lead --dry-run
git -C "$GR" branch -f w "$ME"; HEAD_W=$ME

# 작업 공간 반납: 카드 repo: 폴더가 없으면 epic repo: 에서 브랜치를 읽음
ge=$(bd create "반납" -t epic --labels "branch:w,repo:$GR" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
gk=$(bd create "반납 하위" --parent "$ge" --labels "branch:w,repo:$SB/gone-ws" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'); claim "$gk" w1
good_compile "$SB/ev/gk.log"; ev "$gk" "compile=$SB/ev/gk.log"; good_xml "$SB/ev/gk.xml"; ev "$gk" "editmode=$SB/ev/gk.xml"
expect "반납된 작업 공간 카드도 epic 저장소로 검사" 0 "$BIN" "$gk" w1 --dry-run

# 커밋 묶기
g=$(new_card "커밋"); claim "$g" w1
good_compile "$SB/ev/g.log"; good_xml "$SB/ev/g.xml"
bd comments add "$g" "evidence:compile=$SB/ev/g.log" >/dev/null; ev "$g" "editmode=$SB/ev/g.xml"
expect "commit= 없는 증거 거부" 6 "$BIN" "$g" w1
git -C "$GR" -c user.name=t -c user.email=t@t commit -q --allow-empty -m next; git -C "$GR" branch -f w HEAD
ev "$g" "compile=$SB/ev/g.log"
expect "브랜치가 앞으로 가면 이전 커밋 증거 거부" 6 "$BIN" "$g" w1
git -C "$GR" branch -f w "$HEAD_W"
h=$(bd create "label 없음" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'); claim "$h" w1
expect "branch:·repo: label 없으면 거부" 6 "$BIN" "$h" w1

# 게이트가 직접 컴파일
git -C "$GR" checkout -q w
gc=$(new_card "게이트 컴파일"); claim "$gc" w1
good_compile "$SB/ev/gc.log"; ev "$gc" "compile=$SB/ev/gc.log"; good_xml "$SB/ev/gc.xml"; ev "$gc" "editmode=$SB/ev/gc.xml"
expect "게이트 컴파일 오류면 작업자 로그가 깨끗해도 거부" 7 env BOARD_COMPILE_CMD='echo "A.cs(1,1): error CS0103: x"' "$BIN" "$gc" w1 --dry-run
expect "게이트 컴파일 명령 실패도 거부" 7 env BOARD_COMPILE_CMD='exit 9' "$BIN" "$gc" w1 --dry-run
expect "게이트 컴파일 통과" 0 env BOARD_COMPILE_CMD='echo Compilation succeeded' "$BIN" "$gc" w1 --dry-run
printf '{"%s": {"compile": "exit 3"}}\n' "$GR" > "$BOARD_DIR/repos.json"
expect "repos.json 저장소 compile 설정으로 게이트 컴파일(실패 거부)" 7 "$BIN" "$gc" w1 --dry-run
printf '{"%s": {"compile": "echo ok"}}\n' "$GR" > "$BOARD_DIR/repos.json"
expect "repos.json 저장소 compile 설정 통과" 0 "$BIN" "$gc" w1 --dry-run
rm -f "$BOARD_DIR/repos.json"
"$BIN" "$gc" w1 --dry-run >"$SB/out" 2>&1; grep -q '게이트 컴파일 설정 없음' "$SB/out" && ok "컴파일 설정 없으면 경고" || ko "컴파일 설정 없음 경고"
ls "$BOARD_DIR"/.evidence/"$gc"-compile-*.log >/dev/null 2>&1 && ok "게이트 컴파일 로그를 작업판에 남김" || ko "게이트 컴파일 로그"

# epic 닫히면 기능 코디네이터 정리 실행
printf '#!/bin/bash\necho "$@" > "%s/fc.args"\n' "$SB" > "$SB/fc.sh"
fe=$(bd create "기능 묶음" -t epic --labels "feature:demo,$BL" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
sleep 1; printf 'png' > "$SB/ev/s2.png"; printf 'mp4' > "$SB/ev/r2.mp4"; cp "$SB/ev/rv.json" "$SB/ev/rv2.json"; ev "$fe" "$SB/ev/s2.png"; ev "$fe" "$SB/ev/r2.mp4"; ev "$fe" "review=$SB/ev/rv2.json"
expect "feature: epic 닫힘" 0 env BOARD_FEATURE_CLOSE="$SB/fc.sh" "$BIN" --epic "$fe" lead
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$SB/fc.args" ] && break; sleep 0.3; done
[ "$(cat "$SB/fc.args" 2>/dev/null)" = "--slug demo --yes" ] && ok "epic 닫힘 뒤 feature_close --slug --yes 실행" || ko "feature_close 연결" "$(cat "$SB/fc.args" 2>/dev/null)"

expect "인자 부족은 2" 2 "$BIN" onlyid
expect "--help 는 0" 0 "$BIN" --help
expect "없는 카드는 4" 4 "$BIN" nope-1 w1
expect "작업판 폴더 없음은 3" 3 env BOARD_DIR="$SB/none" "$BIN" x w1

echo "통과 $PASS, 실패 $FAIL"; [ "$FAIL" = 0 ]
