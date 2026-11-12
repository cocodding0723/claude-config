#!/usr/bin/env bash
# 명령 접수기 시험. Slack·토큰·라이브 장부는 쓰지 않음(임시 폴더, 가짜 inbox).
# 사용: tests/intake/run.sh [unittest 인자]   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE" && exec python3 -m unittest discover -v -s . -p 'test_*.py' "$@"
