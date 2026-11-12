#!/usr/bin/env bash
# hub_split.py 시험. 가짜 orca·임시 폴더만 씀. 실제 허브·장부·Slack·~/.config 는 건드리지 않음.
# 사용: tests/hub_split/run.sh [unittest 인자]   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE" && exec python3 -m unittest discover -v -s . -p 'test_*.py' "$@"
