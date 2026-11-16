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
  printf 'Compilation succeeded\n' > "$SB/ev/$1.log"
  printf '<test-run total="1" passed="1" failed="0" result="Passed"></test-run>\n' > "$SB/ev/$1.xml"
  (cd "$BOARD_DIR" && bd comments add "$1" "evidence:compile=$SB/ev/$1.log commit=$h" >/dev/null && bd comments add "$1" "evidence:editmode=$SB/ev/$1.xml commit=$h" >/dev/null)
}
branch() { # branch <이름> <파일> <내용>
  git -C "$R" branch "$1" feat; git -C "$R" checkout -q "$1"; mkdir -p "$R/$(dirname "$2")"; echo "$3" > "$R/$2"
  git -C "$R" add .; git -C "$R" commit -q -m "$1"; git -C "$R" checkout -q feat
}
expect() { local name=$1 want=$2; shift 2; "$@" >"$SB/out" 2>&1; local got=$?; [ "$got" = "$want" ] && ok "$name" || ko "$name" "종료 $got(기대 $want): $(tail -c 300 "$SB/out")"; }
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

echo dirty > "$R/base.txt"; expect "더러운 작업 공간은 3" 3 "$BIN" "$c4" w1 --repo "$R"; git -C "$R" checkout -q base.txt
mkdir "$R/.git/board-merge.lock"; echo 999999 > "$R/.git/board-merge.lock/pid"
expect "죽은 pid 의 잠금은 회수" 7 "$BIN" "$c4" w1 --repo "$R"
expect "--help 0" 0 "$BIN" --help
expect "인자 부족 2" 2 "$BIN" onlycard

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
