#!/usr/bin/env bash
# night-ask.sh 시험. 가짜 훅(ask·deny·통과)만 쓰고 실제 스위치·결재함은 건드리지 않음
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P); NA="$HERE/../../claude/hooks/night-ask.sh"
SB=$(mktemp -d "${TMPDIR:-/tmp}/night-ask.XXXXXX"); trap 'rm -rf "$SB"' EXIT
export LRM_NIGHT_ASK="$SB/mode" LRM_NIGHT_POLICY="$SB/policy.tsv" LRM_NIGHT_INBOX="$SB/inbox.jsonl"
printf 'window_start\t23\t\nwindow_end\t7\t\n' > "$LRM_NIGHT_POLICY"
PASS=0 FAIL=0
chk() { if eval "$2"; then PASS=$((PASS+1)); echo "PASS  $1"; else FAIL=$((FAIL+1)); echo "FAIL  $1 — $OUT"; fi; }
for d in ask deny; do printf '#!/bin/bash\ncat >/dev/null\njq -cn %s\n' "'{hookSpecificOutput:{hookEventName:\"PreToolUse\",permissionDecision:\"$d\",permissionDecisionReason:\"r-$d\"}}'" > "$SB/$d.sh"; done
printf '#!/bin/bash\ncat >/dev/null\nexit 0\n' > "$SB/pass.sh"; printf '#!/bin/bash\ncat >/dev/null\necho blocked >&2; exit 2\n' > "$SB/exit2.sh"; chmod +x "$SB"/*.sh
IN='{"session_id":"s1","cwd":"/w","tool_name":"Bash","tool_input":{"command":"git merge x"}}'
run() { OUT=$(printf '%s' "$IN" | LRM_NIGHT_HOUR="$1" "$NA" "$SB/$2.sh" 2>/dev/null); RC=$?; }
dec() { printf '%s' "$OUT" | jq -r '.hookSpecificOutput.permissionDecision // "none"'; }

run 2 ask;  chk "스위치 꺼짐: 밤에도 ask 유지" '[ "$(dec)" = ask ] && [ ! -e "$LRM_NIGHT_INBOX" ]'
"$NA" --on >/dev/null
run 14 ask; chk "켜짐·낮: ask 유지" '[ "$(dec)" = ask ] && [ ! -e "$LRM_NIGHT_INBOX" ]'
run 23 ask; chk "켜짐·창 시작 시각: deny 로 바꾸고 결재함 기록" '[ "$(dec)" = deny ] && [ "$(wc -l < "$LRM_NIGHT_INBOX" | tr -d " ")" = 1 ]'
run 6 ask;  chk "자정 넘긴 창 안(06시)도 바꿈" '[ "$(dec)" = deny ]'
run 7 ask;  chk "창 끝 시각(07시)은 미포함" '[ "$(dec)" = ask ]'
chk "결재함 한 줄에 사유·세션·명령" 'jq -e "select(.reason==\"r-ask\" and .session==\"s1\" and (.input|test(\"git merge\")))" "$LRM_NIGHT_INBOX" >/dev/null'
run 2 deny; chk "deny 는 그대로(사유 보존)" '[ "$(dec)" = deny ] && printf "%s" "$OUT" | grep -q r-deny'
run 2 pass; chk "통과는 출력 없음·종료 0" '[ -z "$OUT" ] && [ "$RC" = 0 ]'
run 2 exit2; chk "종료 코드 2(차단)는 유지" '[ "$RC" = 2 ]'
printf 'window_start\tx\t\n' > "$LRM_NIGHT_POLICY"
run 2 ask;  chk "정책 표 이상: 창 밖으로 보고 ask 유지" '[ "$(dec)" = ask ]'
"$NA" --off >/dev/null; chk "--off 로 끔" '"$NA" --status | grep -q "switch=off"'
echo "----"; echo "통과 $PASS · 실패 $FAIL"; [ "$FAIL" = 0 ]
