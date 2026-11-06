#!/bin/bash
# slack_pending.sh 케이스: 정상(수정·재발송·삭제 이력)·빈 목록·이력 기록 실패·429·중복 실행
. "$(dirname "$0")/lib.sh"
PEND="$ROOT/lrm-hubs/slack_pending.sh"
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
H_FILE() { echo "$HUB/slack-pending-history.jsonl"; }
ev() { grep -c "\"event\": \"$1\"" "$(H_FILE)" || true; }
PERMA='"chat.getPermalink":[{"ok":true,"permalink":"https://example.test/p"}]'
UPD='"chat.update":[{"ok":true}]'
DEL='"chat.delete":[{"ok":true}]'

echo "[정상] 답한 결정 수정 + 새 대기 메시지, 처음엔 삭제 없음"
new_hub; seed
start_stub "{$PERMA,$UPD,$DEL,\"chat.postMessage\":[{\"ok\":true,\"ts\":\"9.1\"},{\"ok\":true,\"ts\":\"9.2\"}]}"
OUT=$(bash "$PEND"); RC=$?
check "종료 코드" 0 "$RC"
check "메시지 수정 1회(답한 결정만)" 1 "$(count chat.update)"
check "수정 본문에 결과 표시" 1 "$(grep -c '✅ 결정: 둘째 — 답: a' "$LOG")"
check "새 대기 메시지 1회" 1 "$(count chat.postMessage)"
check "삭제 없음(이전 목록 없음)" 0 "$(count chat.delete)"
check "대기 항목 2개" 1 "$(grep -c '답을 기다리는 결정 2개' "$LOG")"
check "이력: 수정 전 기록" 1 "$(ev answered_update)"
check "이력: 새 목록 기록" 1 "$(ev pending_posted)"

echo "[재실행] 새로 보내고 이전 목록을 이력 기록 뒤 삭제, 수정은 반복 안 함"
echo '{"type":"reaction","emoji":"a","action":"a","parent_ts":"1.1","received_at":'"$NOW"'}' >> "$HUB/slack-inbox.jsonl"
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

echo "[빈 목록] 전부 답함 → 새 메시지 없이 이전 목록만 정리"
new_hub; seed
echo '{"type":"reaction","emoji":"a","action":"a","parent_ts":"1.1","received_at":'"$NOW"'}' >> "$HUB/slack-inbox.jsonl"
echo '{"type":"reaction","emoji":"a","action":"a","parent_ts":"1.3","received_at":'"$NOW"'}' >> "$HUB/slack-inbox.jsonl"
echo '{"at":'"$NOW"',"event":"pending_posted","ts":"8.1","channel":"DTEST","titles":["결정: 첫째"]}' > "$(H_FILE)"
start_stub "{$PERMA,$UPD,$DEL,\"chat.postMessage\":[{\"ok\":true,\"ts\":\"9.1\"}]}"
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

echo "[이력 기록 실패] 쓰기 불가면 전송·수정·삭제 모두 안 함"
new_hub; seed
echo '{"at":'"$NOW"',"event":"pending_posted","ts":"8.1","channel":"DTEST","titles":["결정: 첫째"]}' > "$(H_FILE)"
chmod 444 "$(H_FILE)"
start_stub "{$PERMA,$UPD,$DEL,\"chat.postMessage\":[{\"ok\":true,\"ts\":\"9.1\"}]}"
bash "$PEND" >/dev/null 2>&1; RC=$?
check "종료 코드 1" 1 "$RC"
check "삭제 0회" 0 "$(count chat.delete)"
check "수정 0회" 0 "$(count chat.update)"
check "새 메시지 0회" 0 "$(count chat.postMessage)"
stop_stub

echo "[429] 새 메시지 전송이 계속 거절되면 이전 목록을 지우지 않음"
new_hub; seed
echo '{"at":'"$NOW"',"event":"pending_posted","ts":"8.1","channel":"DTEST","titles":["결정: 첫째"]}' > "$(H_FILE)"
start_stub "{$PERMA,$UPD,$DEL,\"chat.postMessage\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"0\"},\"ok\":false}]}"
bash "$PEND" >/dev/null 2>&1; RC=$?
check "종료 코드 1" 1 "$RC"
check "전송 재시도 포함 3회" 3 "$(count chat.postMessage)"
check "삭제 0회" 0 "$(count chat.delete)"
stop_stub

echo "[429 한 번] Retry-After 후 재시도 성공"
new_hub; seed
start_stub "{$PERMA,$UPD,$DEL,\"chat.postMessage\":[{\"_status\":429,\"_headers\":{\"Retry-After\":\"0\"},\"ok\":false},{\"ok\":true,\"ts\":\"9.1\"}]}"
bash "$PEND" >/dev/null 2>&1; RC=$?
check "종료 코드" 0 "$RC"
check "전송 2회" 2 "$(count chat.postMessage)"
stop_stub

echo "[중복 실행] 잠금이 있으면 호출 없이 종료"
new_hub; seed; mkdir "$HUB/.slack-pending.lock"
start_stub "{$PERMA}"
bash "$PEND" >/dev/null; RC=$?
check "종료 코드" 0 "$RC"
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
