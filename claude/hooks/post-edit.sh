#!/usr/bin/env bash
# PostToolUse(Write|Edit|MultiEdit): record touched code files for scoped-test; inject the review-gate once per session.
. "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")/lib/common.sh"
hook_input
f=$(hj '.tool_input.file_path'); [ -z "$f" ] && f=$(hj '.tool_response.filePath')
printf '%s' "$f" | grep -Eq "$CODE_EXT_RE" || exit 0
sid=$(hj '.session_id')
[ -n "$sid" ] && printf '%s\n' "$f" >> "$STATE_DIR/sessions/$sid.files"
gate="$STATE_DIR/sessions/$sid.gated"
[ -n "$sid" ] && [ -f "$gate" ] && exit 0
[ -n "$sid" ] && : > "$gate"
jq -cn '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:"[review-gate] 코드 파일이 변경됨. 이번 작업을 마칠 때 반드시: 변경 파일 목록 + 핵심 변경 요약을 보여주고 사용자의 코드 리뷰/승인을 명시적으로 요청할 것. 승인 전 git commit 금지. (Stop 시 scoped-test가 변경 범위의 테스트를 자동 실행하고 결과를 다음 프롬프트에 알려줌)"}}'
