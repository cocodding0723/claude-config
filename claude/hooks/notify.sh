#!/usr/bin/env bash
# Notification hook: macOS banner when a session needs attention (permission prompt / idle / question).
# Title carries the worktree/project dir name so parallel sessions are distinguishable.
input=$(cat)
msg=$(printf '%s' "$input" | jq -r '.message // "Claude Code needs attention"' | cut -c1-200)
cwd=$(printf '%s' "$input" | jq -r '.cwd // ""'); name=$(basename "${cwd:-Claude}")
kind=$(printf '%s' "$input" | jq -r '.notification_type // ""')
case "$kind" in permission_prompt) sound="Tink";; idle_prompt) sound="Pop";; *) sound="Glass";; esac
osascript -e "display notification \"$(printf '%s' "$msg" | sed 's/"/\\"/g')\" with title \"Claude · $name\" sound name \"$sound\"" >/dev/null 2>&1 || true
exit 0
