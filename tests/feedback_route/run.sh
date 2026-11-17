#!/usr/bin/env bash
# Slack 기능 스레드 피드백 전달 시험. 가짜 slack-inbox 레코드(폴러가 쓰는 모양)와 mktemp -d 임시 작업판만 씀. 실제 Slack·봇 없음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SCR="$HERE/../../hubs/scripts"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/feedback-route.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" LRM_HUB_DIR="$SB/hub"
mkdir -p "$BOARD_DIR" "$LRM_HUB_DIR"
(cd "$BOARD_DIR" && git init -q && bd init --prefix t -q >/dev/null 2>&1) || { echo "임시 작업판 준비 실패"; exit 1; }
b() { (cd "$BOARD_DIR" && bd "$@"); }
id_of() { jq -r 'if type=="array" then .[0].id else .id end'; }
FR=(python3 -I "$SCR/feedback_route.py")
IN="$LRM_HUB_DIR/slack-inbox.jsonl"
rec() { jq -cn --arg ts "$1" --arg p "$2" --arg t "$3" --argjson f "${4:-[]}" '{type:"thread_reply",ts:$ts,parent_ts:$p,text:$t,channel:"Dch1",received_at:1794383300} + (if ($f|length)>0 then {files:$f} else {} end)'; }

MAIL=$(b create "메일함 기능" -t epic -l feature:mail,branch:feat/mail --json | id_of)
SHOP=$(b create "상점 기능" -t epic -l feature:shop --json | id_of)
OLD=$(b create "닫힌 기능" -t epic -l feature:old --json | id_of)
NOFEAT=$(b create "라벨 없는 epic" -t epic --json | id_of)

"${FR[@]}" open-thread --epic "$MAIL" --ts 1794383274.111111 >/dev/null && b show "$MAIL" --json | jq -e 'if type=="array" then .[0] else . end | .labels | index("slack-thread:1794383274.111111")' >/dev/null && ok "open-thread: epic 에 slack-thread:<ts> 라벨" || ko "open-thread" ""
[ "$("${FR[@]}" open-thread --epic "$MAIL" --ts 1794383274.111111 --json | jq -r .added)" = false ] && ok "open-thread 반복 안전" || ko "open-thread 반복" ""
"${FR[@]}" open-thread --epic "$MAIL" --ts nope >/dev/null 2>&1; [ $? = 2 ] && ok "open-thread: ts 형식 오류 → 종료 2" || ko "ts 형식" ""
"${FR[@]}" open-thread --epic "$NOFEAT" --ts 1794383274.555555 >/dev/null 2>"$SB/err"; [ $? = 2 ] && grep -q 'feature:' "$SB/err" && ok "open-thread: feature: 라벨 없는 epic 거부" || ko "라벨 없는 epic" "$(cat "$SB/err")"
"${FR[@]}" open-thread --epic t-zzz --ts 1794383274.555555 >/dev/null 2>&1; [ $? = 4 ] && ok "open-thread: 없는 epic → 종료 4" || ko "없는 epic" ""
"${FR[@]}" open-thread --epic "$SHOP" --ts 1794383274.222222 >/dev/null
"${FR[@]}" open-thread --epic "$OLD" --ts 1794383274.222222 >/dev/null
"${FR[@]}" open-thread --epic "$OLD" --ts 1794383274.333333 >/dev/null
b close "$OLD" >/dev/null

rec 1794383200.000001 1794383274.111111 "옛 답글(첫 실행 전)" > "$IN"
[ "$("${FR[@]}" --once --json | jq -r '.routed|length')" = 0 ] && ok "첫 실행은 파일 끝에서 시작(옛 답글 몰아 보내지 않음)" || ko "첫 실행" ""

RAW='버튼 색이 너무 *진해요* — 시안처럼 `#FFB` 로
둘째 줄도 그대로'
{ rec 1794383300.000001 1794383274.111111 "$RAW" '["/hub/slack-files/a.png","/hub/slack-files/b.jpg"]'
  rec 1794383300.000002 1794383274.222222 "어느 기능 것?"
  rec 1794383300.000003 1794383274.333333 "닫힌 기능 스레드"
  rec 1794383300.000004 1794383274.999999 "등록 안 된 스레드"
  jq -cn '{type:"note",ts:"1794383300.000005",text:"최상위 글"}'; } >> "$IN"
