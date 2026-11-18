#!/usr/bin/env bash
# 허브 보고 접수 줄 가독성 검사 시험(board_cards.text_gaps 를 hub_report 가 부르는 경로). 임시 폴더만 씀, Slack·라이브 파일 미사용.
# 사용: tests/hub_report/run.sh [unittest 인자]   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE" && python3 -m unittest discover -v -s . -p 'test_*.py' "$@"
