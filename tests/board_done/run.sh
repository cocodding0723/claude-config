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

# 작업자 브랜치 w 가 있는 임시 저장소(Unity 프로젝트 표지 포함: label 없는 카드가 일반 카드로 판정됨). 증거 줄은 commit=<w 끝> 로 묶음
GR="$SB/repo"; git init -q -b main "$GR" && mkdir -p "$GR/ProjectSettings" && : > "$GR/ProjectSettings/ProjectVersion.txt" && git -C "$GR" add -A && git -C "$GR" -c user.name=t -c user.email=t@t commit -q -m init && git -C "$GR" branch w
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
bd show "$e" --json | grep -q '"stage:ship"' && ok "검수 통과 뒤에는 반영 단계 label" || ko "반영 단계 label"

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
expect "commit= 없는 board-run 로그는 로그에 적힌 커밋으로 통과" 0 "$BIN" "$g" w1 --dry-run
bd comments add "$g" "evidence:compile=$SB/ev/g.log commit=$HEAD_W (error CS 0건, 슬롯 조정 뒤 다시 돌림)" >/dev/null
expect "증거 줄 끝의 괄호 메모는 경로로 읽지 않음" 0 "$BIN" "$g" w1 --dry-run
git -C "$GR" -c user.name=t -c user.email=t@t commit -q --allow-empty -m next; git -C "$GR" branch -f w HEAD
expect "commit= 이 없어도 브랜치가 앞으로 가면 로그의 이전 커밋으로 거부" 6 "$BIN" "$g" w1
grep -q '낡은 증거' "$SB/out" && ok "거부 사유는 낡은 증거(형식 아님)" || ko "거부 사유" "$(head -c 300 "$SB/out")"
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

