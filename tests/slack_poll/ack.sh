#!/usr/bin/env bash
# slack_poll.sh 수신 확인 자동 답장 검증. 가짜 Slack API(로컬 HTTP)만 사용하며 실제 Slack·토큰·네트워크는 쓰지 않음.
# 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함. ACK_POLL=<경로> 로 변형 사본을 시험할 수 있음(변이 확인용).
set -uo pipefail
export LRM_PUSH=off  # 폴러 끝의 메인 전달(push)이 실제 orca 를 부르지 않게 함. push 는 push.sh 가 가짜 orca 로 시험

HERE=$(cd "$(dirname "$0")" && pwd -P)
POLL="${ACK_POLL:-$HERE/../../hubs/scripts/slack_poll.sh}"
CMDSH="$HERE/../../hubs/scripts/slack_cmd.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-slackack.XXXXXX")
SRV=""
trap '[ -n "$SRV" ] && { kill "$SRV"; wait "$SRV"; } 2>/dev/null; rm -rf "${SB:?}"' EXIT
FIX="$SB/fix" CL="$SB/calls.log"
mkdir -p "$FIX"; : > "$CL"

FAKE_TOKEN="fake-token-0123456789-abcdefghij"
ME=Uuser1; BOT=Ubot1
N=$(date +%s)
DEFAULT_TEXT="📥 메시지 집계 중이에요"

mkhub() { # 허브 폴더: 설정 파일과 첨부 전환 시각만 있는 새 허브
  mkdir -p "$1"
  printf 'SLACK_BOT_TOKEN=%s\nSLACK_USER_ID=%s\n' "$FAKE_TOKEN" "$ME" > "$1/slack-bot.env"
  printf '{"file_share_since":%s}' $((N - 60)) > "$1/slack-poll-state.json"
}
hist() { printf '{"ok":true,"messages":[%s]}' "$1" > "$FIX/conversations.history.json"; }
replies() { printf '{"ok":true,"messages":[%s]}' "$1" > "$FIX/conversations.replies.json"; }
note() { printf '{"ts":"%s","user":"%s","text":"메모 %s"}' "$1" "$ME" "$1"; }
umsg() { printf '{"ts":"%s","user":"%s","text":"%s"}' "$1" "$ME" "$2"; } # 두 번째 인자는 JSON 문자열 본문(이스케이프 포함)
botmsg() { printf '{"ts":"%s","user":"%s","bot_id":"B1","text":"%s"}' "$1" "$BOT" "$2"; }
okjson() { printf '{"ok":true}' > "$FIX/$1.json"; }
errjson() { printf '{"ok":false,"error":"%s"}' "$2" > "$FIX/$1.json"; }
agepast() { jq --argjson d "$2" '(.acked // {}) |= with_entries(.value -= $d) | (.ack_sent // []) |= map(. - $d)' "$1/slack-poll-state.json" > "$1/s.tmp" && mv "$1/s.tmp" "$1/slack-poll-state.json"; }
backdate() { jq '.ack_disabled_at -= 700' "$1/slack-poll-state.json" > "$1/s.tmp" && mv "$1/s.tmp" "$1/slack-poll-state.json"; }

printf '{"ok":true,"channel":{"id":"Dfake"}}' > "$FIX/conversations.open.json"
replies ""

python3 -I "$HERE/fake_slack.py" "$FIX" "$SB/port" "$CL" &
SRV=$!
for _ in $(seq 1 50); do [ -s "$SB/port" ] && break; sleep 0.1; done
[ -s "$SB/port" ] || { ko "가짜 API 기동" "포트 파일 없음"; exit 1; }
API="http://127.0.0.1:$(cat "$SB/port")"

pollh() { # 허브 폴더, 출력 파일 접두사
  LRM_HUB_DIR="$1" SLACK_API_BASE="$API" bash "$POLL" --once > "$SB/$2.out" 2> "$SB/$2.err"
}
acalls() { awk '/^chat\.postMessage\t/{n++} END{print n+0}' "$CL"; }
acks() { python3 -I "$HERE/ack_calls.py" "$CL"; } # 전체 chat.postMessage 호출을 JSON 한 줄씩
ack_threads() { acks | jq -r '.thread_ts' | tail -n +"$(($1 + 1))" | tr '\n' ','; } # 앞 $1 건을 건너뛴 thread_ts 목록
st() { jq -r "$2" "$1/slack-poll-state.json"; }
errlines() { wc -l < "$SB/$1.err" | tr -d ' '; }
inbox_n() { wc -l < "$1/slack-inbox.jsonl" | tr -d ' '; }

# ---- 시나리오 A: 새 메시지 → 스레드 답장 1회 ------------------------------------------
# 최상위 메모 1건 + 봇 메시지의 스레드에 단 사용자 답글 1건. 봇 자신의 확인 답장(에코)이 목록에 다시 보여도 수집·답장 대상이 아님
H="$SB/hubA"; mkhub "$H"
hist "$(note "$N.000100"),
 {\"ts\":\"$N.000300\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":3,\"latest_reply\":\"$N.000450\"},
 $(botmsg "$N.000160" "$DEFAULT_TEXT")"
replies "$(botmsg "$N.000300" "결정 필요"),$(umsg "$N.000400" "답글"),$(botmsg "$N.000450" "$DEFAULT_TEXT")"
printf '{"ts":"%s.000300","at":%s,"title":"결정 1","tag":"t"}\n' "$N" "$N" > "$H/slack-sent.jsonl"
c=$(acalls); pollh "$H" a1; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 2 ] && ok "수집 정상(메모·스레드 답글 2건, 봇 확인 답장 에코는 수집 안 됨)" || ko "수집" "rc=$rc inbox $(inbox_n "$H")"
[ "$(( $(acalls) - c ))" = 2 ] && [ "$(ack_threads "$c")" = "$N.000100,$N.000300," ] && ok "ⓐ 새 메시지 2건에 스레드 답장 각 1회(thread_ts: 최상위는 그 ts, 답글은 부모 ts)" || ko "ⓐ 호출" "$(( $(acalls) - c )) $(ack_threads "$c")"
[ "$(acks | jq -rs '[.[].channel] | unique | join(",")')" = Dfake ] && [ "$(acks | jq -rs '[.[].text] | unique | join(",")')" = "$DEFAULT_TEXT" ] && ok "ⓐ channel=DM 채널, 문구=📥 메시지 집계 중이에요" || ko "ⓐ 채널·문구" "$(acks | head -2)"
[ "$(errlines a1)" = 0 ] && [ "$(st "$H" '.acked | keys | length')" = 2 ] && [ "$(st "$H" '.ack_sent | length')" = 2 ] && ok "ⓐ 정상 시 stderr 없음, 상태에 acked 2건·ack_sent 2건" || ko "ⓐ 상태" "$(cat "$SB/a1.err") $(st "$H" '.acked')"
[ "$(jq -c 'select(.ts=="'"$N.000100"'") | keys' "$H/slack-inbox.jsonl")" = '["channel","received_at","text","ts","type"]' ] && ok "inbox 레코드 스키마 불변(기존 필드 그대로)" || ko "스키마" "$(head -1 "$H/slack-inbox.jsonl")"
[ "$(wc -l < "$H/slack-sent.jsonl" | tr -d ' ')" = 1 ] && ok "확인 답장은 slack-sent.jsonl 에 기록하지 않음(반응·답글 수집 대상이 되지 않게)" || ko "sent 기록" ""
c=$(acalls); pollh "$H" a2; rc=$?
[ "$rc" = 0 ] && [ "$(acalls)" = "$c" ] && [ "$(inbox_n "$H")" = 2 ] && ok "ⓐ 에코가 보이는 재실행: 호출 0회·수집 불변(답장이 사용자 메시지로 다시 잡히지 않음)" || ko "ⓐ 재실행" "rc=$rc $(( $(acalls) - c ))"
[ "$(grep -c 'bot_id\|Ubot1' "$H/slack-inbox.jsonl" || true)" = 0 ] && ok "봇 메시지·봇 답글은 inbox 에 없음" || ko "봇 수집" ""

