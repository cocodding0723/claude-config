#!/bin/bash
# slack_poll.sh 케이스: 정상·변경분만 조회·반응 잘림 보강·429 재시도·429 회차의 즉답 호출·중복 실행
. "$(dirname "$0")/lib.sh"
POLL="$ROOT/lrm-hubs/slack_poll.sh"
export LRM_RETRY_CAP=0
NOW=$(date +%s)

OPEN='"conversations.open":[{"ok":true,"channel":{"id":"DTEST"}}]'
# 봇 메시지 B1(반응 a, 답글 1), B2(답글 2, 이미 읽음), 사용자 최상위 명령
HIST='"conversations.history":[{"ok":true,"messages":[
 {"ts":"1000.1","user":"UBOT","text":"결정1","reactions":[{"name":"a","users":["UTEST"],"count":1}],"reply_count":1,"latest_reply":"1000.9"},
 {"ts":"1000.2","user":"UBOT","text":"결정2","reply_count":2,"latest_reply":"1000.8"},
 {"ts":"1000.3","user":"UTEST","text":"대기"}]}]'
REPLIES='"conversations.replies":[{"ok":true,"messages":[{"ts":"1000.1","user":"UBOT"},{"ts":"1000.9","user":"UTEST","text":"결정1 A"}]}]'

echo "[정상] 목록 1회 + 변경된 스레드만 답글 조회"
new_hub
printf '{"seen":{},"latest":{"1000.2":"1000.8"}}' > "$HUB/slack-poll-state.json"
start_stub "{$OPEN,$HIST,$REPLIES}"
OUT=$(bash "$POLL" --once --json); RC=$?
check "종료 코드" 0 "$RC"
check "목록 조회 1회" 1 "$(count conversations.history)"
check "답글 조회 1회(읽은 스레드 제외)" 1 "$(count conversations.replies)"
check "반응 조회 0회" 0 "$(count reactions.get)"
check "inbox 3줄(반응·명령·답글 명령)" 3 "$(wc -l < "$HUB/slack-inbox.jsonl" | tr -d ' ')"
check "즉답 스크립트 호출" 1 "$(wc -l < "$HUB/cmd-calls" | tr -d ' ')"
check "JSON 요약" '{"ok": true, "added": 3}' "$OUT"
echo "[재실행] 중복 기록·재조회 없음"
: > "$LOG"
bash "$POLL" --once >/dev/null
check "inbox 그대로 3줄" 3 "$(wc -l < "$HUB/slack-inbox.jsonl" | tr -d ' ')"
check "답글 재조회 0회" 0 "$(count conversations.replies)"
stop_stub

echo "[반응 잘림] 본인 반응이 안 보이면 그 메시지만 reactions.get"
new_hub
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
check "inbox 변화 없음" 0 "$([ -f "$HUB/slack-inbox.jsonl" ] && wc -l < "$HUB/slack-inbox.jsonl" | tr -d ' ' || echo 0)"
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
