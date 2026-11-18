#!/usr/bin/env bash
# 결정 답 공용 기록(answer_store) 시험. 임시 폴더만 쓰며 실제 ~/.config/lrm-hubs·Slack 은 건드리지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
rc=0
for t in test_store.py test_terminal.py; do
  out=$(python3 "$HERE/$t" 2>&1); r=$?
  printf '%s: %s\n' "$t" "$(echo "$out" | grep -E '^(Ran |OK|FAILED)' | tr '\n' ' ')"
  [ "$r" = 0 ] || { rc=1; echo "$out" | grep -E '^(FAIL:|ERROR:)|Error|Assertion' | head -20; }
done
exit "$rc"
