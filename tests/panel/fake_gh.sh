#!/bin/bash
# 가짜 gh. FAKE_GH_MODE=ok|fail|auth. 호출 인자만 기록
D="${FAKE_ORCA_DIR:?}"
echo "$*" >> "$D/gh.calls"
case "${FAKE_GH_MODE:-ok}" in
  fail) echo "network down" >&2; exit 1 ;;
  auth) echo "To get started with GitHub CLI, please run:  gh auth login" >&2; exit 4 ;;
esac
cat "$D/prs.json"
