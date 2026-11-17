#!/usr/bin/env bash
# 작업 공간 = Slack 스레드 시험 묶음. 임시 작업판·가짜 알림·가짜 orca 만 씀(실제 Slack·작업판·Orca 무관).
# 사용: tests/ws_thread/run.sh [unittest 인자]   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
export BOARD_INCIDENTS="${TMPDIR:-/tmp}/ws-thread-incidents.$$.jsonl"   # 실제 작업판 사건 기록 보호
trap 'rm -f "$BOARD_INCIDENTS"' EXIT
rc=0
(cd "$HERE" && python3 -m unittest discover -v -s . -p 'test_*.py' "$@") || rc=1
[ $# -gt 0 ] || bash "$HERE/lifecycle.sh" || rc=1   # 인자(-k 등)로 일부만 돌릴 때는 셸 시험을 건너뜀
exit "$rc"