# ---- 시나리오 B: 같은 스레드 30초 묶기 -------------------------------------------------
H="$SB/hubB"; mkhub "$H"
hist "{\"ts\":\"$N.001300\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":2,\"latest_reply\":\"$N.001410\"}"
replies "$(botmsg "$N.001300" "결정 필요"),$(umsg "$N.001400" "답글1"),$(umsg "$N.001410" "답글2")"
printf '{"ts":"%s.001300","at":%s,"title":"결정 2","tag":"t"}\n' "$N" "$N" > "$H/slack-sent.jsonl"
c=$(acalls); pollh "$H" b1; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 2 ] && [ "$(( $(acalls) - c ))" = 1 ] && [ "$(ack_threads "$c")" = "$N.001300," ] && ok "ⓑ 같은 스레드 답글 2건 한 회차 → 답장 1회(수집은 2건 모두)" || ko "ⓑ 같은 회차" "rc=$rc $(( $(acalls) - c )) $(ack_threads "$c")"
hist "{\"ts\":\"$N.001300\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":3,\"latest_reply\":\"$N.001420\"}"
replies "$(botmsg "$N.001300" "결정 필요"),$(umsg "$N.001400" "답글1"),$(umsg "$N.001410" "답글2"),$(umsg "$N.001420" "답글3")"
c=$(acalls); pollh "$H" b2; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 3 ] && [ "$(acalls)" = "$c" ] && ok "ⓑ 30초 안 다음 회차의 같은 스레드 답글 → 호출 0회" || ko "ⓑ 다음 회차" "rc=$rc $(( $(acalls) - c ))"
agepast "$H" 40
# 앞 답장이 이미 '전달했어요'로 갱신된 상태(n>0)여야 30초 규칙만 시험됨. 갱신 전이면 새 답장을 보내지 않음(아래 ⓥ)
jq '.ack_msgs["'"$N.001300"'"].n = 1' "$H/slack-poll-state.json" > "$H/s.tmp" && mv "$H/s.tmp" "$H/slack-poll-state.json"
hist "{\"ts\":\"$N.001300\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":4,\"latest_reply\":\"$N.001430\"}"
replies "$(botmsg "$N.001300" "결정 필요"),$(umsg "$N.001400" "답글1"),$(umsg "$N.001410" "답글2"),$(umsg "$N.001420" "답글3"),$(umsg "$N.001430" "답글4")"
c=$(acalls); pollh "$H" b3; rc=$?
[ "$rc" = 0 ] && [ "$(( $(acalls) - c ))" = 1 ] && [ "$(ack_threads "$c")" = "$N.001300," ] && ok "ⓑ 30초가 지난 뒤의 같은 스레드 새 답글 → 다시 1회" || ko "ⓑ 30초 뒤" "rc=$rc $(( $(acalls) - c ))"

# ---- 시나리오 C: 서로 다른 스레드 burst — 회차 3회, 60초 3회 ---------------------------
H="$SB/hubC"; mkhub "$H"
msgs=""; for i in 1 2 3 4 5; do msgs="$msgs$([ -n "$msgs" ] && echo ,)$(note "$N.0007$(printf '%02d' "$i")")"; done
hist "$msgs"
c=$(acalls); pollh "$H" c1; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 5 ] && [ "$(( $(acalls) - c ))" = 3 ] && ok "ⓒ 서로 다른 스레드 5건 burst → 첫 회차 3회, 수집은 5건 모두" || ko "ⓒ burst" "rc=$rc $(( $(acalls) - c ))"
[ "$(ack_threads "$c")" = "$N.000701,$N.000702,$N.000703," ] && ok "ⓒ 오래된 메시지부터 답장(ts 순)" || ko "ⓒ 순서" "$(ack_threads "$c")"
hist "$msgs,$(note "$N.000706"),$(note "$N.000707")"
c=$(acalls); pollh "$H" c2
[ "$(acalls)" = "$c" ] && ok "ⓒ 60초 안 이어진 새 메시지 2건 → 호출 0회(60초 3회 상한)" || ko "ⓒ 60초 상한" "$(( $(acalls) - c ))"
agepast "$H" 70
hist "$msgs,$(note "$N.000706"),$(note "$N.000707"),$(note "$N.000708")"
c=$(acalls); pollh "$H" c3
[ "$(( $(acalls) - c ))" = 1 ] && [ "$(ack_threads "$c")" = "$N.000708," ] && ok "ⓒ 60초가 지나면 다시 답장(새 메시지 1건에 1회)" || ko "ⓒ 60초 뒤" "$(( $(acalls) - c )) $(ack_threads "$c")"
# 회차를 나눠도 60초 3회: 2건 + 2건 → 2회 + 1회
H="$SB/hubC2"; mkhub "$H"
hist "$(note "$N.000801"),$(note "$N.000802")"; c=$(acalls); pollh "$H" c4
hist "$(note "$N.000801"),$(note "$N.000802"),$(note "$N.000803"),$(note "$N.000804")"; pollh "$H" c5
[ "$(( $(acalls) - c ))" = 3 ] && [ "$(ack_threads "$c")" = "$N.000801,$N.000802,$N.000803," ] && ok "ⓒ 회차를 나눠도 60초 안 최대 3회(2건+2건 → 2회+1회)" || ko "ⓒ 회차 분할" "$(( $(acalls) - c )) $(ack_threads "$c")"

# ---- 시나리오 D: 제외 대상 ----------------------------------------------------------------
# 봇 메시지, 내 반응(reaction 레코드), 30분 넘게 묵은 항목, 전환 이전 첨부, slack_cmd.sh 즉답 명령(줄 단위 판별 포함)
H="$SB/hubD"; mkhub "$H"
OLD=$((N - 86400)); STALE=$((N - 3600))
cmds=""; i=0
for t in '대기' '미승인' '승인 대기' 'PR' 'pr' '초안 PR' '할일' '할 일' 'CI' 'ci' '문서 abc' '안녕\n대기' '  대기  '; do
  i=$((i + 1)); cmds="$cmds,$(umsg "$N.0090$(printf '%02d' "$i")" "$t")"
