#!/usr/bin/env bash
# board-ask 결정 카드 필수 칸(배경·용어·추천·선택지 결과) 시험. mktemp -d 임시 작업판만 씀.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SCR="$HERE/../../hubs/scripts"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/board-cards.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" LRM_HUB_DIR="$SB/hub"
mkdir -p "$BOARD_DIR" "$LRM_HUB_DIR"
(cd "$BOARD_DIR" && git init -q && bd init --prefix t -q >/dev/null 2>&1) || { echo "임시 작업판 준비 실패"; exit 1; }
b() { (cd "$BOARD_DIR" && bd "$@"); }
count() { b list --json --limit 0 | jq --arg t "$1" '[.[]|select(.title==$t)]|length'; }
ASK="$SCR/board-ask"
OPT="A => 가 됨|B => 나 됨"

refuse() {  # refuse <이름> <빠진 칸 문구> <board-ask 인자...>
  local name=$1 gap=$2; shift 2
  "$ASK" --runnable no "$name" "$@" >/dev/null 2>"$SB/err"; local rc=$?
  [ "$rc" = 2 ] && grep -q "$gap" "$SB/err" && grep -q '조치' "$SB/err" && [ "$(count "$name")" = 0 ] \
    && ok "거부(종료 2·원인·조치·카드 없음): $name" || ko "거부: $name" "rc=$rc $(cat "$SB/err")"
}
refuse "배경 없음" '배경' --options "$OPT" --recommend A --terms 없음
refuse "용어 없음" '용어' --options "$OPT" --recommend A --background 배경
refuse "결과 없음" '선택지 결과' --options "A|B" --recommend A --background 배경 --terms 없음
refuse "결과 빈 칸" '선택지 결과' --options "A =>|B => 나" --recommend A --background 배경 --terms 없음
refuse "추천 없음" '추천' --options "$OPT" --background 배경 --terms 없음
refuse "선택지 1개" '선택지 2~4개' --options "A => 가" --recommend A --background 배경 --terms 없음
refuse "선택지 5개" '선택지 2~4개' --options "A => 1|B => 2|C => 3|D => 4|E => 5" --recommend A --background 배경 --terms 없음
"$ASK" --runnable no "여럿 빠짐" --options "A|B" >/dev/null 2>"$SB/err"
grep -q '배경, 용어' "$SB/err" && grep -q '추천' "$SB/err" && ok "빠진 칸을 한 번에 모두 안내" || ko "여럿 빠짐" "$(cat "$SB/err")"

FULL=(--options "$OPT" --recommend A --background "시안 두 벌이라 하나로 맞춤" --terms 없음)
ID=$("$ASK" --runnable no "다 채운 결정" "${FULL[@]}"); rc=$?
[ "$rc" = 0 ] && [ -n "$ID" ] && b show "$ID" --json | jq -r 'if type=="array" then .[0] else . end | .description' | grep -q '^배경: 시안 두 벌' && ok "다 채우면 카드 생성, 배경·용어 기록" || ko "생성" "rc=$rc"
python3 -I "$SCR/board_slack.py" push "$ID" --dry-run >/dev/null 2>"$SB/err" && ok "만든 카드는 board_slack push --dry-run 통과" || ko "slack 건조" "$(cat "$SB/err")"
[ "$("$ASK" --runnable no "다 채운 결정" "${FULL[@]}" --json | jq -r .created)" = false ] && ok "같은 제목 열린 카드는 새로 안 만듦(반복 안전)" || ko "반복" ""
"$ASK" --runnable no "건조 결정" "${FULL[@]}" --dry-run >/dev/null && [ "$(count "건조 결정")" = 0 ] && ok "--dry-run 은 카드 안 만듦" || ko "dry-run" ""
"$ASK" "PR 게시" --gate approval >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 2 ] && grep -q '배경' "$SB/err" && ok "승인 성격 결정도 배경·용어·추천 필수(기본 선택지는 결과 있음)" || ko "승인 거부" "rc=$rc $(cat "$SB/err")"
AP=$("$ASK" "PR 게시" --gate approval --recommend 승인 --background "draft PR 게시" --terms 없음) && ok "승인 성격: 칸을 채우면 기본 선택지로 생성" || ko "승인 생성" ""
T=$("$ASK" "할 일 하나" --kind user-todo) && [ -n "$T" ] && ok "user-todo 는 칸 없이도 생성" || ko "todo" ""
refuse "배경 너무 짧음" '배경이' --options "$OPT" --recommend A --background 짧음 --terms 없음
refuse "결과 너무 김" '결과가' --options "A => $(printf '가%.0s' $(seq 120))|B => 나" --recommend A --background "시안 두 벌이라 하나로 맞춤" --terms 없음
"$ASK" --help | grep -q '결정 카드 필수 칸' && ok "--help 에 필수 칸 안내" || ko "help" ""
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
