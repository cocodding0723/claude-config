#!/bin/bash
# slack_cmd.sh 케이스: 대기 명령에서 대기 조회 실패·빈 목록·정상 목록의 응답 구분
. "$(dirname "$0")/lib.sh"
CMD="$SCRIPTS/slack_cmd.sh"

# 대기 명령 한 줄을 inbox 에 넣고 대기·전송 스텁을 지정한 동작으로 구성
setup() {
  new_hub
  echo '{"type":"note","ts":"1.1","text":"대기"}' > "$HUB/slack-inbox.jsonl"
  echo 0 > "$HUB/.cmd-read"
  printf '#!/bin/bash\n%s\n' "$1" > "$HUB/slack_pending.sh"
  printf '#!/bin/bash\necho "$1" >> "%s/notify-calls"\n' "$HUB" > "$HUB/slack_notify.sh"
  chmod +x "$HUB/slack_pending.sh" "$HUB/slack_notify.sh"
}
notified() { [ -f "$HUB/notify-calls" ] && cat "$HUB/notify-calls" || echo ""; }

echo "[대기 조회 실패] 결정 없음이 아니라 확인 불가로 응답"
setup 'echo "실패: 이력" >&2; exit 1'
bash "$CMD"
check "확인 불가 응답" "⚠️ 대기 목록 확인 불가 — slack-poll.log 확인" "$(notified)"
rm -rf "$HUB"

echo "[빈 목록] 결정 없음 응답"
setup 'echo "대기 0"'
bash "$CMD"
check "결정 없음 응답" "✅ 답을 기다리는 결정 없음" "$(notified)"
rm -rf "$HUB"

echo "[정상 목록] 목록은 대기 스크립트가 보내므로 추가 응답 없음"
setup 'echo "ok 2"'
bash "$CMD"
check "추가 응답 없음" "" "$(notified)"
check "읽은 줄 갱신" 1 "$(tr -d ' ' < "$HUB/.cmd-read")"
rm -rf "$HUB"

summary
