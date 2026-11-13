#!/usr/bin/env bash
# 결정 답 전달 경로(answer_route) 시험. 가짜 orca·임시 폴더만 쓰며 실제 Orca·터미널·~/.config/lrm-hubs 는 건드리지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
out=$(python3 "$HERE/test_route.py" 2>&1); r=$?
printf 'test_route.py: %s\n' "$(echo "$out" | grep -E '^(Ran |OK|FAILED)' | tr '\n' ' ')"
[ "$r" = 0 ] || echo "$out" | grep -E '^(FAIL:|ERROR:)|Error|Assertion' | head -20
exit "$r"
