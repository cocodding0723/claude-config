#!/usr/bin/env bash
# usage-limit.sh 시험. 가짜 hub_report 로 보고 본문만 받고 실제 outbox 는 건드리지 않음
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P); UL="$HERE/../../claude/hooks/usage-limit.sh"
SB=$(mktemp -d "${TMPDIR:-/tmp}/usage-limit.XXXXXX"); trap 'rm -rf "$SB"' EXIT
export LRM_USAGE_LIMIT_LOG="$SB/log.jsonl" LRM_HUB_REPORT="$SB/report.py"
printf 'import sys\nopen(sys.argv[0]+".out","a").write(sys.argv[sys.argv.index("--text")+1]+"\\n---\\n")\n' > "$LRM_HUB_REPORT"
PASS=0 FAIL=0
chk() { if eval "$2"; then PASS=$((PASS+1)); echo "PASS  $1"; else FAIL=$((FAIL+1)); echo "FAIL  $1"; fi; }
run() { printf '%s' "$1" | CLAUDE_CONFIG_DIR="$2" "$UL"; RC=$?; }

run '{"error":"server_error","session_id":"s0"}' /x
chk "rate_limit 아니면 무시" '[ "$RC" = 0 ] && [ ! -e "$LRM_USAGE_LIMIT_LOG" ]'
run '{"error":"rate_limit","session_id":"s1","cwd":"/w","last_assistant_message":"You have hit your limit · resets 3:45pm (Asia/Seoul)"}' "/a/claude-profiles/0b48e852-9ec1/home"
chk "기록 한 줄: 계정 앞 8자·세션·리셋" 'jq -e "select(.account==\"0b48e852\" and .session==\"s1\" and .resets==\"3:45pm (Asia/Seoul)\")" "$LRM_USAGE_LIMIT_LOG" >/dev/null'
chk "보고에 리셋·전환 명령" 'grep -q "리셋 3:45pm" "$LRM_HUB_REPORT.out" && grep -q "switch-account <계정> s1" "$LRM_HUB_REPORT.out"'
run '{"error":"rate_limit","session_id":"s2","last_assistant_message":"API Error: Rate limit reached"}' /plain
chk "리셋 문구 없으면 미확인·계정 default" 'grep -q "계정 default 멈춤 — 리셋 미확인" "$LRM_HUB_REPORT.out"'
LRM_HUB_REPORT="$SB/none.py" run '{"error":"rate_limit","session_id":"s3"}' /x
chk "보고 도구 없어도 종료 0" '[ "$RC" = 0 ]'
echo "----"; echo "통과 $PASS · 실패 $FAIL"; [ "$FAIL" = 0 ]
