#!/usr/bin/env bash
# 작업판 결정 카드 → Slack 번호 카드 보내기 시험. 가짜 slack_notify(인자만 기록)와 mktemp -d 임시 작업판만 씀. 실제 Slack·봇 없음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SCR="$HERE/../../hubs/scripts"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/board-slack.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" LRM_HUB_DIR="$SB/hub" LRM_NOTIFY="$SB/notify.sh" LRM_PREVIEW="$SB/preview.sh"
mkdir -p "$BOARD_DIR" "$LRM_HUB_DIR"
(cd "$BOARD_DIR" && git init -q && bd init --prefix t -q >/dev/null 2>&1) || { echo "임시 작업판 준비 실패"; exit 1; }
cat > "$LRM_NOTIFY" <<'SH'
#!/bin/bash
D=$(dirname "$0"); N=$(cat "$D/notify.n" 2>/dev/null || echo 0); N=$((N + 1)); echo "$N" > "$D/notify.n"
F=notify.args; [ "$N" = 1 ] || F=notify.args$N
for a in "$@"; do printf '%s\n' "$a"; done > "$D/$F"
echo "ok 1794383274.123456"
SH
cat > "$LRM_PREVIEW" <<'SH'
#!/bin/bash
[ -f "$1" ] && printf 'png' > "$2"
SH
chmod +x "$LRM_NOTIFY" "$LRM_PREVIEW"
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
DOC=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$SB/doc.html"); mkdoc "$DOC"  # 카드에는 정규화한 절대 경로가 저장됨
b() { (cd "$BOARD_DIR" && bd "$@"); }
labels() { b show "$1" --json | jq -r 'if type=="array" then .[0] else . end | (.labels|sort|join(","))'; }
BS="$SCR/board_slack.py"

legacy() {  # board-ask 가 이제 막는 칸 빠진 옛 결정 카드를 bd 로 직접 만듦
  b create "$1" -l kind:decision -p 1 -d $'선택지:\n- A\n- B\n추천: A\n돌려 보면 알 수 있나: 아니오' --json | jq -r 'if type=="array" then .[0].id else .id end'
}
THIN=$(legacy "선택지 결과 없는 결정")
python3 -I "$BS" push "$THIN" 2>"$SB/err"; rc=$?
[ "$rc" = 2 ] && grep -q '배경' "$SB/err" && grep -q '선택지 결과' "$SB/err" && grep -q '조치' "$SB/err" && [ ! -f "$SB/notify.args" ] && ok "필수 칸(배경·용어·결과) 없으면 안 보냄, 원인·조치" || ko "필수 칸" "rc=$rc $(cat "$SB/err")"

C=$("$SCR/board-ask" --runnable no "색 정할까?" --options "파랑 => 차분|빨강 => 강렬" --recommend 빨강 --why "시안 기준" --background "시안 두 벌이라 하나로 맞춤" --terms 없음 --doc "$DOC")
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
body = "\n".join(a[a.index("--recommend") + 2:-1]).strip()
why = decide_card.problem(body, opts, a[a.index("--decision-id") + 1])
print(why or "", file=sys.stderr)
sys.exit(1 if why else 0)
PY
labels "$C" | grep -q "slack:$DID" && ok "보낸 카드에 label slack:<결정 번호>" || ko "label" "$(labels "$C")"
rm -f "$SB/notify.args"*
[ "$(python3 -I "$BS" push "$C" --json | jq -r .why)" = already ] && [ ! -f "$SB/notify.args" ] && ok "다시 보내지 않음(반복 안전)" || ko "반복" ""
AP=$("$SCR/board-ask" "PR #1 게시" --gate approval --recommend 승인 --background "draft PR 을 올려 리뷰를 받음" --terms 없음 --doc "$DOC")
python3 -I "$BS" push "$AP" --dry-run --json | jq -r '.args[]' | grep -qx '1=승인 => 이대로 진행,2=보류 => 진행하지 않고 카드를 열어 둠' && ok "승인 성격 기본 선택지도 결과가 있어 보낼 수 있음(--dry-run 은 안 보냄)" || ko "승인 push" ""
[ ! -f "$SB/notify.args" ] && ok "--dry-run 은 slack_notify 를 부르지 않음" || ko "dry-run" ""

R=$(python3 -I "$BS" answer "$DID" "결정 $DID = 선택 2(빨강) (버튼)" --json)
[ "$(echo "$R" | jq -r .closed)" = true ] && b comments "$C" --json | jq -r '.[].text' | grep -q "사용자 답: 결정 $DID = 선택 2(빨강)" && ok "answer: 결정 번호로 카드를 찾아 원문 코멘트 + 닫기" || ko "answer" "$R"
[ "$(python3 -I "$BS" answer D-RDY812 "승인" --json | jq -r .board)" = false ] && ok "작업판 카드가 아닌 결정 번호는 손대지 않음" || ko "남의 번호" ""

