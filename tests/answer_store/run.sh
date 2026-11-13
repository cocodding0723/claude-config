#!/usr/bin/env bash
# 결정 답 공용 기록(answer_store) 시험. 임시 폴더만 쓰며 실제 ~/.config/lrm-hubs·Slack 은 건드리지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
out=$(python3 "$HERE/test_store.py" 2>&1); r=$?
printf 'test_store.py: %s\n' "$(echo "$out" | grep -E '^(Ran |OK|FAILED)' | tr '\n' ' ')"
[ "$r" = 0 ] || echo "$out" | grep -E '^(FAIL:|ERROR:)|Error|Assertion' | head -20
exit "$r"