done
hist "$(botmsg "$N.000500" "봇 일반 메시지"),
 {\"ts\":\"$N.000600\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reactions\":[{\"name\":\"white_check_mark\",\"count\":1,\"users\":[\"$ME\"]}]},
 $(note "$STALE.000100"),
 {\"ts\":\"$OLD.000050\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"지난 첨부\",\"files\":[]}$cmds"
printf '{"ts":"%s.000600","at":%s,"title":"결정 3","tag":"t"}\n' "$N" "$N" > "$H/slack-sent.jsonl"
c=$(acalls); pollh "$H" d1; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 15 ] && ok "ⓓ 수집은 정상(반응 1 + 묵은 메모 1 + 명령 13 = 15건)" || ko "ⓓ 수집" "rc=$rc $(inbox_n "$H")"
[ "$(acalls)" = "$c" ] && ok "ⓓ 봇 메시지·reaction 레코드·30분 넘게 묵은 항목·전환 이전 첨부·slack_cmd 즉답 명령 13종 → 호출 0회" || ko "ⓓ 제외" "$(( $(acalls) - c )): $(ack_threads "$c")"
[ "$(st "$H" '(.acked // {}) | length')" = 0 ] && ok "ⓓ 제외된 항목은 acked 기록도 없음" || ko "ⓓ 상태" "$(st "$H" '.acked')"
# 판별 복제 점검: slack_cmd.sh 의 case 라벨이 그대로 있는지(바뀌면 이 시험과 slack_poll.sh 의 CMD_REPLY_* 를 함께 고칠 것)
miss=""; for lab in '대기|미승인|"승인 대기")' 'PR|pr|"초안 PR")' '할일|"할 일")' 'CI|ci)' '문서\ *)'; do grep -qF -- "$lab" "$CMDSH" || miss="$miss $lab"; done
[ -z "$miss" ] && ok "ⓓ slack_cmd.sh case 라벨 5종이 CMD_REPLY_* 복제본과 일치" || ko "ⓓ case 라벨 변경됨" "$miss"
# 비슷하지만 즉답 명령이 아닌 메시지는 답장함(과다 제외 방지)
H="$SB/hubD2"; mkhub "$H"
hist "$(umsg "$N.000901" "대기중"),$(umsg "$N.000902" "문서"),$(umsg "$N.000903" "Pr")"
c=$(acalls); pollh "$H" d2; rc=$?
[ "$rc" = 0 ] && [ "$(( $(acalls) - c ))" = 3 ] && ok "ⓓ '대기중'·'문서'(뒤 공백 없음)·'Pr'(대소문자 다름)은 즉답 명령이 아니므로 답장 3회" || ko "ⓓ 과다 제외" "$(( $(acalls) - c ))"
# 스레드 답글은 slack_cmd.sh 가 안 다루므로 '대기' 라도 답장함
H="$SB/hubD3"; mkhub "$H"
hist "{\"ts\":\"$N.000950\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":1,\"latest_reply\":\"$N.000960\"}"
replies "$(botmsg "$N.000950" "결정 필요"),$(umsg "$N.000960" "대기")"
printf '{"ts":"%s.000950","at":%s,"title":"결정 4","tag":"t"}\n' "$N" "$N" > "$H/slack-sent.jsonl"
c=$(acalls); pollh "$H" d3
[ "$(( $(acalls) - c ))" = 1 ] && [ "$(ack_threads "$c")" = "$N.000950," ] && ok "ⓓ 스레드 답글 '대기'는 slack_cmd.sh 대상이 아니므로 답장 1회" || ko "ⓓ 답글 대기" "$(( $(acalls) - c ))"
replies ""

# ---- 시나리오 E: 오류 — 조용히 건너뜀 -------------------------------------------------
H="$SB/hubE"; mkhub "$H"; hist "$(note "$N.001000")"
errjson chat.postMessage missing_scope
c=$(acalls); pollh "$H" e1; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 1 ] && [ "$(st "$H" '.reacted["'"$N.001000"'"]')" = eyes ] && ok "ⓔ missing_scope: 종료 코드 0, 수집 정상, 읽음 반응 단계도 계속" || ko "ⓔ 종료·수집" "rc=$rc $(st "$H" '.reacted')"
[ "$(errlines e1)" = 1 ] && grep -q 'missing_scope' "$SB/e1.err" && [ "$(st "$H" '.ack_disabled_reason')" = missing_scope ] && [ "$(( $(acalls) - c ))" = 1 ] && ok "ⓔ stderr 한 줄, 상태에 ack_disabled_reason 기록, 호출 1회" || ko "ⓔ stderr·상태" "$(cat "$SB/e1.err") $(( $(acalls) - c ))"
hist "$(note "$N.001000"),$(note "$N.001010")"
c=$(acalls); pollh "$H" e2; rc=$?
[ "$rc" = 0 ] && [ "$(acalls)" = "$c" ] && [ "$(errlines e2)" = 0 ] && [ "$(inbox_n "$H")" = 2 ] && ok "ⓔ 10분 안 재호출 0회, stderr 조용, 새 메시지 수집은 계속" || ko "ⓔ 10분 내" "rc=$rc $(( $(acalls) - c )) stderr $(errlines e2)"
backdate "$H"; hist "$(note "$N.001000"),$(note "$N.001010"),$(note "$N.001020")"
c=$(acalls); pollh "$H" e3; rc=$?
[ "$rc" = 0 ] && [ "$(( $(acalls) - c ))" = 1 ] && [ "$(errlines e3)" = 0 ] && ok "ⓔ 10분 뒤 재시도 1회, 같은 사유는 stderr 반복 안 함" || ko "ⓔ 재시도" "rc=$rc $(( $(acalls) - c )) stderr $(errlines e3)"
okjson chat.postMessage; backdate "$H"; hist "$(note "$N.001000"),$(note "$N.001010"),$(note "$N.001020"),$(note "$N.001030")"
c=$(acalls); pollh "$H" e4; rc=$?
[ "$rc" = 0 ] && [ "$(( $(acalls) - c ))" = 1 ] && [ "$(st "$H" 'has("ack_disabled_reason")')" = false ] && [ "$(errlines e4)" = 0 ] && ok "ⓔ scope 추가 뒤 복구: 사유 삭제, 답장 1회" || ko "ⓔ 복구" "rc=$rc $(st "$H" '.ack_disabled_reason')"
H="$SB/hubE2"; mkhub "$H"; hist "$(note "$N.001100")"; errjson chat.postMessage invalid_auth; pollh "$H" e5
errjson chat.postMessage missing_scope; backdate "$H"; hist "$(note "$N.001100"),$(note "$N.001110")"; pollh "$H" e6
[ "$(errlines e5)" = 1 ] && [ "$(errlines e6)" = 1 ] && grep -q 'missing_scope' "$SB/e6.err" && ok "ⓔ 사유가 바뀌면 새 사유로 한 줄 알림" || ko "ⓔ 사유 변경" "$(cat "$SB/e5.err" "$SB/e6.err")"
H="$SB/hubE3"; mkhub "$H"; hist "$(note "$N.001200")"; errjson chat.postMessage channel_not_found
c=$(acalls); pollh "$H" e7; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 1 ] && [ "$(errlines e7)" = 1 ] && [ "$(st "$H" '.ack_disabled_reason')" = channel_not_found ] && ok "ⓔ channel_not_found 도 같은 방식으로 건너뜀(종료 코드 0, 사유 기록)" || ko "ⓔ channel_not_found" "rc=$rc"
# 일시 오류: 재시도하지 않고(at-most-once) 정지 상태로도 만들지 않음
H="$SB/hubE4"; mkhub "$H"; hist "$(note "$N.001300")"; errjson chat.postMessage internal_error
c=$(acalls); pollh "$H" e8; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 1 ] && [ "$(errlines e8)" = 1 ] && [ "$(st "$H" 'has("ack_disabled_reason")')" = false ] && [ "$(( $(acalls) - c ))" = 1 ] && ok "일시 오류: 종료 코드 0, 수집 정상, stderr 한 줄, 정지 상태 아님" || ko "일시 오류" "rc=$rc $(cat "$SB/e8.err")"
c=$(acalls); pollh "$H" e9
[ "$(acalls)" = "$c" ] && ok "일시 오류 뒤 같은 메시지 재시도 없음(전송 전 상태 기록, at-most-once)" || ko "재시도" "$(( $(acalls) - c ))"
okjson chat.postMessage

