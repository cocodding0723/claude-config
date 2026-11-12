#!/usr/bin/env bash
# 피드백 장부 도구·동기화 시험. Slack·토큰·라이브 장부는 쓰지 않음(가짜 API, 임시 폴더).
# 사용: tests/ledger/run.sh [unittest 인자]   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE" && exec python3 -m unittest discover -v -s . -p 'test_*.py' "$@"
