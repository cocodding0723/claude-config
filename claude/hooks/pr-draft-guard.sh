#!/usr/bin/env bash
# PreToolUse(Bash): 새 PR 은 draft 로만(gh pr create 에 --draft 필수), draft 해제(gh pr ready 등)는 그 PR 의 사용자 승인 기록이 있을 때만.
# 승인 기록 = 결정 카드 D-RDY<번호>(pick 1/approve) 또는 문서 사이트 PR 병합 승인(pr-approve.jsonl). 판정 불가는 ask. 11-13 D-PRDRAFT.
# 명령 해석은 lib/pr_guard.py(따옴표·heredoc 안 글자 무시, cd·-R 추적). stdin: hook JSON.
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)
cwd=$(printf '%s' "$input" | jq -r '.cwd // ""' 2>/dev/null)
printf '%s' "$cmd" | grep -Eq '(^|[^[:alnum:]_.-])gh([^[:alnum:]_-]|$)' || exit 0   # gh 를 쓰지 않으면 즉시 통과
GUARD_DIR=$(cd "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")" && pwd -P)
out=$(printf '%s' "$cmd" | python3 -I "$GUARD_DIR/lib/pr_guard.py" "$cwd" 2>/dev/null) \
  || out=$(jq -cn '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:"pr-draft-guard 실행 실패 — gh pr create/ready 가 draft 규칙에 맞는지 확인 필요"}}')
[ -n "$out" ] && printf '%s\n' "$out"
exit 0