# ---- 시나리오 F: 스위치 -------------------------------------------------------------------
fi=0
for v in off OFF 0; do
  fi=$((fi + 1)); H="$SB/hubF$fi"; mkhub "$H"; hist "$(note "$N.001400")"
  c=$(acalls); LRM_ACK="$v" pollh "$H" "f$fi"; rc=$?
  [ "$rc" = 0 ] && [ "$(acalls)" = "$c" ] && [ "$(inbox_n "$H")" = 1 ] && [ "$(st "$H" 'has("acked")')" = false ] && ok "ⓕ LRM_ACK=$v → 호출 0회, 수집 정상, 상태 변화 없음" || ko "ⓕ LRM_ACK=$v" "rc=$rc $(( $(acalls) - c ))"
done
H="$SB/hubFon"; mkhub "$H"; hist "$(note "$N.001400")"
c=$(acalls); LRM_ACK=on pollh "$H" fon
[ "$(( $(acalls) - c ))" = 1 ] && ok "ⓕ LRM_ACK=on(그 밖의 값) → 켜짐" || ko "ⓕ on" ""

# ---- 시나리오 G: 중복·상태 손상 -----------------------------------------------------------
H="$SB/hubG"; mkhub "$H"; hist "$(note "$N.001500"),$(note "$N.001510")"
c=$(acalls); pollh "$H" g1; n1=$(( $(acalls) - c ))
pollh "$H" g2; pollh "$H" g3
[ "$n1" = 2 ] && [ "$(( $(acalls) - c ))" = 2 ] && ok "ⓖ 같은 입력 재실행 2회: 추가 호출 0회(중복 없음)" || ko "ⓖ 재실행" "$n1 $(( $(acalls) - c ))"
: > "$H/slack-poll-state.json"; c=$(acalls); pollh "$H" g4; rc=$?
[ "$rc" = 0 ] && [ "$(acalls)" = "$c" ] && ok "ⓖ 상태 파일이 비어 손상돼도 호출 0회(inbox 로 seen 복원)" || ko "ⓖ 상태 손상" "rc=$rc $(( $(acalls) - c ))"
mv "$H/slack-poll-state.json" "$SB/g-state-moved.json"; c=$(acalls); pollh "$H" g5; rc=$?
[ "$rc" = 0 ] && [ "$(acalls)" = "$c" ] && ok "ⓖ 상태 파일이 없어도 호출 0회" || ko "ⓖ 상태 없음" "rc=$rc"
# 유효한 JSON 이지만 acked·ack_sent 자료형이 틀린 경우: 죽지 않고 새 메시지에 1회만
jq '.acked=[1] | .ack_sent=[null,"a",true,{}]' "$H/slack-poll-state.json" > "$H/s.tmp" && mv "$H/s.tmp" "$H/slack-poll-state.json"
hist "$(note "$N.001500"),$(note "$N.001510"),$(note "$N.001520")"; c=$(acalls); pollh "$H" g6; rc=$?
[ "$rc" = 0 ] && [ "$(( $(acalls) - c ))" = 1 ] && [ "$(st "$H" '.acked | type')" = object ] && [ "$(st "$H" '.ack_sent | map(type) | unique | join(",")')" = number ] && ok "ⓖ acked·ack_sent 자료형이 깨져도 종료 코드 0, 새 메시지 1회, 상태 복구" || ko "ⓖ 자료형" "rc=$rc $(( $(acalls) - c )) $(st "$H" '.acked')"
# 30일 지난 키 정리와 개수 상한
H="$SB/hubK"; mkhub "$H"
jq --arg now "$N" '.acked = (([range(0;600) | {key: ("t\(.)"), value: (($now|tonumber) - 100 - .)}] | from_entries) + {old: (($now|tonumber) - 40 * 86400)})' "$H/slack-poll-state.json" > "$H/s.tmp" && mv "$H/s.tmp" "$H/slack-poll-state.json"
hist "$(note "$N.001600")"; pollh "$H" k1; rc=$?
[ "$rc" = 0 ] && [ "$(st "$H" '.acked | length')" -le 500 ] && [ "$(st "$H" '.acked | has("old")')" = false ] && [ "$(st "$H" '.acked | has("'"$N.001600"'")')" = true ] && ok "ⓖ 30일 지난 acked 키 정리, 개수 상한 500 이하(새 키는 유지)" || ko "ⓖ 정리" "rc=$rc $(st "$H" '.acked | length')"
# 호출 도중 강제 종료: 전송 전에 상태가 이미 저장돼 있어 재실행해도 다시 보내지 않음
H="$SB/hubG2"; mkhub "$H"; hist "$(note "$N.001700")"
printf '3' > "$FIX/hang.chat.postMessage"
c=$(acalls); set -m; pollh "$H" g7 & gp=$!; set +m
for _ in $(seq 1 50); do [ "$(acalls)" -gt "$c" ] && break; sleep 0.1; done
kill -9 -- "-$gp" 2>/dev/null; wait "$gp" 2>/dev/null
printf '0' > "$FIX/hang.chat.postMessage"
[ "$(acalls)" -gt "$c" ] && [ "$(st "$H" '.acked | has("'"$N.001700"'")')" = true ] && [ "$(inbox_n "$H")" = 1 ] && ok "ⓖ 전송 중 강제 종료: 상태(acked)와 inbox 가 이미 저장됨" || ko "ⓖ 종료 시점" "$(st "$H" '.acked')"
touch -t 202001010000 "$H/.slack-poll.lock"   # 죽은 실행의 잠금 잔재: 5분이 지난 것으로 보이게 함
c=$(acalls); pollh "$H" g8; rc=$?
[ "$rc" = 0 ] && [ "$(acalls)" = "$c" ] && [ "$(inbox_n "$H")" = 1 ] && ok "ⓖ 강제 종료 뒤 재실행: 재전송·inbox 중복 없음" || ko "ⓖ 재실행" "rc=$rc $(( $(acalls) - c ))"

