#!/usr/bin/env bash
# PreToolUse(Write|Edit|MultiEdit|NotebookEdit|Bash): 계획 승인 기록 쓰기는 메인(ORCA_ROLE=main)만. 판정은 lib/plan_write_guard.py, 실행 실패는 ask.
input=$(cat)
GUARD_DIR=$(cd "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")" && pwd -P)
out=$(printf '%s' "$input" | python3 -I "$GUARD_DIR/lib/plan_write_guard.py" 2>/dev/null) \
  || out=$(jq -cn '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:"plan-approval-write-guard 실행 실패 — 계획 승인 기록 쓰기가 메인 세션인지 확인 필요"}}')
[ -n "$out" ] && printf '%s\n' "$out"
exit 0
