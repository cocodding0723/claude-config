#!/usr/bin/env bash
# SessionStart: prune old state, report stale worktrees, kick off GitHub issue sync (detached).
. "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")/lib/common.sh"
hook_input; cwd=$(hj '.cwd'); src=$(hj '.source')
prune_state
top=$(repo_top "$cwd") || exit 0
git -C "$top" worktree prune 2>/dev/null

# --- stale worktree report (never deletes) ---
main=$(git -C "$top" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##'); main=${main:-main}
report=""; total=0
while IFS= read -r line; do
  case "$line" in
    "worktree "*) wt=${line#worktree }; total=$((total+1)) ;;
    "branch "*)   br=${line#branch refs/heads/} ;;
    "")
      [ -n "$wt" ] && [ "$wt" != "$(git -C "$top" rev-parse --path-format=absolute --show-toplevel)" ] && [ -n "$br" ] && {
        reason=""
        git -C "$top" merge-base --is-ancestor "$br" "$main" 2>/dev/null && reason="$main에 merge됨"
        [ -z "$reason" ] && [ -n "$(find "$wt" -maxdepth 0 -mtime +7 2>/dev/null)" ] && reason="7일+ 미변경"
        [ -n "$reason" ] && {
          dirty=$(git -C "$wt" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
          report="$report
  - $wt ($br, $reason${dirty:+, 미커밋 ${dirty}개})"
        }
      }
      wt=""; br="" ;;
  esac
done <<< "$(git -C "$top" worktree list --porcelain 2>/dev/null; echo)"

# --- issue sync (detached, incremental, max every 6h) ---
if [ "$src" != compact ] && [ -n "$(gh_slug "$top")" ]; then
  rh=$(repo_hash "$top"); stamp="$(repo_dir "$rh")/issues.sync"
  if [ ! -f "$stamp" ] || [ "$(( $(date +%s) - $(cat "$stamp") ))" -gt 21600 ]; then
    nohup "$HOOKS_DIR/lib/issue-sync.sh" "$top" >/dev/null 2>&1 &
  fi
fi

[ -n "$report" ] || exit 0
msg="stale worktree 발견 (총 $total개 중):$report
정리하려면 /worktree-gc"
jq -cn --arg m "$msg" '{systemMessage:$m, hookSpecificOutput:{hookEventName:"SessionStart", additionalContext:$m}}'
