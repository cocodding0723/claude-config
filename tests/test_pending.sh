#!/bin/bash
# slack_pending.sh 케이스: 정상·원문 이력·수정 상한·수정 재시도·빈 목록·이력 실패 차단·전송 직후 이력 실패·429·시간 예산·중복 실행
. "$(dirname "$0")/lib.sh"
PEND="$SCRIPTS/slack_pending.sh"
export LRM_RETRY_CAP=0
NOW=$(date +%s)

# 결정 3건: D1 미답, D2 답함(반응 a), D3 미답
seed() {
  cat > "$HUB/slack-sent.jsonl" <<J
{"at":$NOW,"channel":"DTEST","ts":"1.1","thread_ts":"","kind":"text","title":"결정: 첫째","tag":""}
{"at":$NOW,"channel":"DTEST","ts":"1.2","thread_ts":"","kind":"text","title":"결정: 둘째","tag":""}
{"at":$NOW,"channel":"DTEST","ts":"1.3","thread_ts":"","kind":"text","title":"결정: 셋째","tag":""}
J
  echo '{"type":"reaction","emoji":"a","action":"a","parent_ts":"1.2","received_at":'"$NOW"'}' > "$HUB/slack-inbox.jsonl"
}
answer() { echo '{"type":"reaction","emoji":"a","action":"a","parent_ts":"'"$1"'","received_at":'"$NOW"'}' >> "$HUB/slack-inbox.jsonl"; }
seed_posted() { echo '{"at":'"$NOW"',"event":"pending_posted","ts":"8.1","channel":"DTEST","titles":["결정: 첫째"]}' > "$(H_FILE)"; }
H_FILE() { echo "$HUB/slack-pending-history.jsonl"; }
ev() { grep -c "\"event\": \"$1\"" "$(H_FILE)" || true; }
PERMA='"chat.getPermalink":[{"ok":true,"permalink":"https://example.test/p"}]'
UPD='"chat.update":[{"ok":true}]'
DEL='"chat.delete":[{"ok":true}]'
# 수정 직전 원문 조회 응답. 둘째는 여러 줄 원문
ORIG='"conversations.history":[{"ok":true,"messages":[{"ts":"1.1","text":"결정: 첫째"},{"ts":"1.2","text":"결정: 둘째\n원문 본문 전체"},{"ts":"1.3","text":"결정: 셋째"}]}]'
POST1='"chat.postMessage":[{"ok":true,"ts":"9.1"},{"ok":true,"ts":"9.2"}]'

echo "[정상] 답한 결정 수정 + 새 대기 메시지, 처음엔 삭제 없음"
new_hub; seed
start_stub "{$PERMA,$UPD,$DEL,$ORIG,$POST1}"
OUT=$(bash "$PEND"); RC=$?
check "종료 코드" 0 "$RC"
check "메시지 수정 1회(답한 결정만)" 1 "$(count chat.update)"
check "수정 본문에 결과 표시" 1 "$(grep -c '✅ 결정: 둘째 — 답: a' "$LOG")"
check "새 대기 메시지 1회" 1 "$(count chat.postMessage)"
check "삭제 없음(이전 목록 없음)" 0 "$(count chat.delete)"
check "대기 항목 2개" 1 "$(grep -c '답을 기다리는 결정 2개' "$LOG")"
check "이력: 수정 전 기록" 1 "$(ev answered_update)"
check "이력: 수정 전 원문 보존" 1 "$(grep '"answered_update"' "$(H_FILE)" | grep -c '원문 본문 전체')"
check "이력: 새 목록 기록" 1 "$(ev pending_posted)"

echo "[재실행] 새로 보내고 이전 목록을 이력 기록 뒤 삭제, 수정은 반복 안 함"
answer 1.1
: > "$LOG"
bash "$PEND" >/dev/null; RC=$?
check "종료 코드" 0 "$RC"
check "수정 1회(새로 답한 첫째만)" 1 "$(count chat.update)"
check "삭제 1회" 1 "$(count chat.delete)"
check "삭제 대상은 이전 목록 ts" 1 "$(grep -c '"ts": "9.1"' "$LOG")"
check "이력: 삭제 의도 기록" 1 "$(ev pending_deleted)"
check "이력: 삭제 완료 기록" 1 "$(ev delete_done)"
check "이력에 이전 목록 내용" 1 "$(grep '"pending_deleted"' "$(H_FILE)" | grep -c '결정: 첫째')"
check "이력에 삭제 뒤 처리된 결정" 1 "$(grep '"pending_deleted"' "$(H_FILE)" | grep -c '"answered_at"')"
stop_stub

