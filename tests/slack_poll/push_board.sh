#!/usr/bin/env bash
# slack_push.sh 작업판 경로(main-route=board) 검증: 터미널 전송 없이 메시지마다 작업판 카드 코멘트 + 데스크톱 알림 1회 + 읽음 표식.
# 임시 작업판(bd init)·임시 허브 폴더(mktemp -d)만 쓰고, 가짜 orca·가짜 osascript 로 실제 터미널·알림을 부르지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS=${PUSH_SCRIPTS:-$HERE/../../hubs/scripts}
PUSH="$SCRIPTS/slack_push.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-pushboard.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
FB="$SB/bin"; mkdir -p "$FB"
cp "$HERE/fake_orca.sh" "$FB/orca"; chmod 755 "$FB/orca"
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> "%s/osa.log"\nexit "$(cat "%s/osa.rc" 2>/dev/null || echo 0)"\n' "$SB" "$SB" > "$FB/osascript"; chmod 755 "$FB/osascript"
printf '#!/bin/bash\necho "$*" >> "%s/notify.log"\n' "$SB" > "$FB/notify"; chmod 755 "$FB/notify"
export FAKE_MAIN="$SB/main-checkout" LRM_PUSH_MAIN="$SB/main-checkout"  # 옛 경로 시험의 가짜 메인 체크아웃(문자열로만 쓰임)
export PATH="$FB:$PATH" LRM_OSASCRIPT="$FB/osascript" LRM_PUSH_NOTIFY="$FB/notify" LRM_PUSH_REPOLL=off
export LRM_PUSH_QUIET=0 LRM_PUSH_MAX=1 LRM_PUSH_TICK=0.1 LRM_PUSH_HOLD_RETRY=0.3 LRM_PUSH_LIFE=4
export BOARD_DIR="$SB/board" BOARD_ID_RE='t-[0-9a-z]{3,8}'
mkdir -p "$BOARD_DIR"
(cd "$BOARD_DIR" && git init -q && bd init --prefix t -q >/dev/null 2>&1) || { ko "임시 작업판 준비" "bd init 실패"; exit 1; }
b() { (cd "$BOARD_DIR" && bd "$@"); }
DEC=$(b create "색 정할까?" -l kind:decision,slack:D-0B01 --json | jq -r 'if type=="array" then .[0].id else .id end')

H="$SB/hub" FO="$SB/fo"; mkdir -p "$H" "$FO"
export LRM_HUB_DIR="$H" FAKE_ORCA_DIR="$FO"
printf 'MAIN_TERMINAL=term_0000feed-0000-4000-8000-000000000001\n' > "$H/main.env"
echo board > "$H/main-route"
R=$(date +%s)
{
  jq -nc --argjson r "$R" '{type:"note",ts:"1000.000001",text:"슬랙 봇 죽은듯 #12 확인",channel:"Dfake",received_at:$r}'
  jq -nc --argjson r "$R" '{type:"thread_reply",ts:"1000.000003",parent_ts:"1000.000002",title:"답을 기다리는 결정 5개",text:"이거 작업 다 마무리함",channel:"Dfake",received_at:$r}'
  jq -nc --argjson r "$R" '{type:"thread_reply",ts:"1000.000005",parent_ts:"1000.000004",title:"색 결정",kind:"decide",decision_id:"D-0B01",action:"free",text:"빨강으로",channel:"Dfake",received_at:$r}'
} > "$H/slack-inbox.jsonl"
printf '{"pushed_lines":0,"skip_to":0,"pending_since":null,"last_new_ts":null,"push_pending":null,"react_since":0}' > "$H/slack-push-state.json"

bash "$PUSH" --run; rc=$?
AL=$(b list --label alert:slack --json --limit 0)
ALID=$(echo "$AL" | jq -r '.[0].id // ""')
[ "$rc" = 0 ] && ok "종료 코드 0" || ko "종료 코드" "rc=$rc"
[ ! -s "$FO/sends.log" ] && ok "메인 터미널 전송 0회(작업판 경로)" || ko "터미널 전송" "$(cat "$FO/sends.log")"
[ -n "$ALID" ] && [ "$(b comments "$ALID" --json | jq 'length')" = 2 ] && ok "결정 번호 없는 메시지 2건 → '📥 자동 알림: slack' 카드 코멘트 2개" || ko "모음 카드" "$AL"
b comments "$ALID" --json | jq -r '.[].text' | grep -qF "줄 2 [답을 기다리는 결정 5개] ts=1000.000003" && b comments "$ALID" --json | jq -r '.[].text' | grep -qF "> 이거 작업 다 마무리함" \
  && ok "코멘트 = 줄 번호·제목·ts 머리줄 + 원문 인용" || ko "코멘트 본문" "$(b comments "$ALID" --json | jq -r '.[].text')"