# ---- 시나리오 I: LRM_ACK_TEXT ------------------------------------------------------------
textcase() { # 이름 환경값 기대 문구 — 새 허브에서 1회 호출, 마지막 호출의 문구를 비교
  local name=$1 val=$2 want=$3 h="$SB/hubI$1" c got
  mkhub "$h"; hist "$(note "$N.0018$4")"
  c=$(acalls); LRM_ACK_TEXT="$val" pollh "$h" "i$name"; local rc=$?
  got=$(acks | tail -n +"$((c + 1))" | jq -r '.text' | head -1)
  [ "$rc" = 0 ] && [ "$(( $(acalls) - c ))" = 1 ] && [ "$got" = "$want" ] && [ "$(errlines "i$name")" = 0 ] && ok "ⓘ $5" || ko "ⓘ $5" "rc=$rc 호출 $(( $(acalls) - c )) 문구 길이 ${#got}"
}
textcase custom '접수했어요' '접수했어요' 01 "LRM_ACK_TEXT 로 문구를 바꿀 수 있음"
LONGV=$(printf 'ㄱ%.0s' $(seq 1 5000))
textcase long "$LONGV" "$(printf 'ㄱ%.0s' $(seq 1 200))" 02 "5000자 값은 200자로 자름"
textcase ctrl $'a\x01b\x7fc\r\nd\te' 'abcde' 03 "제어 문자(줄바꿈·탭·DEL 포함)는 제거"
textcase onlyctrl $'\x01\x02\n' "$DEFAULT_TEXT" 04 "제어 문자만 있으면 기본 문구"
textcase badutf $'\xff\xfeok' 'ok' 05 "잘못된 UTF-8 바이트는 제거하고 죽지 않음"
textcase inject 'x&thread_ts=1&channel=C2' 'x&thread_ts=1&channel=C2' 06 "&·= 가 든 값도 문구 한 덩어리로 인코딩됨"
[ "$(acks | tail -1 | jq -r '[.thread_ts, .channel] | join(",")')" = "$N.001806,Dfake" ] && ok "ⓘ 문구에 든 thread_ts=·channel= 이 실제 인자를 덮어쓰지 않음" || ko "ⓘ 인자 주입" "$(acks | tail -1)"
H="$SB/hubI07"; mkhub "$H"; hist "$(note "$N.001807")"; c=$(acalls); LRM_ACK_TEXT="" pollh "$H" i07
[ "$(acks | tail -n +"$((c + 1))" | jq -r '.text')" = "$DEFAULT_TEXT" ] && ok "ⓘ 빈 값은 기본 문구" || ko "ⓘ 빈 값" ""

# ---- 시나리오 J: 본인 최상위 메시지의 스레드 답글(확인 답장 스레드에 이어 쓴 지시) ----------------
rcalls() { awk '/^conversations\.replies\t/{n++} END{print n+0}' "$CL"; }
H="$SB/hubJ"; mkhub "$H"
hist "$(note "$N.002000")"
c=$(acalls); pollh "$H" j1; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 1 ] && [ "$(( $(acalls) - c ))" = 1 ] && ok "ⓙ 1회차: 최상위 메모 수집 1건, 확인 답장 1회" || ko "ⓙ 1회차" "rc=$rc $(inbox_n "$H") $(( $(acalls) - c ))"
hist "{\"ts\":\"$N.002000\",\"user\":\"$ME\",\"text\":\"메모 $N.002000\",\"thread_ts\":\"$N.002000\",\"reply_count\":2,\"latest_reply\":\"$N.002020\"}"
replies "$(note "$N.002000"),$(botmsg "$N.002010" "$DEFAULT_TEXT"),$(umsg "$N.002020" "추가 지시")"
c=$(acalls); r=$(rcalls); pollh "$H" j2; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 2 ] && [ "$(( $(rcalls) - r ))" = 1 ] && ok "ⓙ 2회차: 본인 메모에 달린 사용자 답글 1건 수집, conversations.replies 1회" || ko "ⓙ 2회차 수집" "rc=$rc inbox $(inbox_n "$H") replies $(( $(rcalls) - r ))"
[ "$(tail -1 "$H/slack-inbox.jsonl" | jq -c '[.type, .parent_ts, .title, .tag, .text] | join("|")' -r)" = "thread_reply|$N.002000|||추가 지시" ] && ok "ⓙ 답글 레코드: type=thread_reply, parent_ts=메모 ts, title·tag 빈 값" || ko "ⓙ 레코드" "$(tail -1 "$H/slack-inbox.jsonl")"
[ "$(grep -c 'bot_id\|Ubot1\|분석 중' "$H/slack-inbox.jsonl" || true)" = 0 ] && [ "$(jq -s '[.[] | select(.ts=="'"$N.002010"'")] | length' "$H/slack-inbox.jsonl")" = 0 ] && [ "$(jq -s '[.[] | select(.ts=="'"$N.002000"'")] | length' "$H/slack-inbox.jsonl")" = 1 ] && ok "ⓙ 봇 확인 답장·부모 메모 자신은 답글로 수집 안 됨" || ko "ⓙ 제외" "$(cat "$H/slack-inbox.jsonl")"
[ "$(st "$H" '.reacted["'"$N.002020"'"]')" = eyes ] && ok "ⓙ 답글에도 읽음 반응(👀) 진행 표 적용" || ko "ⓙ 읽음 반응" "$(st "$H" '.reacted')"
[ "$(( $(acalls) - c ))" = 0 ] && ok "ⓙ 확인 답장 30초 안 같은 스레드 답글 → 답장 0회(스레드 묶기)" || ko "ⓙ 30초 묶기" "$(( $(acalls) - c ))"
pollh "$H" j3; r=$(rcalls); pollh "$H" j4
[ "$(inbox_n "$H")" = 2 ] && [ "$(rcalls)" = "$r" ] && ok "ⓙ 같은 입력 재실행: inbox 중복 0, replies 재조회 0회" || ko "ⓙ 중복" "inbox $(inbox_n "$H")"
# 30초가 지난 뒤 사용자 답글은 확인 답장을 다시 받음
agepast "$H" 40
hist "{\"ts\":\"$N.002000\",\"user\":\"$ME\",\"text\":\"메모 $N.002000\",\"thread_ts\":\"$N.002000\",\"reply_count\":4,\"latest_reply\":\"$N.002040\"}"
replies "$(note "$N.002000"),$(botmsg "$N.002010" "$DEFAULT_TEXT"),$(umsg "$N.002020" "추가 지시"),$(botmsg "$N.002030" "$DEFAULT_TEXT"),$(umsg "$N.002040" "또 지시")"
c=$(acalls); pollh "$H" j5
[ "$(inbox_n "$H")" = 3 ] && [ "$(( $(acalls) - c ))" = 1 ] && [ "$(ack_threads "$c")" = "$N.002000," ] && ok "ⓙ 30초 뒤 새 답글: 수집 1건, 같은 스레드(메모 ts)에 확인 답장 1회" || ko "ⓙ 30초 뒤" "inbox $(inbox_n "$H") $(( $(acalls) - c )) $(ack_threads "$c")"
# 봇 답장만 달린 스레드: 수집 0
H="$SB/hubJ2"; mkhub "$H"
hist "$(note "$N.002100")"; pollh "$H" j6
hist "{\"ts\":\"$N.002100\",\"user\":\"$ME\",\"text\":\"메모 $N.002100\",\"thread_ts\":\"$N.002100\",\"reply_count\":1,\"latest_reply\":\"$N.002110\"}"
replies "$(note "$N.002100"),$(botmsg "$N.002110" "$DEFAULT_TEXT")"
c=$(acalls); r=$(rcalls); pollh "$H" j7
[ "$(inbox_n "$H")" = 1 ] && [ "$(acalls)" = "$c" ] && [ "$(( $(rcalls) - r ))" = 1 ] && ok "ⓙ 봇 확인 답장만 있는 스레드: 수집 0, 확인 답장 0회(루프 없음)" || ko "ⓙ 봇 답장만" "inbox $(inbox_n "$H")"
# 답글이 없는 본인 메시지는 replies 를 부르지 않음
H="$SB/hubJ3"; mkhub "$H"; hist "$(note "$N.002200"),$(note "$N.002201")"
r=$(rcalls); pollh "$H" j8; pollh "$H" j9
[ "$(rcalls)" = "$r" ] && ok "ⓙ reply_count 없는 본인 메시지: conversations.replies 호출 0회(API 호출 증가 없음)" || ko "ⓙ 호출 증가" "$(( $(rcalls) - r ))"
# 배포 전(수집 시작 시각 이전)에 달린 묵은 답글은 새 명령으로 재생하지 않음
H="$SB/hubJ4"; mkhub "$H"; OLDR=$((N - 86400))
jq --argjson s "$((N - 60))" '.own_reply_since = $s' "$H/slack-poll-state.json" > "$H/s.tmp" && mv "$H/s.tmp" "$H/slack-poll-state.json"
hist "{\"ts\":\"$OLDR.002300\",\"user\":\"$ME\",\"text\":\"묵은 메모\",\"thread_ts\":\"$OLDR.002300\",\"reply_count\":1,\"latest_reply\":\"$OLDR.002310\"}"
replies "$(umsg "$OLDR.002300" "묵은 메모"),$(umsg "$OLDR.002310" "승인")"
c=$(acalls); r=$(rcalls); pollh "$H" j10
[ "$(inbox_n "$H")" = 1 ] && [ "$(acalls)" = "$c" ] && [ "$(( $(rcalls) - r ))" = 0 ] && ok "ⓙ 수집 시작 시각 이전의 묵은 스레드 답글: 새 항목 0, 조회 생략" || ko "ⓙ 묵은 답글" "inbox $(inbox_n "$H") replies $(( $(rcalls) - r ))"

