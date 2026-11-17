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
mkdoc() {  # mkdoc <경로> [빼려는 절: bg|svg|opt|why]
  local f=$1 skip=${2:-}
  {
    echo '<!doctype html><meta charset="utf-8"><title>설명서</title><body>'
    [ "$skip" = bg ] || echo '<h2>배경</h2><p>시안이 두 벌이라 어느 쪽으로 맞출지 정해야 화면 작업을 이어 갈 수 있다. 지금은 화면마다 색이 달라 QA 가 매번 헷갈리고, 디자인 검수 때도 어느 시안이 기준인지 다시 묻게 된다. 한 가지로 정해 두면 이후 화면은 그 기준만 따르면 된다.</p>'
    [ "$skip" = svg ] || echo '<svg viewBox="0 0 100 40"><rect x="0" y="0" width="30" height="30"/><rect x="40" y="0" width="30" height="30"/><path d="M30 15 L40 15"/></svg>'
    [ "$skip" = opt ] || echo '<h2>선택지별 결과</h2><ul><li>A 를 고르면 차분한 색으로 통일됨</li><li>B 를 고르면 강렬한 색으로 통일됨</li></ul>'
    [ "$skip" = why ] || echo '<h2>추천 이유</h2><p>시안 기준이 차분한 쪽이라 되돌릴 일이 적고 검수 비용이 낮다.</p>'
  } > "$f"
}
DOC="$SB/doc.html"; mkdoc "$DOC"
OPT="A => 가 됨|B => 나 됨"

refuse() {  # refuse <이름> <빠진 칸 문구> <board-ask 인자...>
  local name=$1 gap=$2; shift 2
  "$ASK" --runnable no "$name" --doc "$DOC" "$@" >/dev/null 2>"$SB/err"; local rc=$?
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

FULL=(--options "$OPT" --recommend A --background "시안 두 벌이라 하나로 맞춤" --terms 없음 --doc "$DOC")
ID=$("$ASK" --runnable no "다 채운 결정" "${FULL[@]}"); rc=$?
[ "$rc" = 0 ] && [ -n "$ID" ] && b show "$ID" --json | jq -r 'if type=="array" then .[0] else . end | .description' | grep -q '^배경: 시안 두 벌' && ok "다 채우면 카드 생성, 배경·용어 기록" || ko "생성" "rc=$rc"
python3 -I "$SCR/board_slack.py" push "$ID" --dry-run >/dev/null 2>"$SB/err" && ok "만든 카드는 board_slack push --dry-run 통과" || ko "slack 건조" "$(cat "$SB/err")"
[ "$("$ASK" --runnable no "다 채운 결정" "${FULL[@]}" --json | jq -r .created)" = false ] && ok "같은 제목 열린 카드는 새로 안 만듦(반복 안전)" || ko "반복" ""
"$ASK" --runnable no "건조 결정" "${FULL[@]}" --dry-run >/dev/null && [ "$(count "건조 결정")" = 0 ] && ok "--dry-run 은 카드 안 만듦" || ko "dry-run" ""
"$ASK" "PR 게시" --gate approval >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 2 ] && grep -q '배경' "$SB/err" && ok "승인 성격 결정도 배경·용어·추천 필수(기본 선택지는 결과 있음)" || ko "승인 거부" "rc=$rc $(cat "$SB/err")"
AP=$("$ASK" "PR 게시" --gate approval --recommend 승인 --background "draft PR 게시 확인 요청" --terms 없음 --doc "$DOC") && ok "승인 성격: 칸을 채우면 기본 선택지로 생성" || ko "승인 생성" ""
T=$("$ASK" "할 일 하나" --kind user-todo) && [ -n "$T" ] && ok "user-todo 는 칸 없이도 생성" || ko "todo" ""
refuse "배경 너무 짧음" '배경이' --options "$OPT" --recommend A --background 짧음 --terms 없음
refuse "결과 너무 김" '결과가' --options "A => $(printf '가%.0s' $(seq 120))|B => 나" --recommend A --background "시안 두 벌이라 하나로 맞춤" --terms 없음
refuse "설명서 칸 없음" '설명서' --doc "" --options "$OPT" --recommend A --background "시안 두 벌이라 하나로 맞춤" --terms 없음
for gap in "bg:배경(상세)" "svg:도식" "opt:선택지별 결과" "why:추천 이유"; do
  mkdoc "$SB/bad-${gap%%:*}.html" "${gap%%:*}"
  "$ASK" --runnable no "설명서 ${gap%%:*} 없음" --doc "$SB/bad-${gap%%:*}.html" "${FULL[@]:0:8}" >/dev/null 2>"$SB/err"; rc=$?
  [ "$rc" = 2 ] && grep -q "${gap#*:}" "$SB/err" && grep -q '조치' "$SB/err" && ok "설명서에 ${gap#*:} 없으면 종료 2" || ko "설명서 검사 ${gap%%:*}" "rc=$rc $(cat "$SB/err")"
