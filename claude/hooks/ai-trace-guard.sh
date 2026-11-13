#!/usr/bin/env bash
# PreToolUse(Bash): push 전 AI 흔적 검사. 대상 = git push 로 나갈 커밋 메시지, gh pr create/edit 의 본문.
# 흔적 = 'Co-Authored-By: Claude/Anthropic', 'Generated with Claude Code', 🤖 줄.
# 기준(저장소별 git config hooks.aiTraceMode): deny(거부) | ask(경고 후 확인) | off. 기본은 claude-config 저장소 deny, 그 밖(게임 저장소 등) ask.
# 범위 = 실제로 나갈 커밋: <원격>/<대상 브랜치>..<로컬 ref>. 저장소·ref 는 명령의 cd·git -C·refspec 에서 해석(hook cwd 아님).
# 해석 실패는 거부가 아니라 ask. stdin: hook JSON.
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)
cwd=$(printf '%s' "$input" | jq -r '.cwd // ""' 2>/dev/null)
GUARD_DIR=$(cd "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")" && pwd -P)
# heredoc 본문·따옴표 안 문구의 push 글자는 명령이 아님. 판정만 정리본으로, 본문 검사는 원문으로
scmd=$(printf '%s' "$cmd" | python3 -I "$GUARD_DIR/lib/strip_text.py" 2>/dev/null) && [ -n "$scmd" ] || scmd="$cmd"
G='(^|[[:space:];&|(])git[[:space:]]+((-C|-c)[[:space:]]+[^[:space:]]+[[:space:]]+|-[^[:space:]]+[[:space:]]+)*'   # -C <dir>·-c k=v 는 값 포함
GH='(^|[[:space:];&|(])gh[[:space:]]+pr[[:space:]]+(create|edit)\b'
is_push=0; is_pr=0
printf '%s' "$scmd" | grep -Eq "${G}push\b" && is_push=1
printf '%s' "$scmd" | grep -Eq "$GH" && is_pr=1
[ $((is_push + is_pr)) -gt 0 ] || exit 0

TRACE_RE='co-authored-by:[[:space:]]*.*(claude|anthropic)|generated with[[:space:]]*(\[?claude|claude code)|claude\.com/claude-code|🤖'
CONFIG_REPO="${LRM_CONFIG_REPO_NAME:-claude-config}"
found=""; mode=""; ask_only=""

# 저장소별 기준. 설정이 없으면 공통 git 디렉터리의 저장소 이름으로 판정(작업 공간 폴더 이름과 무관)
repo_mode() {
  local m common name
  m=$(git -C "$1" config --get hooks.aiTraceMode 2>/dev/null)
  if [ -z "$m" ]; then
    common=$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
    name=$(basename "$(dirname "$common")")
    if [ "$name" = "$CONFIG_REPO" ]; then m=deny; else m=ask; fi
  fi
  printf '%s' "$m"
}

# 대상 하나의 범위를 검사. $1=저장소 최상위 $2=원격 $3=원격 브랜치 $4=로컬 ref
check_range() {
  local top=$1 remote=$2 dst=$3 src=$4 base range hit warn=""
  if git -C "$top" rev-parse -q --verify "refs/remotes/$remote/$dst" >/dev/null 2>&1; then base="refs/remotes/$remote/$dst"
  elif git -C "$top" rev-parse -q --verify "refs/remotes/$remote/HEAD" >/dev/null 2>&1; then base="refs/remotes/$remote/HEAD"
  fi
  if [ -n "$base" ]; then range="$base..$src"
  else range="-50 $src"; warn=" (원격 기준 브랜치를 못 찾아 최근 50개만 검사)"; fi
  # shellcheck disable=SC2086
  hit=$(git -C "$top" log --format='%h %B' $range 2>/dev/null | grep -Eiv '^$' | grep -Ei "$TRACE_RE" | head -1)
  if [ -n "$hit" ]; then
    found="push 할 커밋 메시지에 AI 흔적: ${hit:0:80}$warn"; mode=$(repo_mode "$top")
  elif [ -n "$warn" ]; then
    echo "ai-trace-guard:$warn" >&2
  fi
}

