#!/bin/bash
# slack_poll.sh 케이스: 정상·변경분만 조회·sent 범위·latest_reply 폴백·반응 잘림·429·시간 예산·상태 손상·중복 실행
. "$(dirname "$0")/lib.sh"
POLL="$SCRIPTS/slack_poll.sh"
export LRM_RETRY_CAP=0
NOW=$(date +%s)

OPEN='"conversations.open":[{"ok":true,"channel":{"id":"DTEST"}}]'
# 봇 메시지 B1(반응 a, 답글 1), B2(답글 2, 이미 읽음), 사용자 최상위 명령
HIST='"conversations.history":[{"ok":true,"messages":[
 {"ts":"1000.1","user":"UBOT","text":"결정1","reactions":[{"name":"a","users":["UTEST"],"count":1}],"reply_count":1,"latest_reply":"1000.9"},
 {"ts":"1000.2","user":"UBOT","text":"결정2","reply_count":2,"latest_reply":"1000.8"},
 {"ts":"1000.3","user":"UTEST","text":"대기"}]}]'
REPLIES='"conversations.replies":[{"ok":true,"messages":[{"ts":"1000.1","user":"UBOT"},{"ts":"1000.9","user":"UTEST","text":"결정1 A"}]}]'
# slack_notify.sh 가 기록한 메시지만 반응·답글 대상
seed_sent() {
  for ts in "$@"; do
    echo "{\"at\":$NOW,\"channel\":\"DTEST\",\"ts\":\"$ts\",\"thread_ts\":\"\",\"kind\":\"text\",\"title\":\"결정: $ts\",\"tag\":\"\"}" >> "$HUB/slack-sent.jsonl"
  done
}
inbox_lines() { [ -f "$HUB/slack-inbox.jsonl" ] && wc -l < "$HUB/slack-inbox.jsonl" | tr -d ' ' || echo 0; }

echo "[정상] 목록 1회 + 변경된 스레드만 답글 조회"
new_hub; seed_sent 1000.1 1000.2
printf '{"seen":{},"latest":{"1000.2":"1000.8"}}' > "$HUB/slack-poll-state.json"
start_stub "{$OPEN,$HIST,$REPLIES}"
OUT=$(bash "$POLL" --once --json); RC=$?
check "종료 코드" 0 "$RC"
check "목록 조회 1회" 1 "$(count conversations.history)"
check "답글 조회 1회(읽은 스레드 제외)" 1 "$(count conversations.replies)"
check "반응 조회 0회" 0 "$(count reactions.get)"
check "inbox 3줄(반응·명령·답글 명령)" 3 "$(inbox_lines)"
check "즉답 스크립트 호출" 1 "$(wc -l < "$HUB/cmd-calls" | tr -d ' ')"
check "JSON 요약" '{"ok": true, "added": 3}' "$OUT"
echo "[재실행] 중복 기록·재조회 없음"
: > "$LOG"
bash "$POLL" --once >/dev/null
check "inbox 그대로 3줄" 3 "$(inbox_lines)"
check "답글 재조회 0회" 0 "$(count conversations.replies)"
stop_stub

