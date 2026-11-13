#!/usr/bin/env bash
# HEAD(또는 <커밋>)의 로컬 게이트 통과 기록을 PR 검증 절용 `명령 — 결과` 줄로 출력. 기록 없으면 종료 1(출력 없음).
# 사용: gate-lines.sh [커밋]   기록 위치: ${LRM_LOCAL_GATE_DIR:-~/.config/lrm-hubs/local-gate}/<커밋>.<기준>.json
set -u
sha=$(git rev-parse "${1:-HEAD}" 2>/dev/null) || { echo "gate-lines: 커밋을 찾을 수 없음" >&2; exit 2; }
f=$(ls -t "${LRM_LOCAL_GATE_DIR:-$HOME/.config/lrm-hubs/local-gate}/$sha".*.json 2>/dev/null | grep -v "\.last\.json$" | head -1)
[ -n "$f" ] && [ -f "$f" ] || { echo "gate-lines: 통과 기록 없음(${LRM_LOCAL_GATE_DIR:-$HOME/.config/lrm-hubs/local-gate}/$sha.*.json) — push 전 게이트를 통과해야 함" >&2; exit 1; }
jq -r --arg s "${sha:0:8}" '
  "- 로컬 게이트 `Tools/ci/local-gate.sh --base \(.base)` — 통과 (\(.total_seconds // "?")초, 커밋 \($s))",
  (.steps[] | "  - `\(.name)` — \(.status) (\(.seconds // "?")초)\(if (.note // "") != "" then " · " + .note else "" end)")' "$f"