touch "$LRM_HUB_DIR/.board-slack.on"
A2=$("$SCR/board-ask" --runnable no "글꼴 정할까?" --options "A => 둥근|B => 각진" --recommend A --background "제목 글꼴 후보 둘" --terms 없음 --doc "$DOC")
labels "$A2" | grep -q 'slack:D-B' && ok "스위치 파일 있으면 board-ask 가 새 결정 카드를 바로 보냄" || ko "auto push" "$(labels "$A2")"
"$SCR/board-ask" --runnable no "칸 빠진 결정" --options "A|B" >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 2 ] && grep -q '배경' "$SB/err" && grep -q '조치' "$SB/err" && [ -z "$(b list --json --limit 0 | jq -r '.[]|select(.title=="칸 빠진 결정")|.id')" ] && ok "board-ask 가 칸 빠진 결정을 종료 2 로 거부(스위치 켜진 상태에서도 카드 안 만듦)" || ko "필수 칸 거부" "rc=$rc $(cat "$SB/err")"
A3=$(legacy "옛 결정"); python3 -I "$BS" push "$A3" 2>"$SB/err" >/dev/null; rc=$?
[ "$rc" = 2 ] && ok "옛 카드를 직접 push 하면 거부(원인·조치)" || ko "옛 카드 push" "rc=$rc"
BG=$(PYTHONPATH= python3 -I - "$SCR" "$A3" 2>"$SB/err" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import board_slack
board_slack.auto_push(sys.argv[2])
PY
)
grep -q 'Slack 카드 못 보냄' "$SB/err" && ok "자동 보내기 실패는 경고만(예외 없음)" || ko "auto push 실패" "$(cat "$SB/err")"
rm -f "$LRM_HUB_DIR/.board-slack.on"

# ── 설명서(HTML) 첨부·재검사 ──
rm -f "$SB/notify.args"* "$SB/notify.n"
D1=$("$SCR/board-ask" --runnable no "설명서 붙일 결정" --options "A => 가 됨|B => 나 됨" --recommend A --background "시안이 두 벌이라 하나로 맞춤" --terms 없음 --doc "$DOC")
python3 -I "$BS" push "$D1" >/dev/null
tail -1 "$SB/notify.args" | grep -qx "$DOC" && ! grep -qx -- '--no-doc' "$SB/notify.args" && ok "설명서 경로가 slack_notify 마지막 인자(카드 스레드 첨부), --no-doc 없음" || ko "설명서 첨부" "tail=$(tail -1 "$SB/notify.args") doc=$DOC n=$(cat "$SB/notify.n")"
grep -qx -- '--thread' "$SB/notify.args2" && grep -qx '1794383274.123456' "$SB/notify.args2" && tail -1 "$SB/notify.args2" | grep -q 'preview.png$' && ok "모바일 미리보기 PNG 를 카드 스레드에 따로 첨부(기존 --thread 경로)" || ko "미리보기" "$(cat "$SB/notify.args2" 2>&1)"
D2=$("$SCR/board-ask" --runnable no "미리보기 실패 결정" --options "A => 가 됨|B => 나 됨" --recommend A --background "시안이 두 벌이라 하나로 맞춤" --terms 없음 --doc "$DOC")
printf '#!/bin/bash\nexit 1\n' > "$LRM_PREVIEW"
[ "$(python3 -I "$BS" push "$D2" --json | jq -r .sent)" = true ] && labels "$D2" | grep -q 'slack:D-B' && ok "미리보기 실패해도 카드는 보내고 label 도 붙임" || ko "미리보기 실패" ""
D3=$("$SCR/board-ask" --runnable no "나중에 바뀐 결정" --options "A => 가 됨|B => 나 됨" --recommend A --background "시안이 두 벌이라 하나로 맞춤" --terms 없음 --doc "$DOC")
rm -f "$SB/notify.args"* "$SB/notify.n"
cp "$DOC" "$SB/doc-keep.html"; mkdoc "$DOC" svg
python3 -I "$BS" push "$D3" 2>"$SB/err" >/dev/null; rc=$?
[ "$rc" = 2 ] && grep -q '도식' "$SB/err" && grep -q '조치' "$SB/err" && [ ! -f "$SB/notify.args" ] && ok "카드 만든 뒤 설명서가 바뀌어 도식이 빠지면 전송 직전에 거부" || ko "재검사" "rc=$rc $(cat "$SB/err")"
cp "$SB/doc-keep.html" "$DOC"
python3 -I "$BS" push "$D3" --dry-run --json | jq -e '.args[-1]' >/dev/null && ok "설명서를 되돌리면 --dry-run 통과(마지막 인자가 설명서)" || ko "되돌린 뒤" ""
b update "$D3" -d $'선택지:\n- A => 가 됨\n- B => 나 됨\n추천: A\n배경: 시안이 두 벌이라 하나로 맞춤\n용어: 없음' >/dev/null
python3 -I "$BS" push "$D3" --dry-run 2>"$SB/err" >/dev/null; rc=$?
[ "$rc" = 2 ] && grep -q '설명서' "$SB/err" && ok "카드에서 설명서 줄이 사라지면 거부" || ko "설명서 줄 없음" "rc=$rc $(cat "$SB/err")"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