echo "[원문 조회 실패] 원문을 못 얻으면 그 메시지는 수정하지 않고 다음 주기로"
new_hub; seed
start_stub "{$PERMA,$UPD,$DEL,\"conversations.history\":[{\"ok\":false,\"error\":\"ratelimited\"}],$POST1}"
bash "$PEND" >/dev/null; RC=$?
check "종료 코드" 0 "$RC"
check "수정 0회" 0 "$(count chat.update)"
check "수정 사건 기록 없음" 0 "$(ev answered_update)"
swap_stub "{$PERMA,$UPD,$DEL,$ORIG,$POST1}"
bash "$PEND" >/dev/null
check "원문 조회 복구 뒤 수정 1회" 1 "$(count chat.update)"
stop_stub

echo "[수정 상한] 회당 LRM_UPDATE_MAX 개까지, 나머지는 다음 실행"
new_hub; seed; answer 1.1; answer 1.3
start_stub "{$PERMA,$UPD,$DEL,$ORIG,$POST1}"
LRM_UPDATE_MAX=2 bash "$PEND" >/dev/null
check "첫 실행 수정 2회" 2 "$(count chat.update)"
: > "$LOG"
LRM_UPDATE_MAX=2 bash "$PEND" >/dev/null
check "다음 실행 남은 1회" 1 "$(count chat.update)"
stop_stub

echo "[수정 재시도] 일시 오류는 다음 실행에 다시, 최종 오류는 다시 안 함"
new_hub; seed
start_stub "{$PERMA,\"chat.update\":[{\"ok\":false,\"error\":\"ratelimited\"}],$DEL,$ORIG,$POST1}"
bash "$PEND" >/dev/null
check "1회차 수정 시도 1회" 1 "$(count chat.update)"
swap_stub "{$PERMA,\"chat.update\":[{\"ok\":false,\"error\":\"message_not_found\"}],$DEL,$ORIG,$POST1}"
bash "$PEND" >/dev/null
check "일시 오류 뒤 재시도 1회" 1 "$(count chat.update)"
swap_stub "{$PERMA,$UPD,$DEL,$ORIG,$POST1}"
bash "$PEND" >/dev/null
check "최종 오류 뒤 재시도 0회" 0 "$(count chat.update)"
stop_stub

echo "[수정 호출 예외] 429 로 수정이 끝나도 재시도 가능 표시를 남겨 다음 실행에 다시 수정"
new_hub; seed
start_stub "{$PERMA,\"chat.update\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"0\"},\"ok\":false}],$DEL,$ORIG,$POST1}"
bash "$PEND" >/dev/null 2>&1; RC=$?
check "종료 코드 1" 1 "$RC"
check "이력: 일시 실패 표시" 1 "$(grep '"update_failed"' "$(H_FILE)" | grep -c '"final": false')"
swap_stub "{$PERMA,$UPD,$DEL,$ORIG,$POST1}"
bash "$PEND" >/dev/null 2>&1
check "다음 실행에서 수정 재시도 1회" 1 "$(count chat.update)"
stop_stub

echo "[빈 목록] 전부 답함 → 새 메시지 없이 이전 목록만 정리"
new_hub; seed; answer 1.1; answer 1.3
seed_posted
start_stub "{$PERMA,$UPD,$DEL,$ORIG,\"chat.postMessage\":[{\"ok\":true,\"ts\":\"9.1\"}]}"
OUT=$(bash "$PEND"); RC=$?
check "종료 코드" 0 "$RC"
check "출력" "대기 0" "$OUT"
check "새 메시지 0회" 0 "$(count chat.postMessage)"
check "이전 목록 삭제 1회" 1 "$(count chat.delete)"
stop_stub

echo "[빈 목록·이력 없음] 기록 파일 없는 첫 실행도 정상"
new_hub; : > "$HUB/slack-sent.jsonl"; : > "$HUB/slack-inbox.jsonl"
start_stub "{$PERMA}"
OUT=$(bash "$PEND"); RC=$?
check "종료 코드" 0 "$RC"
check "출력" "대기 0" "$OUT"
check "API 호출 0회" 0 "$(wc -l < "$LOG" | tr -d ' ')"
stop_stub

echo "[이력 선검사] 쓰기 불가면 전송·수정·삭제 모두 안 함"
new_hub; seed
seed_posted; chmod 444 "$(H_FILE)"
start_stub "{$PERMA,$UPD,$DEL,$ORIG,$POST1}"
bash "$PEND" >/dev/null 2>&1; RC=$?
check "종료 코드 1" 1 "$RC"
check "삭제 0회" 0 "$(count chat.delete)"
check "수정 0회" 0 "$(count chat.update)"
check "새 메시지 0회" 0 "$(count chat.postMessage)"
stop_stub

