#!/bin/bash
# 셸 테스트 전체 실행. 스크립트 문법 검사 후 케이스 실행
cd "$(dirname "$0")/.." || exit 2
rc=0
for f in lrm-hubs/*.sh tests/*.sh; do bash -n "$f" || rc=1; done
for t in tests/test_*.sh; do echo "== $t"; bash "$t" || rc=1; done
exit "$rc"
