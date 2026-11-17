#!/usr/bin/env bash
# 세션 상태 파일: 훅 사건마다 ~/.config/lrm-hubs/ai-state/<세션>.json 한 개를 덮어씀. 패널 수집기가 화면 글자 대신 이 값을 읽음(11-17).
# 사건 → 상태: UserPromptSubmit·PostToolUse=working, Notification=needs_input(종류 그대로), Stop=idle, SessionEnd=파일 지움.
# 어떤 실패도 세션을 막지 않음(항상 종료 0). 본문·명령은 기록하지 않음(경로·상태·시각만).
input=$(cat)
DIR="${LRM_AI_STATE_DIR:-${LRM_HUB_DIR:-$HOME/.config/lrm-hubs}/ai-state}"
read -r ev sid cwd kind < <(printf '%s' "$input" | jq -r '[.hook_event_name // "", .session_id // "", (.cwd // "" | gsub("\\s";"_")), .notification_type // ""] | @tsv' 2>/dev/null | tr '\t' ' ')
case "$sid" in ''|*/*|*..*) exit 0;; esac
f="$DIR/$sid.json"
case "$ev" in
  UserPromptSubmit|PostToolUse) st=working;;
  Notification) st=needs_input;;
  Stop) st=idle;;
  SessionEnd) rm -f -- "$f" 2>/dev/null; exit 0;;
  *) exit 0;;
esac
mkdir -p "$DIR" 2>/dev/null || exit 0
tmp="$f.$$"
jq -cn --arg cwd "$cwd" --arg st "$st" --arg k "$kind" --arg ev "$ev" '{cwd:$cwd,state:$st,kind:(if $k=="" then null else $k end),event:$ev,at:now|floor}' > "$tmp" 2>/dev/null && mv -f "$tmp" "$f" 2>/dev/null
rm -f -- "$tmp" 2>/dev/null
exit 0
