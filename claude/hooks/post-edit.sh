#!/usr/bin/env bash
# PostToolUse(Write|Edit|MultiEdit): record touched code files for scoped-test; inject the commit checklist once per session.
. "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")/lib/common.sh"
hook_input
f=$(hj '.tool_input.file_path'); [ -z "$f" ] && f=$(hj '.tool_response.filePath')
printf '%s' "$f" | grep -Eq "$CODE_EXT_RE" || exit 0
sid=$(hj '.session_id')
[ -n "$sid" ] && printf '%s\n' "$f" >> "$STATE_DIR/sessions/$sid.files"
gate="$STATE_DIR/sessions/$sid.gated"
[ -n "$sid" ] && [ -f "$gate" ] && exit 0
[ -n "$sid" ] && : > "$gate"
jq -cn '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:"[commit-check] 코드 파일이 변경됨. 작업 브랜치 커밋은 승인 없이 함. 커밋 전: comment-lint 주석 점검·컴파일·관련 테스트 통과. push·리베이스·머지·PR 생성은 변경 요약을 보여주고 사용자 승인 뒤에만. (Stop 시 scoped-test가 변경 범위의 테스트를 자동 실행하고 결과를 다음 프롬프트에 알려줌)"}}'
