#!/usr/bin/env bash
# SessionEnd: drop this session's scratch state. Never touches worktrees.
. "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")/lib/common.sh"
hook_input; sid=$(hj '.session_id')
[ -n "$sid" ] && rm -f "$STATE_DIR/sessions/$sid".*
exit 0