DRY=$("${FR[@]}" --once --dry-run --json)
[ "$(echo "$DRY" | jq -r '.routed|length')" = 3 ] && [ "$(b comments "$MAIL" --json | jq 'length')" = 0 ] && [ ! -f "$LRM_HUB_DIR/feedback-routed.jsonl" ] && ok "--dry-run: 판정만, 작업판·기록 그대로" || ko "dry-run" "$DRY"
OUT=$("${FR[@]}" --once --json)
[ "$(echo "$OUT" | jq -r '[.routed[]|.to]|join(",")')" = "feature,triage,triage" ] && ok "전달: 기능 1·애매 2(epic 여럿·닫힌 epic), 등록 안 된 스레드·최상위 글은 건너뜀" || ko "전달 판정" "$OUT"
C=$(b comments "$MAIL" --json | jq -r '.[0].text')
[ "$C" = "Slack 피드백(원문): $RAW
첨부: /hub/slack-files/a.png /hub/slack-files/b.jpg" ] && ok "epic 코멘트 = 원문 그대로(줄바꿈·기호 유지) + 첨부 경로" || ko "코멘트 원문" "$C"
BOX="$LRM_HUB_DIR/hub-inbox/feat-mail.jsonl"
[ "$(jq -r .kind "$BOX")" = feedback ] && [ "$(jq -r .text "$BOX")" = "$RAW
첨부: /hub/slack-files/a.png /hub/slack-files/b.jpg" ] && jq -r .ref "$BOX" | grep -q "$MAIL slack:1794383300.000001" && ok "기능 코디네이터 상자 hub-inbox/feat-mail.jsonl 에 원문 한 줄" || ko "상자" "$(cat "$BOX" 2>/dev/null)"
[ ! -s "$LRM_HUB_DIR/hub-inbox/main.jsonl" ] && ok "메인 받은 편지함에는 아무것도 안 감" || ko "메인 경유" "$(cat "$LRM_HUB_DIR/hub-inbox/main.jsonl")"
TC=$(b list --label feedback:triage --json --limit 0 | jq -r '.[]|.title' | sort)
[ "$(echo "$TC" | wc -l | tr -d ' ')" = 2 ] && echo "$TC" | grep -q '1794383274.222222' && b list --label feedback:triage --json --limit 0 | jq -e 'all(.[]; .labels|index("hub:main"))' >/dev/null && ok "애매한 것은 메인 카드 '📥 기능 판별 필요'(스레드마다 1장)" || ko "판별 카드" "$TC"
T2=$(b list --label feedback:triage --json --limit 0 | jq -r '.[]|select(.title|test("222222"))|.id')
b comments "$T2" --json | jq -r '.[0].text' | grep -q "epic 여럿.*어느 기능 것?" && ok "판별 카드 코멘트에 이유·원문" || ko "판별 코멘트" "$(b comments "$T2" --json)"
[ "$("${FR[@]}" --once --json | jq -r '.routed|length')" = 0 ] && [ "$(b comments "$MAIL" --json | jq 'length')" = 1 ] && ok "다시 돌려도 같은 답글 재전달 없음" || ko "반복" ""
rec 1794383400.000001 1794383274.222222 "같은 스레드 둘째 답글" >> "$IN"
"${FR[@]}" --once >/dev/null
[ "$(b list --label feedback:triage --json --limit 0 | jq 'length')" = 2 ] && [ "$(b comments "$T2" --json | jq 'length')" = 2 ] && ok "같은 스레드 애매 답글은 기존 판별 카드에 이어 붙임" || ko "판별 카드 재사용" ""

python3 -I - "$SCR" "$LRM_HUB_DIR" <<'PY' && ok "slack_push.read_inbox: 기능으로 보낸 답글은 메인 전달에서 빠짐, 나머지는 그대로" || ko "slack_push 건너뜀" ""
import os, sys
os.environ["LRM_HUB_DIR"] = sys.argv[2]
sys.path.insert(0, sys.argv[1])
import slack_push
rows = slack_push.read_inbox()
kept = [r["ts"] for r in rows if r]
sys.exit(0 if kept == ["1794383200.000001", "1794383300.000004", "1794383300.000005"] else print(kept) or 1)
PY
python3 -I "$SCR/main_inbox.py" read --since-offset --box feat-mail --json | jq -e 'select(.kind=="feedback")' >/dev/null && ok "코디네이터는 main_inbox.py read --box feat-mail 로 읽음" || ko "상자 읽기" ""
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
