#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd -P)
SB=$(mktemp -d); trap 'rm -rf "$SB"' EXIT
mkdir -p "$SB/bin" "$SB/board"
export BOARD_BIN="$SB/bin" BOARD_DIR="$SB/board" PATH="$SB/bin:$PATH"
cat > "$SB/bin/bd" <<'EOF'
#!/usr/bin/env bash
printf '[{"id":"lrm-test","status":"open","created_at":"2026-11-21"}]\n'
EOF
cat > "$SB/bin/board-stage" <<'EOF'
#!/usr/bin/env bash
printf '단계: 3 구현\n크기: 보통\n계획 승인: 됨 (D-PLANTEST)\n다음 할 일:\n- 관련 시험을 돌린다.\n'
EOF
cat > "$SB/bin/board-said" <<'EOF'
#!/usr/bin/env bash
echo '미처리: 처리 안 적힌 지시 #2, #3. 조치: 작업판 확인' >&2
exit 6
EOF
chmod +x "$SB/bin/"*
RESTORE="$ROOT/claude/hooks/lib/compact_restore.sh"
PRE="$ROOT/claude/hooks/lib/pre_compact.sh"
out=$(bash "$RESTORE" /w/coord-feat-test-hub)
for expected in lrm-test '3 구현' D-PLANTEST '다음 할 일:' '관련 시험' '#2, #3' 'bd comments add lrm-test' '2분' 'hub_report.py'; do
  [[ "$out" == *"$expected"* ]]
done
printf '%s' "$out" | python3 -c 'import sys; assert len(sys.stdin.read()) <= 2000'
printf '\nPASS 압축 복원: 카드·단계·승인·지시·진행 규칙·다음 할 일\n'
printf 'python3 -c '\''print("가" * 10000)'\''\n' >> "$SB/bin/board-stage"
bash "$RESTORE" /w/coord-feat-test-hub | python3 -c 'import sys; s=sys.stdin.read(); assert len(s) <= 2000 and "[생략]" in s and "#2, #3" in s'
echo 'PASS 압축 복원: 긴 출력 2000자 이하'
for trigger in manual auto; do
  out=$(jq -cn --arg t "$trigger" '{cwd:"/w/coord-feat-test-hub",trigger:$t}' | bash "$PRE")
  for expected in '기능 카드 id' '현재 단계' '결정 id' '작업자' '카드 id' '원문 지시 번호' '도구 출력 원문' '파일 덤프'; do
    [[ "$out" == *"$expected"* ]]
  done
  printf '%s' "$out" | python3 -c 'import sys; assert 0 < len(sys.stdin.read()) <= 1500'
done
echo 'PASS 압축 요약 지시: 자동·수동, 보존·제외 항목, 1500자 이하'
for cwd in /w/repo /w/coord-other-hub ''; do
  [ -z "$(jq -cn --arg cwd "$cwd" '{cwd:$cwd}' | bash "$PRE")" ]
done
[ -z "$(printf 'invalid json' | bash "$PRE")" ]
[ -z "$(printf '{"cwd":42}' | bash "$PRE")" ]
[ -z "$(bash "$RESTORE" /w/repo)" ]
echo 'PASS 대상 밖·잘못된 입력: 출력 없음, 종료 0'
jq -e '.hooks.PreCompact | length == 1 and .[0].hooks[0].command == "bash ~/.claude/hooks/lib/pre_compact.sh"' "$ROOT/claude/settings.base.json" >/dev/null
echo 'PASS PreCompact 설정 등록'
