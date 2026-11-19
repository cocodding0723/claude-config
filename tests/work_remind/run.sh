#!/usr/bin/env bash
# 남은 작업 리마인드 시험. 작업판·Slack·토큰은 쓰지 않음(순수 함수, 임시 폴더의 가짜 전송 스크립트).
# 사용: tests/work_remind/run.sh [unittest 인자]   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE" && python3 -m unittest discover -v -s . -p 'test_*.py' "$@"
