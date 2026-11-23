#!/usr/bin/env bash
# board-merge 시험. 훅이 하나도 없는 환경(빈 HOME·전역 git 설정 없음 = Codex 작업자와 같은 조건)에서
# 범위 밖·증거 없음·충돌·검증 실패는 통합 브랜치를 바꾸지 못하고, 통과분만 병합·완료됨을 보임.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
BIN="$HERE/../../board/bin/board-merge"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/bmerge-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
mkdir -p "$SB/home"
export HOME="$SB/home" GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null BD_LAST_TOUCHED_FALLBACK=0 BOARD_FEATURE_CLOSE=off
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export BOARD_DIR="$SB/board"; mkdir -p "$BOARD_DIR" "$SB/ev"
(cd "$BOARD_DIR" && git init -q && bd init --quiet >/dev/null 2>&1) || { echo "bd init 실패"; exit 1; }
[ ! -e "$HOME/.claude" ] && ok "준비: Claude 훅 없는 환경" || ko "준비" "훅 폴더 있음"

R="$SB/repo"; git init -q -b feat "$R"; echo base > "$R/base.txt"; git -C "$R" add .; git -C "$R" commit -q -m init
into() { git -C "$R" rev-parse feat; }
card() { # card <제목> <브랜치> <file label>
  (cd "$BOARD_DIR" && bd create "$1" --labels "file:$3,branch:$2,repo:$R,base:feat" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
}
claim() { (cd "$BOARD_DIR" && BD_ACTOR="$2" bd update "$1" --claim --json >/dev/null); sleep 1; }
evidence() { # evidence <카드> <브랜치>
  local h; h=$(git -C "$R" rev-parse "$2")
  printf '<test-run total="1" passed="1" failed="0" result="Passed"></test-run>\n' > "$SB/ev/$1.xml"
  git -C "$R" checkout -q "$2"; (cd "$R" && "$HERE/../../board/bin/board-run" compile --out "$SB/ev/$1.log" -- echo ok >/dev/null 2>&1); git -C "$R" checkout -q feat
  (cd "$BOARD_DIR" && bd comments add "$1" "evidence:compile=$SB/ev/$1.log commit=$h" >/dev/null && bd comments add "$1" "evidence:editmode=$SB/ev/$1.xml commit=$h" >/dev/null)
}
branch() { # branch <이름> <파일> <내용>
  git -C "$R" branch "$1" feat; git -C "$R" checkout -q "$1"; mkdir -p "$R/$(dirname "$2")"; echo "$3" > "$R/$2"
  git -C "$R" add .; git -C "$R" commit -q -m "$1"; git -C "$R" checkout -q feat
}
expect() { local name=$1 want=$2; shift 2; "$@" >"$SB/out" 2>&1; local got=$?; [ "$got" = "$want" ] && ok "$name" || ko "$name" "종료 $got(기대 $want): $(tail -c 300 "$SB/out")"; }
st() { (cd "$BOARD_DIR" && bd show "$1" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print((d[0] if isinstance(d,list) else d)["status"])'); }
unchanged() { [ "$(into)" = "$1" ] && ok "$2: 통합 브랜치 그대로" || ko "$2: 통합 브랜치 바뀜"; }

branch w-out src/b/x.cs 1; c1=$(card 범위밖 w-out src/a/); claim "$c1" w1; evidence "$c1" w-out
before=$(into); expect "범위 밖 변경은 4" 4 "$BIN" "$c1" w1 --repo "$R"; unchanged "$before" "범위 밖"

branch w-noev src/a/x.cs 1; c2=$(card 증거없음 w-noev src/a/); claim "$c2" w1
before=$(into); expect "증거 없으면 6" 6 "$BIN" "$c2" w1 --repo "$R"; unchanged "$before" "증거 없음"

branch w-cf src/a/v.cs 2   # 아래 w-vf 병합 뒤 같은 파일 충돌용으로 미리 만듦
branch w-vf src/a/v.cs 1; c3=$(card 검증실패 w-vf src/a/); claim "$c3" w1; evidence "$c3" w-vf
before=$(into); expect "검증 실패는 8" 8 env BOARD_MERGE_VERIFY='exit 1' "$BIN" "$c3" w1 --repo "$R"; unchanged "$before" "검증 실패"
[ -z "$(git -C "$R" status --porcelain)" ] && ok "검증 실패 뒤 작업 트리 깨끗" || ko "작업 트리" "$(git -C "$R" status --porcelain)"

expect "통과분은 병합·완료" 0 env BOARD_MERGE_VERIFY='test -f src/a/v.cs' "$BIN" "$c3" w1 --repo "$R"
git -C "$R" merge-base --is-ancestor w-vf feat && [ "$(git -C "$R" rev-list --parents -n1 feat | wc -w | tr -d ' ')" = 3 ] && ok "--no-ff 병합 커밋" || ko "병합 커밋"
st=$(cd "$BOARD_DIR" && bd show "$c3" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print((d[0] if isinstance(d,list) else d)["status"])')
[ "$st" = closed ] && ok "병합 뒤 카드 닫힘" || ko "카드 상태" "$st"
before=$(into); expect "같은 카드 다시 실행은 0(반복 안전)" 0 "$BIN" "$c3" w1 --repo "$R"; unchanged "$before" "다시 실행"

c4=$(card 충돌 w-cf src/a/); claim "$c4" w1; evidence "$c4" w-cf
before=$(into); expect "충돌은 7" 7 "$BIN" "$c4" w1 --repo "$R"; unchanged "$before" "충돌"
grep -q '"kind": "merge_reject".*"exit": "7"' "$BOARD_DIR/incidents.jsonl" 2>/dev/null && ok "충돌 거부는 사건 기록" || ko "충돌 사건 기록" "$(cat "$BOARD_DIR/incidents.jsonl" 2>/dev/null)"

echo dirty > "$R/base.txt"; expect "더러운 작업 공간은 3" 3 "$BIN" "$c4" w1 --repo "$R"; git -C "$R" checkout -q base.txt
mkdir "$R/.git/board-merge.lock"; echo 999999 > "$R/.git/board-merge.lock/pid"
expect "죽은 pid 의 잠금은 회수" 7 "$BIN" "$c4" w1 --repo "$R"
# 병합 단일 경로 우회 3가지
branch w-direct src/b/d.cs 1; c5=$(card 직접병합 w-direct src/a/); claim "$c5" w1; evidence "$c5" w-direct
git -C "$R" merge -q --no-ff -m direct w-direct
expect "git merge 를 먼저 했어도 범위 밖이면 4" 4 "$BIN" "$c5" w1 --repo "$R"
[ "$(st "$c5")" = in_progress ] && ok "직접 병합 범위 밖: 카드 안 닫힘" || ko "직접 병합 범위 밖 카드" "$(st "$c5")"
branch w-direct2 src/a/d2.cs 1; c5b=$(card 직접병합범위안 w-direct2 src/a/); claim "$c5b" w1; evidence "$c5b" w-direct2
git -C "$R" merge -q --no-ff -m direct2 w-direct2
expect "git merge 를 먼저 했어도 범위 안·증거 통과면 0" 0 "$BIN" "$c5b" w1 --repo "$R"
branch w-closed src/a/cl.cs 1; c6=$(card 직접닫음 w-closed src/a/); claim "$c6" w1
(cd "$BOARD_DIR" && BD_ACTOR=w1 bd close "$c6" --reason 직접 --force >/dev/null 2>&1)
before=$(into); expect "bd close 로 먼저 닫은 카드는 6" 6 "$BIN" "$c6" w1 --repo "$R"; unchanged "$before" "직접 닫은 카드"
# 작업자가 완료 검사로 먼저 닫은 카드: 다시 열라고 돌려보내지 않고 병합까지 이어감
branch w-pre src/a/pre.cs 1; c8=$(card 먼저닫음 w-pre src/a/); claim "$c8" w1; evidence "$c8" w-pre
(cd "$R" && "$HERE/../../board/bin/board-done" "$c8" w1 >/dev/null 2>&1); [ "$(st "$c8")" = closed ] && ok "준비: 완료 검사가 카드를 먼저 닫음" || ko "준비: 먼저 닫기" "$(st "$c8")"
expect "완료 검사로 먼저 닫힌 카드도 병합 0" 0 "$BIN" "$c8" w1 --repo "$R"
git -C "$R" merge-base --is-ancestor w-pre feat && ok "먼저 닫힌 카드: 브랜치 병합됨" || ko "먼저 닫힌 카드 병합" "$(tail -3 "$SB/out")"
[ "$(st "$c8")" = closed ] && ok "먼저 닫힌 카드: 병합 뒤 다시 닫힘" || ko "먼저 닫힌 카드 상태" "$(st "$c8")"
(cd "$BOARD_DIR" && bd comments "$c8" 2>/dev/null) | grep -q 'board-merge: ' && ok "먼저 닫힌 카드: 병합 기록 남음" || ko "먼저 닫힌 카드 병합 기록"
# 다시 연 뒤 증거가 낡았으면(브랜치에 커밋이 더 생김) 병합하지 않고 진행 중으로 남김
branch w-pre2 src/a/pre2.cs 1; c9=$(card 먼저닫고커밋 w-pre2 src/a/); claim "$c9" w1; evidence "$c9" w-pre2
(cd "$R" && "$HERE/../../board/bin/board-done" "$c9" w1 >/dev/null 2>&1)
git -C "$R" checkout -q w-pre2; echo 2 > "$R/src/a/pre2.cs"; git -C "$R" commit -q -am more; git -C "$R" checkout -q feat
before=$(into); expect "먼저 닫힌 뒤 커밋이 더 생기면 6" 6 "$BIN" "$c9" w1 --repo "$R"; unchanged "$before" "먼저 닫힌 뒤 낡은 증거"
[ "$(st "$c9")" = in_progress ] && ok "낡은 증거: 카드는 진행 중으로 남음(다시 집을 필요 없음)" || ko "낡은 증거 카드 상태" "$(st "$c9")"
branch w-lab src/a/la.cs 1; branch w-oth src/a/ot.cs 1; c7=$(card 다른브랜치 w-lab src/a/); claim "$c7" w1; evidence "$c7" w-lab
before=$(into); expect "--branch 가 카드 label 과 다르면 2" 2 "$BIN" "$c7" w1 --repo "$R" --branch w-oth; unchanged "$before" "--branch 불일치"
expect "--branch 가 label 과 같으면 0" 0 "$BIN" "$c7" w1 --repo "$R" --branch w-lab

# 저장소별 설정 repos.json 의 merge_verify 가 시험 병합을 검증
branch w-cfg src/a/cfg.cs 1; kc=$(card 설정검증 w-cfg src/a/cfg.cs); claim "$kc" w1; evidence "$kc" w-cfg
printf '{"%s": {"merge_verify": "exit 1"}}\n' "$R" > "$BOARD_DIR/repos.json"
before=$(into); expect "repos.json merge_verify 실패면 8" 8 "$BIN" "$kc" w1 --repo "$R"; unchanged "$before" "설정 검증 실패"
rm -f "$BOARD_DIR/repos.json"
"$BIN" "$kc" w1 --repo "$R" >"$SB/out" 2>&1; grep -q '병합 검증 설정 없음' "$SB/out" && ok "설정 없으면 경고" || ko "설정 없음 경고" "$(tail -3 "$SB/out")"

# 동시 병합: 뒤 병합은 앞 병합(시험 병합 중)이 끝날 때까지 잠금에서 기다림
branch w-c1 src/a/c1.cs 1; branch w-c2 src/a/c2.cs 1
k1=$(card 동시1 w-c1 src/a/c1.cs); k2=$(card 동시2 w-c2 src/a/c2.cs); claim "$k1" w1; claim "$k2" w2; evidence "$k1" w-c1; evidence "$k2" w-c2
BOARD_MERGE_VERIFY='sleep 4' "$BIN" "$k1" w1 --repo "$R" >"$SB/o1" 2>&1 & bg=$!
sleep 2; "$BIN" "$k2" w2 --repo "$R" >"$SB/o2" 2>&1; r2=$?; wait "$bg"; r1=$?
[ "$r1" = 0 ] && [ "$r2" = 0 ] && ok "동시 병합 둘 다 성공(뒤는 잠금 대기)" || ko "동시 병합" "rc $r1/$r2: $(tail -2 "$SB/o2")"
branch w-c3 src/a/c3.cs 1; k3=$(card 대기초과 w-c3 src/a/c3.cs); claim "$k3" w1; evidence "$k3" w-c3
mkdir "$R/.git/board-merge.lock"; echo $$ > "$R/.git/board-merge.lock/pid"
expect "살아 있는 잠금은 BOARD_MERGE_WAIT 뒤 3" 3 env BOARD_MERGE_WAIT=2 "$BIN" "$k3" w1 --repo "$R"
rm -rf "$R/.git/board-merge.lock"

expect "--help 0" 0 "$BIN" --help
expect "인자 부족 2" 2 "$BIN" onlycard

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