b list --label pr:12 --json --limit 0 | jq -e 'length==0' >/dev/null && ! b comments "$ALID" --json | jq -r '.[].text' | grep -q '^\[slack_push·slack\] #' \
  && ok "머리줄은 '#n' 대신 '줄 n'(PR 번호로 오인 안 함)" || ko "PR 오인" ""
b comments "$DEC" --json | jq -r '.[-1].text // ""' | grep -qF "결정 D-0B01 답(자유 글)" && ok "결정 답 글 → 그 결정 카드(slack:D-0B01)에 코멘트" || ko "결정 카드" "$(b comments "$DEC" --json)"
[ "$(wc -l < "$SB/osa.log" | tr -d ' ')" = 1 ] && grep -qF "Slack 메시지 3건 — 작업판 기록" "$SB/osa.log" && ! grep -q "activate" "$SB/osa.log" \
  && ok "데스크톱 알림 1회(3건 묶음, activate 없음)" || ko "데스크톱 알림" "$(cat "$SB/osa.log" 2>/dev/null)"
[ "$(cat "$H/.main-read-lines" | tr -d ' \n')" = 3 ] && [ "$(jq -r '.pushed_lines' "$H/slack-push-state.json")" = 3 ] && ok "읽음 표식·전달 줄 3으로 올림(👀→✅ 대상)" || ko "읽음 표식" "$(cat "$H/.main-read-lines" 2>/dev/null)"
[ "$(jq -r '.via' "$H/slack-push-delivered.jsonl")" = board ] && [ "$(jq -c '.roots' "$H/slack-push-delivered.jsonl")" = '{"1000.000001":1,"1000.000002":1,"1000.000004":1}' ] \
  && ok "전달 기록 via=board·스레드별 건수(확인 답장 '작업판에 기록했어요' 갱신용)" || ko "전달 기록" "$(cat "$H/slack-push-delivered.jsonl")"
grep -q " push ok via=board n=3 lines=1-3 mark=3 new=3 dup=0 fallback=0 notice=ok" "$H/slack-push.log" && ok "로그 한 줄(본문 없음)" || ko "로그" "$(cat "$H/slack-push.log")"
[ ! -s "$H/hub-inbox/main.jsonl" ] && ok "main.jsonl 에 새 줄 없음(작업판 기록 성공)" || ko "main.jsonl" "$(cat "$H/hub-inbox/main.jsonl")"

# 같은 줄을 다시 보내도(상태를 잃음) 코멘트는 늘지 않음 — main_inbox·board_notify 멱등 키
printf '{"pushed_lines":0,"skip_to":0,"pending_since":null,"last_new_ts":null,"push_pending":null,"react_since":0}' > "$H/slack-push-state.json"
echo 0 > "$H/.main-read-lines"
bash "$PUSH" --run
[ "$(b comments "$ALID" --json | jq 'length')" = 2 ] && [ "$(b comments "$DEC" --json | jq 'length')" = 1 ] && grep -q "new=0 dup=3 fallback=0" "$H/slack-push.log" && ok "재전달해도 코멘트 중복 없음(로그 dup=3)" || ko "중복" "$(b comments "$ALID" --json | jq 'length')"

# 작업판 쓰기 실패(bd 없음) → main.jsonl 로 떨어져 잃지 않음
jq -nc --argjson r "$(date +%s)" '{type:"note",ts:"1000.000009",text:"작업판 꺼짐",channel:"Dfake",received_at:$r}' >> "$H/slack-inbox.jsonl"
BOARD_BD=/nonexistent/bd bash "$PUSH" --run
grep -qF "작업판 꺼짐" "$H/hub-inbox/main.jsonl" && grep -q "new=0 dup=0 fallback=1" "$H/slack-push.log" && [ "$(cat "$H/.main-read-lines" | tr -d ' \n')" = 4 ] && ok "작업판 실패 → main.jsonl 기록 후 읽음 처리" || ko "실패 경로" "$(cat "$H/hub-inbox/main.jsonl" 2>/dev/null)"

# main-route 를 지우면 옛 경로(터미널 전송) 그대로
rm "$H/main-route"
jq -nc --argjson r "$(date +%s)" '{type:"note",ts:"1000.000010",text:"터미널로",channel:"Dfake",received_at:$r}' >> "$H/slack-inbox.jsonl"
bash "$PUSH" --run
[ -s "$FO/sends.log" ] && ok "main-route 없으면 옛 경로(메인 터미널 전송) 그대로" || ko "옛 경로" "$(tail -3 "$H/slack-push.log")"

echo
echo "결과: PASS $PASS, FAIL $FAIL"
[ "$FAIL" = 0 ]