# ---- 시나리오 L: 결정 카드 스레드 답글(명령)에는 확인 답장 없음 -------------------------------
H="$SB/hubL"; mkhub "$H"
hist "{\"ts\":\"$N.002400\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":5,\"latest_reply\":\"$N.002450\"}"
replies "$(botmsg "$N.002400" "결정 필요"),$(umsg "$N.002410" "승인"),$(umsg "$N.002420" "결정 1 A"),$(umsg "$N.002430" "보류"),$(umsg "$N.002440" "전부 추천"),$(umsg "$N.002450" "이건 어떻게 하죠")"
printf '{"ts":"%s.002400","at":%s,"title":"결정 5","tag":"t"}\n' "$N" "$N" > "$H/slack-sent.jsonl"
c=$(acalls); pollh "$H" l1; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 5 ] && [ "$(( $(acalls) - c ))" = 1 ] && [ "$(acks | tail -1 | jq -r .thread_ts)" = "$N.002400" ] && ok "ⓛ 결정 카드 스레드: 승인·결정 1 A·보류·전부 추천 4건은 답장 0, 일반 답글 1건만 답장 1회(수집은 5건 모두)" || ko "ⓛ 명령 제외" "rc=$rc inbox $(inbox_n "$H") $(( $(acalls) - c ))"
H="$SB/hubL2"; mkhub "$H"
hist "{\"ts\":\"$N.002500\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":1,\"latest_reply\":\"$N.002510\"}"
replies "$(botmsg "$N.002500" "결정 필요"),$(umsg "$N.002510" "승인")"
printf '{"ts":"%s.002500","at":%s,"title":"결정 6","tag":"t"}\n' "$N" "$N" > "$H/slack-sent.jsonl"
c=$(acalls); pollh "$H" l2
[ "$(acalls)" = "$c" ] && [ "$(inbox_n "$H")" = 1 ] && [ "$(st "$H" '(.acked // {}) | length')" = 0 ] && ok "ⓛ 결정 카드 스레드 답글 '승인' 하나 → 답장 0회, acked 기록 없음" || ko "ⓛ 승인" "$(( $(acalls) - c ))"
H="$SB/hubL3"; mkhub "$H"; hist "$(umsg "$N.002600" "승인 1"),$(umsg "$N.002610" "보류 사유")"
c=$(acalls); pollh "$H" l3
[ "$(acalls)" = "$c" ] && [ "$(inbox_n "$H")" = 2 ] && ok "ⓛ 최상위 명령('승인 1'·'보류 사유')도 답장 0회" || ko "ⓛ 최상위 명령" "$(( $(acalls) - c ))"
replies ""

# ---- 시나리오 M: 회차 상한이 창 상한과 따로 걸림(변이: 회차 상한 9 를 잡음) ----------------------
# ACK_WINDOW 를 0 으로 바꾼 사본(60초 창이 항상 비어 창 상한이 안 걸림)에서도 한 회차는 ACK_MAX(3)회까지만
VAR="$SB/poll-nowin.sh"
sed 's/^ACK_WINDOW, ACK_MAX = 60, 3 /ACK_WINDOW, ACK_MAX = 0, 3 /' "$POLL" > "$VAR"
if cmp -s "$POLL" "$VAR"; then ko "ⓜ 변형 사본 생성" "치환 실패(상수 선언이 바뀜)"; else
  H="$SB/hubM"; mkhub "$H"
  hist "$msgs"
  c=$(acalls); LRM_HUB_DIR="$H" SLACK_API_BASE="$API" bash "$VAR" --once > "$SB/m1.out" 2> "$SB/m1.err"; rc=$?
  [ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 5 ] && [ "$(( $(acalls) - c ))" = 3 ] && ok "ⓜ 창 상한이 없어도 한 회차 확인 답장은 3회(회차 상한)" || ko "ⓜ 회차 상한" "rc=$rc $(( $(acalls) - c ))"
fi

# ---- 시나리오 N: 시간 예산 부족 -----------------------------------------------------------
# 회차 예산(LRM_POLL_BUDGET)이 5초 안쪽이면 호출 없이 건너뛰고 사유 budget 을 알림. 재시도 없음
H="$SB/hubN"; mkhub "$H"; hist "$(note "$N.002700")"
c=$(acalls); LRM_POLL_BUDGET=3 pollh "$H" n1; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 1 ] && [ "$(acalls)" = "$c" ] && [ "$(errlines n1)" = 1 ] && grep -q 'budget' "$SB/n1.err" && ! grep -q 'http' "$SB/n1.err" && ok "ⓝ 예산 부족: 종료 코드 0, 수집 정상, 호출 0회, stderr 한 줄(budget, http 아님)" || ko "ⓝ 예산 부족" "rc=$rc $(( $(acalls) - c )) $(cat "$SB/n1.err")"
[ "$(st "$H" '(.acked // {}) | length')" = 0 ] && [ "$(st "$H" 'has("ack_disabled_reason")')" = false ] && ok "ⓝ 건너뛴 항목은 acked 기록·정지 상태 없음" || ko "ⓝ 상태" "$(st "$H" '.acked')"
c=$(acalls); pollh "$H" n2
[ "$(acalls)" = "$c" ] && ok "ⓝ 건너뛴 항목은 다음 회차에도 재시도하지 않음(at-most-once)" || ko "ⓝ 재시도" "$(( $(acalls) - c ))"

