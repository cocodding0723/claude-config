#!/usr/bin/env bash
# 문서 모아보기(doc_index·doc_send·doc_browser) 시험. 임시 폴더·가짜 전송 스크립트만 쓰며 실제 Slack·운영 폴더는 건드리지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
rc=0
for t in "$HERE"/test_*.py; do
  out=$(python3 "$t" 2>&1); r=$?
  printf '%s: %s\n' "$(basename "$t")" "$(echo "$out" | grep -E '^(Ran |OK|FAILED)' | tr '\n' ' ')"
  [ "$r" = 0 ] || { echo "$out" | grep -E '^(FAIL:|ERROR:)|Error' | head -20; rc=1; }
done
exit "$rc"
