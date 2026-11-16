#!/usr/bin/env bash
# 지표 재측정 시험: 가짜 HOME 의 세션 기록 3개(메인·허브·워커)로 measure.sh 를 돌려 분류·전송·자리 집계가 나오는지 확인.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
M="$HERE/../../tools/metrics/measure.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/metrics-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
P="$SB/home/.claude/projects"
mkdir -p "$P/-x-orca-LoveRevolutionMaker" "$P/-x-orca-workspaces-LoveRevolutionMaker-coord-ui-hub" "$P/-x-orca-workspaces-LoveRevolutionMaker-coord-ui-hub-w1"
line() { printf '{"timestamp":"%s","message":{"content":[{"type":"tool_use","id":"%s","name":"Bash","input":{"command":"%s"}}]}}\n{"timestamp":"%s","message":{"content":[{"type":"tool_result","tool_use_id":"%s","content":"ok"}]}}\n' "$1" "$2" "$3" "$4" "$2"; }
line 2026-11-10T01:00:00 a1 'orca terminal send --terminal term_x 안녕하세요 허브 작업 지시' 2026-11-10T01:00:05 > "$P/-x-orca-LoveRevolutionMaker/m.jsonl"
line 2026-11-10T02:00:00 b1 'unity-run --name ui:render -- bash render.sh' 2026-11-10T02:03:00 > "$P/-x-orca-workspaces-LoveRevolutionMaker-coord-ui-hub/h.jsonl"
line 2026-11-10T03:00:00 c1 'echo work' 2026-11-10T03:00:01 > "$P/-x-orca-workspaces-LoveRevolutionMaker-coord-ui-hub-w1/w.jsonl"
line 2026-10-01T03:00:00 d1 'echo old' 2026-10-01T03:00:01 >> "$P/-x-orca-workspaces-LoveRevolutionMaker-coord-ui-hub-w1/w.jsonl"
HOME="$SB/home" bash "$M" --since 2026-11-10 --until 2026-11-10 --out "$SB/out" >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 0 ] && ok "measure.sh 4단계 완료" || ko "measure.sh" "rc=$rc $(cat "$SB/err")"
R="$SB/out/report.txt"
grep -q "세션 3" "$R" && ok "기간 안 세션 3개 수집" || ko "세션 수" "$(head -3 "$R")"
grep -q "'main': 1" "$R" && grep -q "'hub': 1" "$R" && grep -q "'worker': 1" "$R" && ok "메인·허브·워커 분류" || ko "분류" "$(grep 분류 "$R")"
grep -q "^전송 1" "$R" && ok "메인→허브 전송 1건" || ko "전송" "$(grep 전송 "$R")"
grep -q "^Unity 자리 요청 1" "$R" && grep -q "^자리 명령 1 중앙(초) 180" "$R" && ok "unity-run 자리 명령·보유 시간" || ko "자리" "$(grep 자리 "$R")"
bash "$M" --since 2026-11-10 >/dev/null 2>&1; [ $? = 2 ] && ok "인자 부족 2" || ko "인자 부족"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
