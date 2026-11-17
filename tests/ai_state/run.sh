#!/usr/bin/env bash
# 세션 상태 훅(claude/hooks/ai-state.sh) 시험. 가짜 훅 입력 JSON 과 mktemp -d 폴더만 씀.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
H="$HERE/../../claude/hooks/ai-state.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/ai-state.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export LRM_AI_STATE_DIR="$SB/st"
ev() { jq -cn --arg e "$1" --arg s "${2:-s1}" --arg k "${3:-}" '{hook_event_name:$e,session_id:$s,cwd:"/ws/feat-mail",notification_type:$k,prompt:"비밀 글"}' | bash "$H"; echo $?; }
st() { jq -r .state "$LRM_AI_STATE_DIR/s1.json" 2>/dev/null; }
[ "$(ev UserPromptSubmit)" = 0 ] && [ "$(st)" = working ] && ok "입력 → working" || ko "working" "$(st)"
ev Notification s1 permission_prompt >/dev/null; [ "$(st)" = needs_input ] && [ "$(jq -r .kind "$LRM_AI_STATE_DIR/s1.json")" = permission_prompt ] && ok "알림 → needs_input(종류 그대로)" || ko "needs_input" ""
ev PostToolUse >/dev/null; [ "$(st)" = working ] && ok "도구 실행 뒤 → working" || ko "post" ""
ev Stop >/dev/null; [ "$(st)" = idle ] && ok "멈춤 → idle" || ko "idle" ""
! grep -q '비밀' "$LRM_AI_STATE_DIR/s1.json" && ok "본문은 기록하지 않음(경로·상태·시각만)" || ko "본문 기록" ""
ev SessionEnd >/dev/null; [ ! -e "$LRM_AI_STATE_DIR/s1.json" ] && ok "세션 끝 → 파일 지움" || ko "끝" ""
[ "$(ev Stop '../x')" = 0 ] && [ ! -e "$SB/x.json" ] && ok "세션 id 에 경로 문자 → 아무것도 안 씀, 종료 0" || ko "경로 문자" ""
[ "$(printf 'garbage' | bash "$H"; echo $?)" = 0 ] && ok "깨진 입력도 종료 0(세션을 막지 않음)" || ko "깨진 입력" ""
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