echo "[sent 범위] 기록에 없는 봇 메시지(대기 목록 등)의 반응·답글은 무시"
new_hub; seed_sent 1000.1
start_stub "{$OPEN,\"conversations.history\":[{\"ok\":true,\"messages\":[
 {\"ts\":\"1000.1\",\"user\":\"UBOT\",\"reactions\":[{\"name\":\"a\",\"users\":[\"UTEST\"],\"count\":1}]},
 {\"ts\":\"7000.1\",\"user\":\"UBOT\",\"text\":\"대기 목록\",\"reactions\":[{\"name\":\"white_check_mark\",\"users\":[\"UTEST\"],\"count\":1}],\"reply_count\":1,\"latest_reply\":\"7000.2\"}]}]}"
bash "$POLL" --once >/dev/null
check "기록된 메시지 반응만 inbox" 1 "$(inbox_lines)"
check "대기 목록 반응 미기록" 0 "$(grep -c '7000.1' "$HUB/slack-inbox.jsonl")"
check "대기 목록 답글 조회 0회" 0 "$(count conversations.replies)"
stop_stub

echo "[latest_reply 없음] reply_count 로 비교"
new_hub; seed_sent 3000.1
start_stub "{$OPEN,\"conversations.history\":[{\"ok\":true,\"messages\":[{\"ts\":\"3000.1\",\"user\":\"UBOT\",\"reply_count\":1}]}],$REPLIES}"
bash "$POLL" --once >/dev/null
check "첫 회차 답글 조회 1회" 1 "$(count conversations.replies)"
: > "$LOG"; bash "$POLL" --once >/dev/null
check "같은 개수면 재조회 0회" 0 "$(count conversations.replies)"
stop_stub
new_hub; seed_sent 3000.1
printf '{"seen":{},"latest":{"3000.1":"n1"}}' > "$HUB/slack-poll-state.json"
start_stub "{$OPEN,\"conversations.history\":[{\"ok\":true,\"messages\":[{\"ts\":\"3000.1\",\"user\":\"UBOT\",\"reply_count\":2}]}],$REPLIES}"
bash "$POLL" --once >/dev/null
check "개수가 늘면 조회 1회" 1 "$(count conversations.replies)"
stop_stub

echo "[반응 잘림] 본인 반응이 안 보이면 그 메시지만 reactions.get"
new_hub; seed_sent 2000.1
start_stub "{$OPEN,\"conversations.history\":[{\"ok\":true,\"messages\":[{\"ts\":\"2000.1\",\"user\":\"UBOT\",\"reactions\":[{\"name\":\"b\",\"users\":[\"UX\"],\"count\":3}]}]}],\"reactions.get\":[{\"ok\":true,\"message\":{\"reactions\":[{\"name\":\"b\",\"users\":[\"UX\",\"UTEST\"],\"count\":3}]}}]}"
bash "$POLL" --once >/dev/null
check "reactions.get 1회" 1 "$(count reactions.get)"
check "반응 기록" 1 "$(grep -c '"emoji": "b"' "$HUB/slack-inbox.jsonl")"
stop_stub

echo "[429] 한 번 거절 뒤 재시도로 성공"
new_hub
start_stub "{$OPEN,\"conversations.history\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"0\"},\"ok\":false},{\"ok\":true,\"messages\":[]}]}"
bash "$POLL" --once >/dev/null; RC=$?
check "종료 코드" 0 "$RC"
check "목록 조회 2회(재시도 1)" 2 "$(count conversations.history)"
stop_stub

echo "[429 지속] 실패해도 즉답·대기 목록 호출은 유지"
new_hub
echo 0 > "$HUB/.pending-last"  # 2시간 주기 도래
start_stub "{$OPEN,\"conversations.history\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"0\"},\"ok\":false}]}"
bash "$POLL" --once >/dev/null 2>&1; RC=$?
check "종료 코드 1" 1 "$RC"
check "목록 조회 3회(재시도 2)" 3 "$(count conversations.history)"
check "즉답 스크립트 호출" 1 "$(wc -l < "$HUB/cmd-calls" | tr -d ' ')"
check "대기 목록 스크립트 호출" 1 "$(wc -l < "$HUB/pending-calls" | tr -d ' ')"
check "inbox 변화 없음" 0 "$(inbox_lines)"
stop_stub

echo "[Retry-After] 알려 준 시간만큼 쉼"
new_hub
start_stub "{$OPEN,\"conversations.history\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"2\"},\"ok\":false},{\"ok\":true,\"messages\":[]}]}"
T0=$(now_ms); LRM_RETRY_CAP=20 bash "$POLL" --once >/dev/null; T1=$(now_ms)
check "대기 2초 이상" yes "$([ $((T1 - T0)) -ge 1900 ] && echo yes || echo no)"
stop_stub

echo "[시간 예산] 남은 예산보다 긴 대기는 쉬지 않고 실패 처리"
new_hub
start_stub "{$OPEN,\"conversations.history\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"30\"},\"ok\":false}]}"
T0=$(now_ms); LRM_RETRY_CAP=20 LRM_POLL_BUDGET=2 bash "$POLL" --once >/dev/null 2>&1; RC=$?; T1=$(now_ms)
check "종료 코드 1" 1 "$RC"
check "2초 안에 종료" yes "$([ $((T1 - T0)) -lt 2500 ] && echo yes || echo no)"
check "목록 조회 1회" 1 "$(count conversations.history)"
stop_stub

echo "[상태 손상] 백업 후 초기화, inbox 로 중복 방지 복원"
new_hub; seed_sent 1000.1 1000.2
printf '{"seen":{},"latest":{"1000.2":"1000.8"}}' > "$HUB/slack-poll-state.json"
start_stub "{$OPEN,$HIST,$REPLIES}"
bash "$POLL" --once >/dev/null
check "첫 수집 3줄" 3 "$(inbox_lines)"
printf '{"seen": {broken' > "$HUB/slack-poll-state.json"
bash "$POLL" --once >/dev/null 2>&1; RC=$?
check "종료 코드" 0 "$RC"
check "손상 파일 백업" 1 "$(ls "$HUB" | grep -c 'slack-poll-state.json.corrupt-')"
check "복구 뒤 중복 기록 없음" 3 "$(inbox_lines)"
check "상태 파일 복원" yes "$(python3 -c 'import json,sys;json.load(open(sys.argv[1]))' "$HUB/slack-poll-state.json" && echo yes || echo no)"
stop_stub

echo "[상태 없음] 손상 직후 회차가 API 실패로 끝나도 다음 회차에 중복 기록 없음"
new_hub; seed_sent 1000.1 1000.2
printf '{"seen":{},"latest":{"1000.2":"1000.8"}}' > "$HUB/slack-poll-state.json"
start_stub "{$OPEN,$HIST,$REPLIES}"
bash "$POLL" --once >/dev/null
check "첫 수집 3줄" 3 "$(inbox_lines)"
printf '{"seen": {broken' > "$HUB/slack-poll-state.json"
swap_stub "{$OPEN,\"conversations.history\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"0\"},\"ok\":false}]}"
bash "$POLL" --once >/dev/null 2>&1; RC=$?
check "실패 회차 종료 코드 1" 1 "$RC"
check "상태 파일 없음(백업만 남음)" no "$([ -e "$HUB/slack-poll-state.json" ] && echo yes || echo no)"
swap_stub "{$OPEN,$HIST,$REPLIES}"
bash "$POLL" --once >/dev/null 2>&1; RC=$?
check "다음 회차 종료 코드" 0 "$RC"
check "중복 기록 없음" 3 "$(inbox_lines)"
stop_stub

echo "[총 예산] 앞 단계가 쓴 시간만큼 뒤 단계 예산이 줄어듦"
new_hub
echo 0 > "$HUB/.pending-last"
printf '#!/bin/bash\necho "$LRM_PENDING_BUDGET" >> "%s/pending-budget"\nsleep 3\n' "$HUB" > "$HUB/slack_pending.sh"
printf '#!/bin/bash\necho "$LRM_PENDING_BUDGET" >> "%s/cmd-budget"\n' "$HUB" > "$HUB/slack_cmd.sh"
start_stub "{$OPEN,\"conversations.history\":[{\"ok\":true,\"messages\":[]}]}"
LRM_TOTAL_BUDGET=4 bash "$POLL" --once >/dev/null
check "대기 목록 단계 예산은 총 예산 이내" yes "$([ "$(cat "$HUB/pending-budget")" -le 4 ] && echo yes || echo no)"
check "즉답 단계 예산은 남은 시간만큼(최소 1초)" 1 "$(cat "$HUB/cmd-budget")"
stop_stub
new_hub
echo 0 > "$HUB/.pending-last"
printf '#!/bin/bash\necho "$LRM_PENDING_BUDGET" >> "%s/pending-budget"\n' "$HUB" > "$HUB/slack_pending.sh"
start_stub "{$OPEN,\"conversations.history\":[{\"ok\":true,\"messages\":[]}]}"
bash "$POLL" --once >/dev/null
check "기본 상한 120초" 120 "$(cat "$HUB/pending-budget")"
stop_stub

echo "[중복 실행] 잠금이 있으면 호출 없이 종료"
new_hub
mkdir "$HUB/.slack-poll.lock"
start_stub "{$OPEN}"
OUT=$(bash "$POLL" --once --json); RC=$?
check "종료 코드" 0 "$RC"
check "건너뜀 표시" '{"ok":true,"added":0,"skipped":"locked"}' "$OUT"
check "API 호출 0회" 0 "$(wc -l < "$LOG" | tr -d ' ')"
stop_stub

summary
