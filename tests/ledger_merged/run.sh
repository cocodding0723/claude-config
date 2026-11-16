#!/usr/bin/env bash
# 병합 PR 자동 완료(ledger_merged.py) 시험. 가짜 gh·임시 장부만 씀(라이브 장부·GitHub 접근 없음).
# 사용: tests/ledger_merged/run.sh [unittest 인자]   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE" && exec python3 -m unittest discover -v -s . -p 'test_*.py' "$@"
