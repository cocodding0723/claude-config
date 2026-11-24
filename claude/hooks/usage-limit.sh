#!/bin/bash
# StopFailure(rate_limit) 훅. 사용량 한도로 멈춘 세션을 기록하고 Slack 보고 1건을 남김.
# 재개 자체는 autoContinueAtUsageLimit 이 맡음 — 여기는 알림과 계정 전환(switch-account) 재료만.
# 리셋 시각은 훅 입력에 없어 오류 문구("resets 3:45pm")에서 읽음. 못 읽으면 "미확인".
set -u
HUB_DIR="${LRM_HUB_DIR:-$HOME/.config/lrm-hubs}"
LOG="${LRM_USAGE_LIMIT_LOG:-$HUB_DIR/usage-limit.jsonl}"
REPORT="${LRM_HUB_REPORT:-$HUB_DIR/hub_report.py}"

input=$(cat)
[ "$(printf '%s' "$input" | jq -r '.error // ""' 2>/dev/null)" = rate_limit ] || exit 0

conf="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
# Orca 계정 폴더: claude-profiles/<id>/home → <id> 앞 8자. Orca 밖이면 "default"
case "$conf" in */claude-profiles/*/home) account=$(basename "$(dirname "$conf")" | cut -c1-8) ;; *) account=default ;; esac
msg=$(printf '%s' "$input" | jq -r '[.last_assistant_message // "", (.error_details // "" | tostring)] | join(" ")')
resets=$(printf '%s' "$msg" | grep -oiE 'resets?( at)? [0-9]{1,2}(:[0-9]{2})? ?([ap]m)?( \([^)]*\))?' | head -1 | sed -E 's/^[Rr]esets?( at)? //')
session=$(printf '%s' "$input" | jq -r '.session_id // ""')
cwd=$(printf '%s' "$input" | jq -r '.cwd // ""')

mkdir -p "$(dirname "$LOG")"
jq -cn --arg at "$(date '+%Y-%m-%dT%H:%M:%S')" --arg a "$account" --arg s "$session" --arg c "$cwd" \
  --arg r "${resets:-}" --arg conf "$conf" \
  '{at:$at, account:$a, session:$s, cwd:$c, resets:$r, config_dir:$conf}' >> "$LOG" 2>/dev/null

text="사용량 한도: 계정 $account 멈춤 — 리셋 ${resets:-미확인}, 리셋 뒤 자동으로 이어 감
작업 폴더: ${cwd/#$HOME/~}
바로 다른 계정으로: switch-account <계정> $session (계정 목록: switch-account --list)"
[ -f "$REPORT" ] && python3 -I "$REPORT" add --hub usage --task 없음 --kind warn --text "$text" >/dev/null 2>&1
exit 0
