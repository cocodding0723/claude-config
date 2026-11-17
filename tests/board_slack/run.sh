#!/usr/bin/env bash
# 작업판 결정 카드 → Slack 번호 카드 보내기 시험. 가짜 slack_notify(인자만 기록)와 mktemp -d 임시 작업판만 씀. 실제 Slack·봇 없음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SCR="$HERE/../../hubs/scripts"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/board-slack.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" LRM_HUB_DIR="$SB/hub" LRM_NOTIFY="$SB/notify.sh"
mkdir -p "$BOARD_DIR" "$LRM_HUB_DIR"
(cd "$BOARD_DIR" && git init -q && bd init --prefix t -q >/dev/null 2>&1) || { echo "임시 작업판 준비 실패"; exit 1; }
cat > "$LRM_NOTIFY" <<'SH'
#!/bin/bash
for a in "$@"; do printf '%s\n' "$a"; done > "$(dirname "$0")/notify.args"
echo "ok 1794383274.123456"
SH
chmod +x "$LRM_NOTIFY"
b() { (cd "$BOARD_DIR" && bd "$@"); }
labels() { b show "$1" --json | jq -r 'if type=="array" then .[0] else . end | (.labels|sort|join(","))'; }
BS="$SCR/board_slack.py"

THIN=$("$SCR/board-ask" --runnable no "선택지 결과 없는 결정" --options "A|B" --recommend A)
python3 -I "$BS" push "$THIN" 2>"$SB/err"; rc=$?
[ "$rc" = 2 ] && grep -q '배경' "$SB/err" && grep -q '선택지 결과' "$SB/err" && grep -q '조치' "$SB/err" && [ ! -f "$SB/notify.args" ] && ok "필수 칸(배경·용어·결과) 없으면 안 보냄, 원인·조치" || ko "필수 칸" "rc=$rc $(cat "$SB/err")"

C=$("$SCR/board-ask" --runnable no "색 정할까?" --options "파랑 => 차분|빨강 => 강렬" --recommend 빨강 --why "시안 기준" --background "시안 두 벌이라 하나로 맞춤" --terms 없음)
OUT=$(python3 -I "$BS" push "$C" --json); rc=$?
DID=$(echo "$OUT" | jq -r .decisionId)
[ "$rc" = 0 ] && [ "$(echo "$OUT" | jq -r .sent)" = true ] && [[ "$DID" =~ ^D-B[A-Za-z0-9]{1,16}$ ]] && ok "보냄: 결정 번호 D-B+카드 id" || ko "push" "rc=$rc $OUT"
grep -qx -- '1=파랑 => 차분,2=빨강 => 강렬' "$SB/notify.args" && grep -A1 -x -- '--recommend' "$SB/notify.args" | grep -qx 2 && grep -A1 -x -- '--decision-id' "$SB/notify.args" | grep -qx "$DID" && grep -q '^배경: 시안 두 벌' "$SB/notify.args" && grep -q '^이유: 시안 기준' "$SB/notify.args" && ok "번호 카드 인자: 선택지·결과·추천 번호·결정 번호·배경·용어·이유" || ko "인자" "$(cat "$SB/notify.args")"
python3 -I - "$SCR" "$SB/notify.args" <<'PY' && ok "실제 결정 카드 규칙(decide_card.problem)도 통과" || ko "decide_card 규칙" ""
import sys
sys.path.insert(0, sys.argv[1])
import decide_card
a = open(sys.argv[2], encoding="utf-8").read().split("\n")
opts = a[a.index("--options") + 1]
body = "\n".join(a[a.index("--recommend") + 2:]).strip()
why = decide_card.problem(body, opts, a[a.index("--decision-id") + 1])
print(why or "", file=sys.stderr)
sys.exit(1 if why else 0)
PY
labels "$C" | grep -q "slack:$DID" && ok "보낸 카드에 label slack:<결정 번호>" || ko "label" "$(labels "$C")"
rm -f "$SB/notify.args"
[ "$(python3 -I "$BS" push "$C" --json | jq -r .why)" = already ] && [ ! -f "$SB/notify.args" ] && ok "다시 보내지 않음(반복 안전)" || ko "반복" ""
AP=$("$SCR/board-ask" "PR #1 게시" --gate approval --recommend 승인 --background "draft PR 을 올려 리뷰를 받음" --terms 없음)
python3 -I "$BS" push "$AP" --dry-run --json | jq -r '.args[]' | grep -qx '1=승인 => 이대로 진행,2=보류 => 진행하지 않고 카드를 열어 둠' && ok "승인 성격 기본 선택지도 결과가 있어 보낼 수 있음(--dry-run 은 안 보냄)" || ko "승인 push" ""
[ ! -f "$SB/notify.args" ] && ok "--dry-run 은 slack_notify 를 부르지 않음" || ko "dry-run" ""

R=$(python3 -I "$BS" answer "$DID" "결정 $DID = 선택 2(빨강) (버튼)" --json)
[ "$(echo "$R" | jq -r .closed)" = true ] && b comments "$C" --json | jq -r '.[].text' | grep -q "사용자 답: 결정 $DID = 선택 2(빨강)" && ok "answer: 결정 번호로 카드를 찾아 원문 코멘트 + 닫기" || ko "answer" "$R"
[ "$(python3 -I "$BS" answer D-RDY812 "승인" --json | jq -r .board)" = false ] && ok "작업판 카드가 아닌 결정 번호는 손대지 않음" || ko "남의 번호" ""

touch "$LRM_HUB_DIR/.board-slack.on"
A2=$("$SCR/board-ask" --runnable no "글꼴 정할까?" --options "A => 둥근|B => 각진" --recommend A --background "제목 글꼴 후보 둘" --terms 없음)
labels "$A2" | grep -q 'slack:D-B' && ok "스위치 파일 있으면 board-ask 가 새 결정 카드를 바로 보냄" || ko "auto push" "$(labels "$A2")"
A3=$("$SCR/board-ask" --runnable no "칸 빠진 결정" --options "A|B" 2>"$SB/err"); rc=$?
[ "$rc" = 0 ] && [ -n "$A3" ] && grep -q 'Slack 카드 못 보냄' "$SB/err" && ok "자동 보내기 실패는 카드 생성을 막지 않음(경고만)" || ko "auto push 실패" "rc=$rc $(cat "$SB/err")"
rm -f "$LRM_HUB_DIR/.board-slack.on"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