# 기능 코디네이터는 검수와 반영이 끝날 때까지 남음: 닫힘 → 검수 통과(--user-ok) → 반영 끝(--shipped) 순서의 마지막에만 정리 실행
printf '#!/bin/bash\necho "$@" > "%s/fc.args"\n' "$SB" > "$SB/fc.sh"
fe=$(bd create "기능 묶음" -t epic --labels "feature:demo,$BL" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
sleep 1; printf 'png' > "$SB/ev/s2.png"; printf 'mp4' > "$SB/ev/r2.mp4"; cp "$SB/ev/rv.json" "$SB/ev/rv2.json"; ev "$fe" "$SB/ev/s2.png"; ev "$fe" "$SB/ev/r2.mp4"; ev "$fe" "review=$SB/ev/rv2.json"
expect "feature: epic 닫힘" 0 env BOARD_FEATURE_CLOSE="$SB/fc.sh" "$BIN" --epic "$fe" lead
sleep 1.5
[ ! -e "$SB/fc.args" ] && ok "epic 이 닫혀도 코디네이터 정리는 아직 실행하지 않음(검수 답을 받을 세션 유지)" || ko "검수 전 정리 실행됨" "$(cat "$SB/fc.args" 2>/dev/null)"
expect "--shipped 는 검수 통과 전에는 거부(4)" 4 env BOARD_FEATURE_CLOSE="$SB/fc.sh" "$BIN" --epic "$fe" lead --shipped "PR #9 병합"
expect "feature: 검수 통과 기록" 0 env BOARD_FEATURE_CLOSE="$SB/fc.sh" "$BIN" --epic "$fe" lead --user-ok "통과"
sleep 1.5
[ ! -e "$SB/fc.args" ] && ok "검수 통과만으로는 정리하지 않음(PR·병합이 남음)" || ko "검수 통과에 정리 실행됨" "$(cat "$SB/fc.args" 2>/dev/null)"
expect "--shipped 근거 없으면 2" 2 env BOARD_FEATURE_CLOSE="$SB/fc.sh" "$BIN" --epic "$fe" lead --shipped ""
expect "feature: 반영 끝 기록" 0 env BOARD_FEATURE_CLOSE="$SB/fc.sh" "$BIN" --epic "$fe" lead --shipped "PR #9 병합(abc1234)"

# 창에 친 검수 통과(--user-ok-typed): 완료 검사 뒤 코디네이터 창에 친 '통과'를 세션 기록에서 읽어 기록. Slack 카드 답을 기다리지 않음
TW="$(cd "$SB" && pwd -P)/coord-feat-typed-hub"; TP="$SB/cfg/projects/$(printf '%s' "$TW" | sed 's/[^A-Za-z0-9]/-/g')"; mkdir -p "$TP"
typed_line() { python3 -c 'import json,sys,datetime;print(json.dumps({"type":"user","timestamp":datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),"message":{"role":"user","content":sys.argv[1]}},ensure_ascii=False))' "$1" >> "$TP/s.jsonl"; }
te=$(bd create "창 통과 기능" -t epic --labels "$BL" -d "- 기능 코디네이터 허브: $TW" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
typed_line "통과"   # 완료 검사 전에 친 글은 세지 않음
sleep 1; printf 'png' > "$SB/ev/s3.png"; printf 'mp4' > "$SB/ev/r3.mp4"; cp "$SB/ev/rv.json" "$SB/ev/rv3.json"; printf 'png' > "$SB/ev/old.png"; touch -t 202001010000 "$SB/ev/old.png"
bd comments add "$te" "evidence:$SB/ev/old.png" >/dev/null; bd comments add "$te" "evidence:$SB/ev/r3.mp4" >/dev/null; bd comments add "$te" "evidence:review=$SB/ev/rv3.json" >/dev/null
expect "commit= 없는 촬영 파일이 브랜치 끝 커밋보다 오래되면 거부" 6 env BOARD_CLAUDE_DIRS="$SB/cfg" "$BIN" --epic "$te" lead --dry-run
bd comments add "$te" "evidence:$SB/ev/s3.png" >/dev/null
expect "typed: epic 닫힘(commit= 없는 촬영·녹화·판정 증거)" 0 env BOARD_CLAUDE_DIRS="$SB/cfg" "$BIN" --epic "$te" lead
grep -q -- '--user-ok-typed' "$SB/out" && ok "typed: 닫을 때 창 입력으로 통과하는 법을 알려 줌" || ko "typed: 안내 줄 없음" "$(cat "$SB/out")"
expect "typed: 완료 검사 뒤에 친 통과가 없으면 4" 4 env BOARD_CLAUDE_DIRS="$SB/cfg" "$BIN" --epic "$te" lead --user-ok-typed
expect "typed: --user-ok 와 같이 쓰면 2" 2 env BOARD_CLAUDE_DIRS="$SB/cfg" "$BIN" --epic "$te" lead --user-ok-typed --user-ok "통과"
sleep 1; typed_line "토스트는 나오나?"; typed_line "통과 ㄱㄱ"
expect "typed: 창에 친 통과로 검수 통과 기록" 0 env BOARD_CLAUDE_DIRS="$SB/cfg" "$BIN" --epic "$te" lead --user-ok-typed
bd show "$te" --json | grep -q '"stage:ship"' && ok "typed: 반영 단계 label" || ko "typed: 반영 단계 label 없음"
bd comments "$te" 2>/dev/null | grep -q 'user-ok: (창에 친 글) 통과 ㄱㄱ' && ok "typed: 사용자 원문을 카드에 남김" || ko "typed: 원문 코멘트 없음" "$(bd comments "$te" 2>&1 | tail -3)"
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$SB/fc.args" ] && break; sleep 0.3; done
[ "$(cat "$SB/fc.args" 2>/dev/null)" = "--slug demo --yes" ] && ok "반영이 끝나면 feature_close --slug --yes 실행" || ko "feature_close 연결" "$(cat "$SB/fc.args" 2>/dev/null)"
bd show "$fe" --json | grep -q '"stage:ship"' && ko "반영 단계 label 남음" || ok "반영이 끝나면 단계 label 을 뗌"
bd comments "$fe" --json | grep -q 'shipped: PR #9 병합(abc1234)' && ok "반영 근거가 코멘트로 남음" || ko "반영 근거 코멘트"

# board-run --card: 성공·커밋된 트리면 증거 커밋을 metadata 에 남기고 heartbeat 갱신. 실패·미커밋은 증거 안 남김. 시험 종료 코드는 그대로
bm=$(new_card "beat 연결"); claim "$bm" wb
meta_of() { bd show "$1" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);d=d[0] if isinstance(d,list) else d;print((d.get("metadata") or {}).get(sys.argv[1],""))' "$2"; }
hb_of() { bd show "$1" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);d=d[0] if isinstance(d,list) else d;print(d.get("heartbeat_at",""))'; }
git -C "$GR" checkout -q w; h0=$(hb_of "$bm"); sleep 1
(cd "$GR" && "$RUN" test --out "$SB/ev/bm1.log" --card "$bm" --actor wb -- true >/dev/null 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(meta_of "$bm" evidence_test)" = "$HEAD_W" ] && [ "$(hb_of "$bm")" \> "$h0" ] && ok "board-run --card: 성공 시 증거 커밋·heartbeat 기록" || ko "board-run --card 성공" "rc=$rc ev=$(meta_of "$bm" evidence_test)"
bd comments "$bm" 2>/dev/null | tr -d '\n' | tr -s ' ' | grep -q "evidence:test=.*bm1.log commit=$HEAD_W" && ok "board-run --card: 증거 줄을 카드 코멘트에도 남김(옮겨 달 필요 없음)" || ko "board-run --card 증거 줄" "$(bd comments "$bm" 2>&1 | tail -4)"
bf=$(new_card "beat 실패"); claim "$bf" wb
(cd "$GR" && "$RUN" test --out "$SB/ev/bm2.log" --card "$bf" --actor wb -- false >/dev/null 2>&1); rc=$?
[ "$rc" = 1 ] && [ -z "$(meta_of "$bf" evidence_test)" ] && ok "board-run --card: 시험 실패는 증거 안 남기고 종료 코드 유지" || ko "board-run --card 실패" "rc=$rc"
bd comments "$bf" 2>/dev/null | grep -q "evidence:test=" && ko "board-run --card: 실패한 시험의 증거 줄이 남음" || ok "board-run --card: 실패한 시험은 증거 줄도 남기지 않음"
bd2=$(new_card "beat 미커밋"); claim "$bd2" wb
(cd "$GR" && echo x > dirty.txt && "$RUN" test --out "$SB/ev/bm3.log" --card "$bd2" --actor wb -- true >/dev/null 2>&1; rm -f dirty.txt)
[ -z "$(meta_of "$bd2" evidence_test)" ] && ok "board-run --card: 커밋 안 된 트리는 증거 안 남김" || ko "미커밋 증거" ""
(cd "$GR" && "$RUN" test --out "$SB/ev/bm4.log" --card "$bm" -- true >"$SB/out" 2>&1); rc=$?
[ "$rc" = 0 ] && grep -q '경고: --card 에는 --actor' "$SB/out" && ok "board-run --card 만 주면 경고하고 시험은 그대로" || ko "--actor 누락" "rc=$rc"
(cd "$GR" && "$RUN" test --out "$SB/ev/bm5.log" --card "$bm" --actor intruder -- true >"$SB/out" 2>&1); rc=$?
[ "$rc" = 0 ] && grep -q '경고' "$SB/out" && ok "담당 아닌 --actor 는 경고만, 시험 종료 코드 유지" || ko "담당 아님" "rc=$rc $(cat "$SB/out")"
git -C "$GR" checkout -q main

expect "인자 부족은 2" 2 "$BIN" onlyid
expect "--help 는 0" 0 "$BIN" --help
expect "없는 카드는 4" 4 "$BIN" nope-1 w1
expect "작업판 폴더 없음은 3" 3 env BOARD_DIR="$SB/none" "$BIN" x w1

# 화면에 보이는 변화가 없는 기능은 캡처·녹화 생략(--no-capture). 화면 파일이 바뀌었으면 거부하고 바로 찍게 함
gitc() { git -C "$GR" -c user.name=t -c user.email=t@t "$@"; }
gitc checkout -q -b logic main; echo 'class A {}' > "$GR/A.cs"; gitc add -A; gitc commit -q -m logic; HEAD_L=$(git -C "$GR" rev-parse HEAD)
gitc checkout -q -b vis main; mkdir -p "$GR/Assets"; echo p > "$GR/Assets/a.prefab"; gitc add -A; gitc commit -q -m vis; HEAD_V=$(git -C "$GR" rev-parse HEAD); gitc checkout -q main
epic_of() { bd create "$1" -t epic --labels "$2" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }
nc=$(epic_of "화면 변화 없음" "branch:logic,repo:$GR,base:main"); nv=$(epic_of "화면 변화 있음" "branch:vis,repo:$GR,base:main"); nb=$(epic_of "기준 모름" "branch:logic,repo:$GR")
sleep 1; for n in 5 6 7; do cp "$SB/ev/rv.json" "$SB/ev/rv$n.json"; done
bd comments add "$nc" "evidence:review=$SB/ev/rv5.json commit=$HEAD_L" >/dev/null; bd comments add "$nv" "evidence:review=$SB/ev/rv6.json commit=$HEAD_V" >/dev/null; bd comments add "$nb" "evidence:review=$SB/ev/rv7.json commit=$HEAD_L" >/dev/null
expect "캡처 증거가 없으면 그냥은 거부(6)" 6 "$BIN" --epic "$nc" lead --dry-run
expect "--no-capture 이유 없으면 2" 2 "$BIN" --epic "$nc" lead --no-capture "" --dry-run
expect "화면 파일이 안 바뀐 기능은 --no-capture 로 통과" 0 "$BIN" --epic "$nc" lead --no-capture "계산식만 바뀜" --dry-run
expect "화면 파일이 바뀐 기능은 --no-capture 거부(7)" 7 "$BIN" --epic "$nv" lead --no-capture "간단함" --dry-run
grep -q 'a.prefab' "$SB/out" && grep -q '묻지 않고 바로' "$SB/out" && ok "거부 안내가 바뀐 화면 파일과 '묻지 않고 바로 찍기'를 알려 줌" || ko "캡처 안내" "$(cat "$SB/out")"
expect "기준 브랜치를 모르면 --no-capture 거부(6)" 6 "$BIN" --epic "$nb" lead --no-capture "계산식만 바뀜" --dry-run
expect "--no-capture 로 닫으면 통과" 0 env BOARD_FEATURE_CLOSE=off "$BIN" --epic "$nc" lead --no-capture "계산식만 바뀜"
bd comments "$nc" --json | grep -q 'no-capture: 계산식만 바뀜' && ok "캡처 생략 이유가 카드에 남음" || ko "캡처 생략 이유"

# 지시 장부: 사용자가 코디네이터 창에 직접 친 지시의 처리가 다 적혀야 기능을 닫음(label said:v1 인 기능만)
SAID="$HERE/../../board/bin/board-said"; SDIR="$SB/claude/projects/-w-coord-feat-s-hub"; mkdir -p "$SDIR"
sd=$(bd create "지시 장부" -t epic --labels "branch:logic,repo:$GR,base:main,said:v1" -d $'- 목표: x\n- 기능 코디네이터 허브: /w/coord-feat-s-hub' --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
sleep 1; cp "$SB/ev/rv.json" "$SB/ev/rv8.json"; bd comments add "$sd" "evidence:review=$SB/ev/rv8.json commit=$HEAD_L" >/dev/null
expect "직접 친 지시가 없으면 통과" 0 env BOARD_CLAUDE_DIRS="$SB/claude" "$BIN" --epic "$sd" lead --no-capture "계산식만" --dry-run
python3 -c 'import json,datetime,time,sys; open(sys.argv[1],"w").write(json.dumps({"type":"user","timestamp":datetime.datetime.fromtimestamp(time.time()+5,datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),"message":{"role":"user","content":"높이 조금만 더 낮추자"}},ensure_ascii=False)+"\n")' "$SDIR/s.jsonl"
expect "장부에 없는 지시가 있으면 거부(6)" 6 env BOARD_CLAUDE_DIRS="$SB/claude" "$BIN" --epic "$sd" lead --no-capture "계산식만" --dry-run
grep -q 'board-said' "$SB/out" && ok "거부 안내가 지시 장부 명령을 알려 줌" || ko "장부 안내" "$(cat "$SB/out")"
BOARD_CLAUDE_DIRS="$SB/claude" "$SAID" "$sd" lead --sync >/dev/null 2>&1
expect "장부에 적었어도 처리가 없으면 거부(6)" 6 env BOARD_CLAUDE_DIRS="$SB/claude" "$BIN" --epic "$sd" lead --no-capture "계산식만" --dry-run
BOARD_CLAUDE_DIRS="$SB/claude" "$SAID" "$sd" lead --mark 1 반영 "카드 x.5" >/dev/null 2>&1
expect "지시마다 처리가 적히면 통과" 0 env BOARD_CLAUDE_DIRS="$SB/claude" "$BIN" --epic "$sd" lead --no-capture "계산식만" --dry-run

# 잡파일만 다른 tree: 편집기가 만든 .slnx·evidence/ 폴더는 커밋에 없는 새 파일이어도 같은 내용으로 봄. 코드 파일이 섞이면 거부
G2="$SB/repo2"; git init -q -b main "$G2" && printf 'a\n' > "$G2/Code.cs" && printf 's\n' > "$G2/Settings.asset" \
  && git -C "$G2" add -A && git -C "$G2" -c user.name=t -c user.email=t@t commit -q -m init && git -C "$G2" branch n && git -C "$G2" checkout -q n
tcard() { bd create "$1" --labels "kind:tool,branch:n,repo:$G2" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }
n1=$(tcard "잡파일"); claim "$n1" wn
mkdir -p "$G2/evidence/c1" && printf 'png' > "$G2/evidence/c1/shot.png" && printf 'x' > "$G2/ws.slnx"
(cd "$G2" && "$RUN" test --out "$SB/ev/n1.log" --card "$n1" --actor wn -- true >/dev/null 2>&1)
[ -n "$(meta_of "$n1" evidence_test)" ] && ok "board-run: 잡파일(.slnx·evidence/)만 있으면 커밋된 내용으로 보고 증거를 카드에 남김" || ko "잡파일인데 증거 안 남김" "$(head -1 "$SB/ev/n1.log")"
expect "잡파일만 다른 증거는 통과" 0 "$BIN" "$n1" wn --dry-run
n2=$(tcard "코드 섞임"); claim "$n2" wn
printf 'b\n' > "$G2/New.cs"
(cd "$G2" && "$RUN" test --out "$SB/ev/n2.log" -- true >/dev/null 2>&1); rm -f "$G2/New.cs"
bd comments add "$n2" "evidence:test=$SB/ev/n2.log" >/dev/null
expect "커밋 안 된 코드 파일이 섞인 증거는 거부" 7 "$BIN" "$n2" wn --dry-run
grep -q '달라진 파일: New.cs' "$SB/out" && ok "거부 안내에 달라진 파일 이름" || ko "달라진 파일 안내 없음" "$(head -c 300 "$SB/out")"
n3=$(tcard "추적 파일 수정"); claim "$n3" wn
printf 'changed-by-tool\n' > "$G2/Settings.asset"
(cd "$G2" && "$RUN" test --out "$SB/ev/n3.log" -- true >/dev/null 2>&1); git -C "$G2" checkout -q -- Settings.asset
bd comments add "$n3" "evidence:test=$SB/ev/n3.log" >/dev/null
expect "추적 파일 수정은 기본으로는 거부" 7 "$BIN" "$n3" wn --dry-run
printf '{"%s": {"evidence_ignore": ["Settings.asset"]}}' "$G2" > "$BOARD_DIR/repos.json"
expect "저장소 설정(evidence_ignore)에 있는 파일의 수정은 통과" 0 "$BIN" "$n3" wn --dry-run
printf 'c\n' > "$G2/Code.cs"
(cd "$G2" && "$RUN" test --out "$SB/ev/n4.log" -- true >/dev/null 2>&1); git -C "$G2" checkout -q -- Code.cs
bd comments add "$n3" "evidence:test=$SB/ev/n4.log" >/dev/null
expect "설정에 없는 코드 수정은 여전히 거부" 7 "$BIN" "$n3" wn --dry-run
rm -f "$BOARD_DIR/repos.json"

# 카드 종류 자동 판정: kind label 이 없으면 브랜치 끝이 Unity 프로젝트인지로 가림. G2 는 표지(ProjectSettings/ProjectVersion.txt)가 없는 도구 저장소
ucard() { bd create "$1" --labels "${2}branch:n,repo:$G2" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }
u1=$(ucard "label 없는 도구 저장소 카드" ""); claim "$u1" wn
expect "label 없는 도구 저장소 카드: 증거 없으면 거부" 6 "$BIN" "$u1" wn --dry-run
grep -q 'evidence:test 줄 없음' "$SB/out" && ! grep -q 'editmode\|compile' "$SB/out" && ok "label 없는 도구 저장소 카드: Unity 증거가 아니라 test 증거를 요구" || ko "도구 저장소 요구 증거" "$(head -c 300 "$SB/out")"
(cd "$G2" && "$RUN" test --out "$SB/ev/u1.log" --card "$u1" --actor wn -- true >/dev/null 2>&1)
expect "label 없는 도구 저장소 카드: test 증거로 통과" 0 "$BIN" "$u1" wn --dry-run
u2=$(ucard "kind:task 로 적은 카드" "kind:task,"); claim "$u2" wn
expect "kind:task 를 적으면 도구 저장소여도 일반 카드" 6 "$BIN" "$u2" wn --dry-run
grep -q 'evidence:editmode 줄 없음' "$SB/out" && ok "kind:task: 컴파일·EditMode 증거 요구" || ko "kind:task 요구 증거" "$(head -c 300 "$SB/out")"

echo "통과 $PASS, 실패 $FAIL"; [ "$FAIL" = 0 ]
