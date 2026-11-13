#!/usr/bin/env bash
# PreToolUse(Bash): push 전 AI 흔적 검사. 대상 = git push 로 나갈 커밋 메시지, gh pr create/edit 의 본문.
# 흔적 = 'Co-Authored-By: Claude/Anthropic', 'Generated with Claude Code', 🤖 줄.
# 기준(저장소별 git config hooks.aiTraceMode): deny(거부) | ask(경고 후 확인) | off. 기본은 claude-config 저장소 deny, 그 밖(게임 저장소 등) ask.
# 이미 원격에 있는 이력은 검사하지 않음(upstream..HEAD 범위만). stdin: hook JSON.
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)
cwd=$(printf '%s' "$input" | jq -r '.cwd // ""' 2>/dev/null)
G='(^|[[:space:];&|(])git[[:space:]]+(-[^[:space:]]+[[:space:]]+)*'
GH='(^|[[:space:];&|(])gh[[:space:]]+pr[[:space:]]+(create|edit)\b'
is_push=0; is_pr=0
printf '%s' "$cmd" | grep -Eq "${G}push\b" && is_push=1
printf '%s' "$cmd" | grep -Eq "$GH" && is_pr=1
[ $((is_push + is_pr)) -gt 0 ] || exit 0

TRACE_RE='co-authored-by:[[:space:]]*.*(claude|anthropic)|generated with[[:space:]]*(\[?claude|claude code)|claude\.com/claude-code|🤖'
CONFIG_REPO="${LRM_CONFIG_REPO_NAME:-claude-config}"
top=$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null)
mode=$(git -C "${cwd:-.}" config --get hooks.aiTraceMode 2>/dev/null)
if [ -z "$mode" ]; then
  # 작업 공간 폴더 이름이 달라도 공통 git 디렉터리의 저장소 이름으로 판정
  common=$(git -C "${cwd:-.}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  repo_name=$(basename "$(dirname "$common")")
  if [ "$repo_name" = "$CONFIG_REPO" ]; then mode=deny; else mode=ask; fi
fi
[ "$mode" = off ] && exit 0

found=""
if [ "$is_push" = 1 ] && [ -n "$top" ]; then
  range=""
  if git -C "$top" rev-parse -q --verify '@{u}' >/dev/null 2>&1; then range='@{u}..HEAD'
  else
    for b in origin/dev origin/develop origin/main origin/master; do
      git -C "$top" rev-parse -q --verify "$b" >/dev/null 2>&1 && { range="$b..HEAD"; break; }
    done
  fi
  if [ -n "$range" ]; then
    hit=$(git -C "$top" log --format='%h %B' "$range" 2>/dev/null | grep -Eiv '^$' | grep -Ei "$TRACE_RE" | head -1)
    [ -n "$hit" ] && found="push 할 커밋 메시지에 AI 흔적: ${hit:0:80}"
  fi
fi
if [ "$is_pr" = 1 ] && [ -z "$found" ]; then
  body="$cmd"
  # --body-file/-F 는 파일 내용도 검사(상대 경로는 cwd 기준)
  bf=$(printf '%s' "$cmd" | grep -Eo -- '(--body-file|-F)[ =]+[^[:space:];&|]+' | head -1 | sed -E 's/^(--body-file|-F)[ =]+//')
  if [ -n "$bf" ] && [ "$bf" != "-" ]; then
    case "$bf" in /*) ;; *) bf="${cwd:-.}/$bf";; esac
    [ -f "$bf" ] && body="$body"$'\n'"$(cat "$bf")"
  fi
  hit=$(printf '%s\n' "$body" | grep -Ei "$TRACE_RE" | head -1)
  [ -n "$hit" ] && found="PR 본문에 AI 흔적: ${hit:0:80}"
fi
[ -n "$found" ] || exit 0
msg="$found. 해당 줄을 지운 뒤 다시 실행(커밋은 새 메시지로 수정, PR 본문은 해당 줄 삭제). 기준 변경: git config hooks.aiTraceMode deny|ask|off."
[ "$mode" = deny ] && d=deny || d=ask
jq -cn --arg r "$msg" --arg d "$d" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:$d,permissionDecisionReason:$r}}'
exit 0
