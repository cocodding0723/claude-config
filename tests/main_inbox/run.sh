#!/usr/bin/env bash
# 메인 받은 편지함 모듈 시험. LRM_HUB_DIR 임시 폴더만 씀(라이브 main.jsonl·메인 터미널 없음).
# 사용: tests/main_inbox/run.sh [unittest 인자]   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE" && exec python3 -m unittest discover -v -s . -p 'test_*.py' "$@"
