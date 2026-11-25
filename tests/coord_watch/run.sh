#!/usr/bin/env bash
# 멈춘 코디네이터 깨우기 시험. 작업판·Orca·Slack 은 쓰지 않음(임시 폴더, 가짜 함수). 종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
cd "$(dirname "$0")" && python3 -m unittest discover -q -s . -p 'test_*.py'