echo "[이력 선검사·답한 결정 없음] 수정이 없어도 대기 목록 전송을 막음"
new_hub; seed; : > "$HUB/slack-inbox.jsonl"
seed_posted; chmod 444 "$(H_FILE)"
start_stub "{$PERMA,$UPD,$DEL,$ORIG,$POST1}"
bash "$PEND" >/dev/null 2>&1; RC=$?
check "종료 코드 1" 1 "$RC"
check "새 메시지 0회" 0 "$(count chat.postMessage)"
stop_stub

echo "[전송 직후 이력 실패] 방금 보낸 메시지를 회수하고 이전 목록은 남김"
new_hub; seed; : > "$HUB/slack-inbox.jsonl"
seed_posted
start_stub "{$PERMA,$UPD,$DEL,$ORIG,\"chat.postMessage\":[{\"ok\":true,\"ts\":\"9.1\",\"_chmod\":\"$(H_FILE)\"}]}"
bash "$PEND" >/dev/null 2>&1; RC=$?
check "종료 코드 1" 1 "$RC"
check "삭제는 방금 보낸 것 1회" 1 "$(count chat.delete)"
check "삭제 대상 ts" 1 "$(grep -c '"ts": "9.1"' "$LOG")"
check "이전 목록 삭제 안 함" 0 "$(grep -c '"ts": "8.1"' "$LOG")"
stop_stub

echo "[429] 새 메시지 전송이 계속 거절되면 이전 목록을 지우지 않음"
new_hub; seed
seed_posted
start_stub "{$PERMA,$UPD,$DEL,$ORIG,\"chat.postMessage\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"0\"},\"ok\":false}]}"
bash "$PEND" >/dev/null 2>&1; RC=$?
check "종료 코드 1" 1 "$RC"
check "전송 재시도 포함 3회" 3 "$(count chat.postMessage)"
check "삭제 0회" 0 "$(count chat.delete)"
stop_stub

echo "[429 한 번] Retry-After 후 재시도 성공"
new_hub; seed
start_stub "{$PERMA,$UPD,$DEL,$ORIG,\"chat.postMessage\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"0\"},\"ok\":false},{\"ok\":true,\"ts\":\"9.1\"}]}"
bash "$PEND" >/dev/null 2>&1; RC=$?
check "종료 코드" 0 "$RC"
check "전송 2회" 2 "$(count chat.postMessage)"
stop_stub

echo "[Retry-After] 알려 준 시간만큼 쉼"
new_hub; seed
start_stub "{$PERMA,$UPD,$DEL,$ORIG,\"chat.postMessage\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"2\"},\"ok\":false},{\"ok\":true,\"ts\":\"9.1\"}]}"
T0=$(now_ms); LRM_RETRY_CAP=20 bash "$PEND" >/dev/null 2>&1; T1=$(now_ms)
check "대기 2초 이상" yes "$([ $((T1 - T0)) -ge 1900 ] && echo yes || echo no)"
stop_stub

echo "[시간 예산] 남은 예산보다 긴 대기는 쉬지 않고 실패 처리"
new_hub; seed
start_stub "{$PERMA,$UPD,$DEL,$ORIG,\"chat.postMessage\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"30\"},\"ok\":false}]}"
T0=$(now_ms); LRM_RETRY_CAP=20 LRM_PENDING_BUDGET=2 bash "$PEND" >/dev/null 2>&1; RC=$?; T1=$(now_ms)
check "종료 코드 1" 1 "$RC"
check "2초 안에 종료" yes "$([ $((T1 - T0)) -lt 2500 ] && echo yes || echo no)"
check "전송 1회" 1 "$(count chat.postMessage)"
stop_stub

echo "[중복 실행] 잠금이 있으면 호출 없이 종료"
new_hub; seed; mkdir "$HUB/.slack-pending.lock"
start_stub "{$PERMA}"
bash "$PEND" >/dev/null; RC=$?
check "종료 코드 75(잠금으로 건너뜀)" 75 "$RC"
check "API 호출 0회" 0 "$(wc -l < "$LOG" | tr -d ' ')"
stop_stub

echo "[dry-run] 전송·수정·이력 없음"
new_hub; seed
start_stub "{$PERMA}"
bash "$PEND" --dry-run >/dev/null; RC=$?
check "종료 코드" 0 "$RC"
check "전송 0회" 0 "$(count chat.postMessage)"
check "이력 파일 없음" no "$([ -e "$(H_FILE)" ] && echo yes || echo no)"
stop_stub

summary
