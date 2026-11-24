#!/bin/bash
# 야간 확인 창 대체. 감싼 훅이 "ask" 를 내면 밤에는 사람이 없어 세션이 아침까지 멈춤 →
# 야간 창 안·스위치 on 일 때만 ask 를 deny 로 바꾸고 아침 결재함(night-inbox.jsonl)에 적음. deny·allow·통과는 그대로.
# 사용: night-ask.sh <훅 명령...>   (settings 의 PreToolUse command 를 감쌈)
#       night-ask.sh --status | --on | --off
set -u
HUB_DIR="${LRM_HUB_DIR:-$HOME/.config/lrm-hubs}"
SWITCH="${LRM_NIGHT_ASK:-$HUB_DIR/night-ask.mode}"      # on | off(없음 = off)
POLICY="${LRM_NIGHT_POLICY:-$HUB_DIR/night_policy.tsv}"  # 야간 창(window_start·window_end)은 배정기와 같은 표
INBOX="${LRM_NIGHT_INBOX:-$HUB_DIR/night-inbox.jsonl}"

policy() { awk -F'\t' -v k="$1" '$1 == k { print $2; exit }' "$POLICY" 2>/dev/null; }
# 야간 창 안이면 0. 끝 시각 미포함, 자정 넘김(23~7) 지원. 표가 없거나 값이 이상하면 창 밖(안전한 쪽: 사람에게 물음)
in_window() {
  local s e h
  s=$(policy window_start); e=$(policy window_end)
  case "$s$e" in ''|*[!0-9]*) return 1 ;; esac
  h=$(( 10#${LRM_NIGHT_HOUR:-$(date +%H)} ))
  if [ "$s" -le "$e" ]; then [ "$h" -ge "$s" ] && [ "$h" -lt "$e" ]; else [ "$h" -ge "$s" ] || [ "$h" -lt "$e" ]; fi
}
active() { [ "$(cat "$SWITCH" 2>/dev/null)" = on ] && in_window; }

case "${1:-}" in
  --status) printf 'switch=%s window=%s~%s now_active=%s inbox=%s\n' "$(cat "$SWITCH" 2>/dev/null || echo off)" "$(policy window_start)" "$(policy window_end)" "$(active && echo yes || echo no)" "$INBOX"; exit 0 ;;
  --on|--off) mkdir -p "$(dirname "$SWITCH")"; echo "${1#--}" > "$SWITCH"; echo "night-ask ${1#--}"; exit 0 ;;
  ''|-h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac

input=$(cat)
out=$(printf '%s' "$input" | "$@"); rc=$?
# 밤이 아니거나 ask 가 아니면 감싼 훅 결과를 그대로 돌려줌(종료 코드 2 = 차단도 유지)
if ! active || ! printf '%s' "$out" | jq -e '.hookSpecificOutput.permissionDecision == "ask"' >/dev/null 2>&1; then
  [ -n "$out" ] && printf '%s\n' "$out"
  exit "$rc"
fi
reason=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecisionReason // ""')
mkdir -p "$(dirname "$INBOX")"
printf '%s' "$input" | jq -c --arg r "$reason" --arg at "$(date '+%Y-%m-%dT%H:%M:%S')" --arg hook "$(basename "${1:-?}") ${2:+$(basename "$2")}" \
  '{at:$at, hook:$hook, reason:$r, session:(.session_id // ""), cwd:(.cwd // ""), tool:(.tool_name // ""), input:((.tool_input // {}) | tostring | .[:500])}' >> "$INBOX" 2>/dev/null
jq -cn --arg r "야간 모드: 사람 확인이 필요한 일이라 실행하지 않고 아침 결재함에 올림($reason). 이 일은 건너뛰고, 확인 없이 할 수 있는 다음 일을 계속할 것" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
