#!/usr/bin/env bash
# switch-account 시험. 가짜 계정 폴더·가짜 claude 로 복사·실행 인자만 확인
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P); SW="$HERE/../../claude/bin/switch-account"
SB=$(mktemp -d "${TMPDIR:-/tmp}/switch.XXXXXX"); SB=${SB//\/\//\/}; trap 'rm -rf "$SB"' EXIT
export LRM_CLAUDE_PROFILES="$SB/p" LRM_USAGE_LIMIT_LOG="$SB/log.jsonl" LRM_CLAUDE_BIN="$SB/claude"
SRC="$SB/p/aaaa1111-x/home/projects/-w"; DST="$SB/p/bbbb2222-y/home/projects/-w"
mkdir -p "$SRC/s1" "$SB/p/bbbb2222-y/home" "$SB/w"
printf '{"type":"x"}\n{"cwd":"%s"}\n' "$SB/w" > "$SRC/s1.jsonl"
echo sub > "$SRC/s1/agent.jsonl"
printf '#!/bin/bash\necho "$CLAUDE_CONFIG_DIR|$(pwd -P)|$*" > %s/ran\n' "$SB" > "$SB/claude"; chmod +x "$SB/claude"
printf '{"at":"%s","account":"aaaa1111","resets":"3pm"}\n' "$(date '+%Y-%m-%dT%H:%M:%S')" > "$LRM_USAGE_LIMIT_LOG"
PASS=0 FAIL=0
chk() { if eval "$2"; then PASS=$((PASS+1)); echo "PASS  $1"; else FAIL=$((FAIL+1)); echo "FAIL  $1 — $OUT"; fi; }

OUT=$("$SW" --list)
chk "목록: 여유 계정이 위, 한도 계정 표시" '[ "$(echo "$OUT" | sed -n 2p | cut -f1)" = bbbb2222 ] && echo "$OUT" | grep -q "aaaa1111.한도 걸림.*리셋 3pm"'
OUT=$("$SW" bbbb s1 --dry-run)
chk "dry-run: 복사 안 함" '[ ! -e "$DST/s1.jsonl" ] && echo "$OUT" | grep -q -- "--resume s1"'
OUT=$("$SW" bbbb s1 --bg)
chk "기록·하위 폴더 복사" '[ -f "$DST/s1.jsonl" ] && [ -f "$DST/s1/agent.jsonl" ]'
chk "대상 계정·원래 작업 폴더로 --bg --resume" 'grep -qx "$SB/p/bbbb2222-y/home|$(cd "$SB/w" && pwd -P)|--bg --resume s1" "$SB/ran"'
OUT=$("$SW" zzzz s1 2>&1); RC=$?
chk "없는 계정: 종료 1" '[ "$RC" = 1 ]'
OUT=$("$SW" bbbb nope 2>&1); RC=$?
chk "없는 세션: 종료 1" '[ "$RC" = 1 ]'
OUT=$("$SW" bbbb 2>&1); RC=$?
chk "인자 부족: 종료 2" '[ "$RC" = 2 ]'
echo "----"; echo "통과 $PASS · 실패 $FAIL"; [ "$FAIL" = 0 ]
