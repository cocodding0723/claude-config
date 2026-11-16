#!/usr/bin/env bash
# PreToolUse(mcp__atlassian__*): Jira 이슈 생성·상태 변경은 사용자 승인 기록(결정 카드 D-JIRA<해시>·D-JST<키>) 뒤에만(사용자 11-16).
# 판정은 lib/jira_guard.py, 실행 실패는 ask. stdin: hook JSON.
input=$(cat)
GUARD_DIR=$(cd "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")" && pwd -P)
out=$(printf '%s' "$input" | python3 -I "$GUARD_DIR/lib/jira_guard.py" 2>/dev/null) \
  || out=$(jq -cn '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:"jira-approval-guard 실행 실패 — Jira 발급·상태 변경이 사용자 승인된 것인지 확인 필요"}}')
[ -n "$out" ] && printf '%s\n' "$out"
exit 0
