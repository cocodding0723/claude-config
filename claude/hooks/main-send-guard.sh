#!/usr/bin/env bash
# PreToolUse(Bash): 메인 코디네이터 터미널 대상 orca terminal send 거부(사용자 11-16 D-MAINSENDHOOK2).
# 메인 입력창에 쓴 보고가 사용자 입력과 섞이므로 SendMessage·main_inbox.py 로 보내게 함. 허브·워커 대상과 orchestration send 는 통과.
# 명령 해석·메인 판정은 lib/main_send_guard.py. slack_push.py(사용자 Slack 메시지를 메인에 입력)는 Bash 명령이 아니라 영향 없음. stdin: hook JSON.
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)
printf '%s' "$cmd" | grep -Eq 'terminal[[:space:]]+send' || exit 0   # 해당 명령이 아니면 즉시 통과
GUARD_DIR=$(cd "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")" && pwd -P)
printf '%s' "$cmd" | python3 -I "$GUARD_DIR/lib/main_send_guard.py" || echo "main-send-guard: 실행 실패 — 통과 처리" >&2
exit 0
