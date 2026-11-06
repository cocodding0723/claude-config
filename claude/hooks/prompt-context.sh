#!/usr/bin/env bash
# UserPromptSubmit: tell Claude the latest scoped-test result of this session, once per result.
. "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")/lib/common.sh"
hook_input; sid=$(hj '.session_id'); f="$STATE_DIR/sessions/$sid.last.json"
[ -f "$f" ] || exit 0
[ "$(jq -r .reported "$f")" = true ] && exit 0
jq '.reported=true' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
jq -c '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext:("[scoped-test] 마지막 결과: " + (.status|ascii_upcase) + " — " + .summary + (if .status=="pass" then "" else " (log: " + .log + ")" end))}}' "$f"