# ---- 전달 완료 표시: 메인 전달 기록 → 확인 답장 chat.update ---------------------------------------------
# slack_push 가 남기는 slack-push-delivered.jsonl 을 직접 써서 시험(push 자체는 push.sh 가 시험)
ucalls() { awk '/^POST:chat\.update\t/{n++} END{print n+0}' "$CL"; }
updates() { python3 -I "$HERE/ack_calls.py" "$CL" update; }
DSEQ=1000 # 전달 기록의 단조 증가 번호(seq). 폴러는 이 값을 처리 위치로 씀
deliver() { DSEQ=$((DSEQ + 1)); printf '{"seq":%s,"id":"%s","roots":%s}\n' "$DSEQ" "$2" "$3" >> "$1/slack-push-delivered.jsonl"; }
seed() { # 허브 폴더, 루트 ts...: 확인 답장 ts 가 보관된 상태(acked 포함)를 직접 만듦
  local h="$1"; shift; local j='{}' r
  for r in "$@"; do j=$(printf '%s' "$j" | jq -c --arg r "$r" '.[$r] = {ts: ("8888." + $r), ch: "Dfake", n: 0}'); done
  jq --argjson m "$j" --argjson t "$N" '.ack_msgs = $m | .acked = ($m | with_entries(.value = $t))' "$h/slack-poll-state.json" > "$h/s.tmp" && mv "$h/s.tmp" "$h/slack-poll-state.json"
}
# 앞 시나리오가 남긴 chat.postMessage 고정 응답을 ts 가 있는 정상 응답으로 되돌림
printf '{"ok":true,"channel":"Dfake","ts":"9999.000001"}' > "$FIX/chat.postMessage.json"
H="$SB/hubU"; mkhub "$H"; hist "$(note "$N.003000")"
c=$(acalls); pollh "$H" u1
[ "$(( $(acalls) - c ))" = 1 ] && [ "$(st "$H" '.ack_msgs["'"$N.003000"'"].n')" = 0 ] && st "$H" '.ack_msgs["'"$N.003000"'"].ts' | grep -q '^9999\.' && [ "$(st "$H" '.ack_msgs["'"$N.003000"'"].ch')" = Dfake ] && ok "확인 답장 응답의 ts·채널이 ack_msgs 에 보관됨(기존 acked 표는 그대로)" || ko "ts 보관" "$(st "$H" '.ack_msgs')"
[ "$(st "$H" '.acked["'"$N.003000"'"] | type')" = number ] && ok "기존 acked 표 형식(스레드 → 시각 숫자) 호환 유지" || ko "acked 호환" ""
ACKTS=$(st "$H" '.ack_msgs["'"$N.003000"'"].ts')
pollh "$H" u2
[ "$(ucalls)" = 0 ] && ok "전달 기록이 없으면 chat.update 0회" || ko "기록 없음" "$(ucalls)"
deliver "$H" 5-1 "{\"$N.003000\":1}"
pollh "$H" u3; rc=$?
[ "$rc" = 0 ] && [ "$(ucalls)" = 1 ] && [ "$(updates | jq -r '.ts')" = "$ACKTS" ] && [ "$(updates | jq -r '.channel')" = Dfake ] && [ "$(updates | jq -r '.text')" = "📨 메인에 전달했어요 (1건)" ] && ok "전달 1건 → 그 스레드의 확인 답장에 chat.update 1회(POST), 문구 '📨 메인에 전달했어요 (1건)'" || ko "update 1회" "rc=$rc $(updates)"
pollh "$H" u4
[ "$(ucalls)" = 1 ] && [ "$(errlines u3)" = 0 ] && ok "이미 갱신한 기록은 다음 회차에 재호출 0, 정상 시 stderr 없음" || ko "재호출" "$(ucalls)"
deliver "$H" 9-2 "{\"$N.003000\":2}"
pollh "$H" u5
[ "$(ucalls)" = 2 ] && [ "$(updates | jq -r '.text' | tail -1)" = "📨 메인에 전달했어요 (3건)" ] && [ "$(updates | jq -r '.ts' | sort -u | wc -l | tr -d ' ')" = 1 ] && ok "같은 스레드의 다음 배치(2건): 호출 1회, 건수는 누적 '(3건)'" || ko "누적" "$(updates | tail -1)"
grep -q 'POST:chat.update' "$CL" && ! grep -qF -- "$FAKE_TOKEN" "$CL" && ok "chat.update 는 POST 로 호출하고 토큰은 요청 기록에 없음" || ko "POST·토큰" ""

# 실패해도 나머지 스레드를 계속, 사유 코드만 stderr 1줄씩, 재시도 없음
H="$SB/hubU2"; mkhub "$H"; hist ""; pollh "$H" v0; seed "$H" "$N.003100" "$N.003200"
errjson chat.update message_not_found
deliver "$H" 7-1 "{\"$N.003100\":1,\"$N.003200\":1}"
c=$(ucalls); pollh "$H" v1; rc=$?
[ "$rc" = 0 ] && [ "$(( $(ucalls) - c ))" = 2 ] && [ "$(errlines v1)" = 2 ] && [ "$(grep -c 'message_not_found' "$SB/v1.err")" = 2 ] && ! grep -qF -- "$FAKE_TOKEN" "$SB/v1.err" && ok "실패(message_not_found)해도 다음 스레드 계속: 호출 2회, 종료 코드 0, stderr 사유 코드 1줄씩" || ko "실패 계속" "rc=$rc $(( $(ucalls) - c )) $(cat "$SB/v1.err")"
okjson chat.update
c=$(ucalls); pollh "$H" v2
[ "$(ucalls)" = "$c" ] && ok "실패한 갱신은 재시도하지 않음(at-most-once)" || ko "재시도" "$(( $(ucalls) - c ))"
H="$SB/hubU3"; mkhub "$H"; hist ""; pollh "$H" w0; seed "$H" "$N.003300" "$N.003400"
errjson chat.update missing_scope
deliver "$H" 7-2 "{\"$N.003300\":1,\"$N.003400\":1}"
c=$(ucalls); pollh "$H" w1
[ "$(( $(ucalls) - c ))" = 1 ] && [ "$(errlines w1)" = 1 ] && grep -q 'missing_scope' "$SB/w1.err" && ok "권한·채널 문제(missing_scope)는 그 회차 갱신을 중단(호출 1회, stderr 1줄)" || ko "권한 중단" "$(( $(ucalls) - c ))"
okjson chat.update

# 회차 상한 5회, 나머지는 다음 회차
H="$SB/hubU4"; mkhub "$H"; hist ""; pollh "$H" x0
roots=(); for i in 1 2 3 4 5 6 7; do roots+=("$N.00350$i"); done
seed "$H" "${roots[@]}"
rj=$(printf '"%s":1,' "${roots[@]}"); deliver "$H" 8-1 "{${rj%,}}"
c=$(ucalls); pollh "$H" x1
[ "$(( $(ucalls) - c ))" = 5 ] && ok "회차당 chat.update 상한 5회" || ko "상한" "$(( $(ucalls) - c ))"
c=$(ucalls); pollh "$H" x2; pollh "$H" x3
[ "$(( $(ucalls) - c ))" = 2 ] && [ "$(updates | jq -r '.ts' | sort -u | wc -l | tr -d ' ')" -ge 7 ] && ok "남은 2건은 다음 회차에 처리, 그 뒤 재호출 0(7스레드 각 1회)" || ko "상한 이월" "$(( $(ucalls) - c ))"