if [ "$is_push" = 1 ]; then
  while IFS= read -r rec; do
    [ -n "$rec" ] || continue
    err=$(printf '%s' "$rec" | jq -r '.err // empty')
    if [ -n "$err" ]; then found="push 대상을 해석하지 못함($err). 흔적 검사를 확인 필요"; ask_only=1; break; fi
    dir=$(printf '%s' "$rec" | jq -r '.dir'); remote=$(printf '%s' "$rec" | jq -r '.remote')
    flags=$(printf '%s' "$rec" | jq -r '.flags | join(" ")')
    top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)
    if [ -z "$top" ]; then found="push 할 저장소를 찾지 못함($dir). 흔적 검사를 확인 필요"; ask_only=1; break; fi
    [ "$(repo_mode "$top")" = off ] && continue
    case " $flags " in *" --delete "*|*" -d "*) continue;; esac
    case " $flags " in *" --all "*|*" --mirror "*) found="push --all/--mirror 는 범위를 해석하지 않음. 흔적 검사를 확인 필요"; ask_only=1; break;; esac
    cur=$(git -C "$top" symbolic-ref -q --short HEAD 2>/dev/null)
    up=$(git -C "$top" rev-parse --abbrev-ref '@{u}' 2>/dev/null)
    if [ -z "$remote" ]; then   # 인자 없음: 현재 브랜치의 upstream(원격 이름은 설정에서)
      remote=$(git -C "$top" config --get "branch.$cur.remote" 2>/dev/null); remote=${remote:-origin}
    fi
    nref=$(printf '%s' "$rec" | jq -r '.refs | length')
    if [ "$nref" = 0 ]; then
      if [ -n "$up" ] && [ "${up%%/*}" = "$remote" ]; then dst=${up#*/}; else dst=$cur; fi
      [ -n "$dst" ] || { found="push 할 브랜치를 해석하지 못함(분리된 HEAD). 흔적 검사를 확인 필요"; ask_only=1; break; }
      check_range "$top" "$remote" "$dst" HEAD
    else
      while IFS= read -r spec; do
        spec=${spec#+}
        case "$spec" in :*) continue;; esac   # 원격 브랜치 삭제
        if [[ "$spec" == *:* ]]; then src=${spec%%:*}; dst=${spec#*:}; else src=$spec; dst=$spec; fi
        dst=${dst#refs/heads/}
        [ "$src" = HEAD ] && [ "$dst" = HEAD ] && dst=$cur
        git -C "$top" rev-parse -q --verify "$src" >/dev/null 2>&1 || { found="push 할 ref 를 해석하지 못함($src). 흔적 검사를 확인 필요"; ask_only=1; break 2; }
        check_range "$top" "$remote" "$dst" "$src"
        [ -n "$found" ] && break 2
      done < <(printf '%s' "$rec" | jq -r '.refs[]')
    fi
    [ -n "$found" ] && break
  done < <(printf '%s' "$cmd" | python3 -I "$GUARD_DIR/lib/push_targets.py" "${cwd:-.}" 2>/dev/null)
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
  if [ -n "$hit" ]; then
    found="PR 본문에 AI 흔적: ${hit:0:80}"; mode=$(repo_mode "${cwd:-.}")
    [ "$mode" = off ] && found=""
  fi
fi
[ -n "$found" ] || exit 0
if [ -n "$ask_only" ]; then d=ask; msg="$found."
else
  msg="$found. 해당 줄을 지운 뒤 다시 실행(커밋은 새 메시지로 수정, PR 본문은 해당 줄 삭제). 기준 변경: git config hooks.aiTraceMode deny|ask|off."
  [ "$mode" = deny ] && d=deny || d=ask
fi
jq -cn --arg r "$msg" --arg d "$d" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:$d,permissionDecisionReason:$r}}'
exit 0