done
for bad in '<p>배경은 A / B 두 갈래다</p>' '<ul><li>가 · 나 · 다 를 한 번에 고르면 모든 화면에서 같은 값으로 통일되고 이후 변경은 한 곳에서만 한다</li></ul>' '<ul><li>가·나·다·라·마 를 한 번에 고르면 모든 화면에서 같은 값으로 통일되고 이후 변경은 한 곳에서만 한다</li></ul>'; do
  { cat "$DOC"; echo "$bad"; } > "$SB/bad-read.html"
  "$ASK" --runnable no "읽기 위반 $bad" --doc "$SB/bad-read.html" "${FULL[@]:0:8}" >/dev/null 2>"$SB/err"; rc=$?
  [ "$rc" = 2 ] && grep -q '가독성' "$SB/err" && ok "가독성 위반 줄 거부: $bad" || ko "가독성 $bad" "rc=$rc $(cat "$SB/err")"
done
{ cat "$DOC"; echo '<p>주소는 <code>a / b</code> 와 https://x.test/a/b 와 ~/Documents/a/b 다. 구독·취소 묶음</p><pre>x / y · z · w</pre>'; } > "$SB/ok-read.html"
"$ASK" --runnable no "읽기 통과 코드·경로" --doc "$SB/ok-read.html" "${FULL[@]:0:8}" >/dev/null 2>"$SB/err" && ok "코드·경로·URL 안의 '/' 와 낱말 묶음 가운뎃점은 통과(오탐 없음)" || ko "가독성 오탐" "$(cat "$SB/err")"
FIX="$HERE/fixtures"
"$ASK" --runnable no "표준 형식 카드 절" --doc "$FIX/standard.html#lrm-x.1" "${FULL[@]:0:8}" >/dev/null 2>"$SB/err" && ok "표준 형식 사본(구역 배경·data-check=opt·why-rec)의 카드 절은 통과, 머리 줄 가운뎃점도 통과" || ko "표준 형식" "$(cat "$SB/err")"
"$ASK" --runnable no "doc-kit 양식" --doc "$FIX/dockit.html" "${FULL[@]:0:8}" >/dev/null 2>"$SB/err" && ok "doc-kit 양식 문서(머리 줄 '날짜 · 종류 · 커밋')도 통과" || ko "doc-kit 양식" "$(cat "$SB/err")"
"$ASK" --runnable no "표준 형식 id 없음" --doc "$FIX/standard.html" "${FULL[@]:0:8}" >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 2 ] && grep -q '여러 카드' "$SB/err" && grep -q '경로#카드id' "$SB/err" && ok "여러 카드가 든 설명서는 #카드id 없으면 종료 2" || ko "카드 id 없음" "rc=$rc $(cat "$SB/err")"
"$ASK" --runnable no "표준 형식 둘째 카드" --doc "$FIX/standard.html#lrm-x.2" "${FULL[@]:0:8}" >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 2 ] && grep -q '도식' "$SB/err" && grep -q '배경' "$SB/err" && grep -q '가독성' "$SB/err" && ok "같은 파일의 다른 카드 절은 그 카드 구역 기준으로 따로 거부(앞 카드 통과와 무관)" || ko "둘째 카드" "rc=$rc $(cat "$SB/err")"
"$ASK" --runnable no "없는 카드 절" --doc "$FIX/standard.html#lrm-none" "${FULL[@]:0:8}" >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 2 ] && grep -q '카드 lrm-none 절 없음' "$SB/err" && ok "문서에 없는 카드 id 는 종료 2" || ko "없는 카드" "rc=$rc"
"$ASK" --runnable no "설명서 파일 없음" --doc "$SB/none.html" "${FULL[@]:0:8}" >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 2 ] && grep -q '설명서 파일 없음' "$SB/err" && ok "설명서 파일이 없으면 종료 2" || ko "설명서 파일 없음" "rc=$rc $(cat "$SB/err")"
cp "$DOC" "$SB/doc.txt"; "$ASK" --runnable no "설명서 txt" --doc "$SB/doc.txt" "${FULL[@]:0:8}" >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 2 ] && grep -q '.html 아님' "$SB/err" && ok "설명서가 .html 이 아니면 종료 2" || ko "설명서 확장자" "rc=$rc"
"$ASK" --help | grep -q '결정 카드 필수 칸' && ok "--help 에 필수 칸 안내" || ko "help" ""
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
