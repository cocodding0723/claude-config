#!/usr/bin/env bash
# 야간 디스패처 시험. 라이브 장부·허브·Slack 은 쓰지 않음(임시 폴더).
# 사용: tests/night_dispatch/run.sh [unittest 인자]   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE" && exec python3 -m unittest discover -v -s . -p 'test_*.py' "$@"