# 확인 답장이 없는 스레드·끄기
H="$SB/hubU5"; mkhub "$H"; hist ""; pollh "$H" y0; seed "$H" "$N.003600"
deliver "$H" 6-1 "{\"$N.003999\":1}"
c=$(ucalls); pollh "$H" y1
[ "$(ucalls)" = "$c" ] && [ "$(st "$H" '.upd_pos')" = "$DSEQ" ] && [ "$(st "$H" 'has("upd_part")')" = false ] && ok "확인 답장 ts 가 없는 스레드(답장 안 보냄·옛 답장)는 호출 없이 처리 위치(upd_pos=기록 seq)만 넘김" || ko "ts 없음" "$(st "$H" '.upd_pos')"
for off in "LRM_ACK_UPDATE=off" "LRM_ACK=off"; do
  H="$SB/hubU6-${off%%=*}"; mkhub "$H"; hist ""; pollh "$H" z0; seed "$H" "$N.003700"; deliver "$H" 4-1 "{\"$N.003700\":1}"
  c=$(ucalls); env "$off" LRM_HUB_DIR="$H" SLACK_API_BASE="$API" bash "$POLL" --once > "$SB/z1.out" 2> "$SB/z1.err"
  [ "$(ucalls)" = "$c" ] && ok "$off: chat.update 0회" || ko "$off" "$(( $(ucalls) - c ))"
done

# 결함 2: 처리 위치는 단조 증가 seq. 전달 기록이 1000줄→500줄로 잘리는 장기 운용(1300회차)에서도 회차당 호출 1회·건수 부풀림 0.
# 수정 전에는 처리 표시(500키 상한)가 잘린 기록을 놓쳐 503회차부터 회차당 5회 호출·'(5건)' 으로 바뀜
SIMHUB="$SB/simhub"; mkdir -p "$SIMHUB"
sim=$(python3 -I "$HERE/done_sim.py" "$POLL" "$HERE/../../hubs/scripts/slack_push.py" "$SIMHUB" 1300 2>&1 | tail -1)
[ "$(printf '%s' "$sim" | jq -r '[.rounds, .calls, .bad] | join(",")' 2>/dev/null)" = "1300,1300,0" ] && ok "ⓤ 1300회차 시뮬레이션(기록 1000줄 초과 정리 포함): 회차당 chat.update 정확히 1회, 오래된 기록 재처리·건수 부풀림 0" || ko "ⓤ 장기 시뮬레이션" "$sim"
# 처리 위치 저장: 기록 파일이 통째로 새로 시작돼도(seq 가 시각 기반이라 계속 커짐) 이미 처리한 기록은 건드리지 않음
H="$SB/hubU7"; mkhub "$H"; hist ""; pollh "$H" t0; seed "$H" "$N.003800" "$N.003810"
deliver "$H" 3-1 "{\"$N.003800\":1}"; pollh "$H" t1
c=$(ucalls); pollh "$H" t2; deliver "$H" 3-2 "{\"$N.003810\":1}"; pollh "$H" t3
[ "$(( $(ucalls) - c ))" = 1 ] && [ "$(st "$H" '.upd_pos')" = "$DSEQ" ] && ok "ⓤ 처리 위치(upd_pos)보다 큰 seq 의 기록만 처리(재실행 호출 0·새 기록 1회)" || ko "ⓤ 위치" "$(( $(ucalls) - c )) pos=$(st "$H" '.upd_pos')"
# 한 기록 안에서 회차 상한(5)으로 끊겨도 이어서 처리: 7스레드 기록 → 5회 + 2회, 중복 0 (위 '회차 상한' 시험과 같은 시나리오의 상태 확인)
[ "$(st "$H" 'has("upd_seen")')" = false ] && ok "ⓤ 옛 처리 표시(upd_seen)는 더 쓰지 않음" || ko "ⓤ upd_seen" ""

# 결함 8: 같은 스레드에 앞 확인 답장이 아직 '집계 중'이면 새 답장을 보내지 않음(보내면 앞 답장이 영구히 '집계 중'으로 남음)
H="$SB/hubV"; mkhub "$H"
RT="$N.004000"
printf '{"ts":"%s","at":%s,"title":"결정 8","tag":"t"}\n' "$RT" "$N" > "$H/slack-sent.jsonl"
thr() { # 답글 ts 목록(공백 구분). 마지막 답글이 latest_reply
  local list="" last="" t cnt=0
  for t in "$@"; do list="$list,$(umsg "$t" "답글 $t")"; last=$t; cnt=$((cnt + 1)); done
  hist "{\"ts\":\"$RT\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":$cnt,\"latest_reply\":\"$last\"}"
  replies "$(botmsg "$RT" "결정 필요")$list"
}
thr "$N.004100"; c=$(acalls); pollh "$H" v1
[ "$(( $(acalls) - c ))" = 1 ] && [ "$(st "$H" '.ack_msgs["'"$RT"'"].n')" = 0 ] && ok "ⓥ 첫 답글 → 확인 답장 1회(집계 중)" || ko "ⓥ 첫 답장" "$(( $(acalls) - c ))"
ACK1=$(st "$H" '.ack_msgs["'"$RT"'"].ts')
agepast "$H" 40; thr "$N.004100" "$N.004200"; c=$(acalls); pollh "$H" v2
[ "$(inbox_n "$H")" = 2 ] && [ "$(acalls)" = "$c" ] && [ "$(st "$H" '.ack_msgs["'"$RT"'"].ts')" = "$ACK1" ] && ok "ⓥ 30초 뒤 같은 스레드 새 답글: 앞 답장이 아직 '집계 중'이므로 새 답장을 보내지 않고 앞 답장 ts 를 그대로 보관(수집은 정상)" || ko "ⓥ 두 번째 답장" "calls=$(( $(acalls) - c )) ts=$(st "$H" '.ack_msgs["'"$RT"'"].ts')"
deliver "$H" 4-2 "{\"$RT\":2}"; c=$(ucalls); pollh "$H" v3
[ "$(( $(ucalls) - c ))" = 1 ] && [ "$(updates | jq -r '.ts' | tail -1)" = "$ACK1" ] && [ "$(updates | jq -r '.text' | tail -1)" = "📨 메인에 전달했어요 (2건)" ] && ok "ⓥ 두 답글이 한 묶음으로 전달되면 앞 답장이 '(2건)' 으로 갱신됨(영구 '집계 중' 없음)" || ko "ⓥ 갱신" "$(updates | tail -1)"
agepast "$H" 40; thr "$N.004100" "$N.004200" "$N.004300"; c=$(acalls); pollh "$H" v4
[ "$(( $(acalls) - c ))" = 1 ] && [ "$(st "$H" '.ack_msgs["'"$RT"'"].n')" = 0 ] && ok "ⓥ 앞 답장이 갱신된 뒤의 새 답글에는 다시 확인 답장 1회" || ko "ⓥ 갱신 뒤" "$(( $(acalls) - c ))"

# ---- ⓗ 토큰 비노출 ------------------------------------------------------------------------
leak=$(grep -rlF -- "$FAKE_TOKEN" "$SB" 2>/dev/null | grep -v '/slack-bot\.env$' || true)
[ -z "$leak" ] && ok "ⓗ 토큰이 stdout·stderr·상태·inbox·가짜 API 호출 기록에 없음(설정 파일 제외 전체 검색)" || ko "ⓗ 토큰 노출" "$leak"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
