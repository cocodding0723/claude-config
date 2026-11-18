#!/usr/bin/env bash
# 허브 보고 기록·전송기 시험. Slack·토큰·라이브 파일은 쓰지 않음(임시 폴더, 가짜 전송 스크립트).
# 사용: tests/report_sender/run.sh [unittest 인자]   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
export BOARD_INCIDENTS="${TMPDIR:-/tmp}/report-sender-incidents.$$.jsonl"   # 실제 작업판 사건 기록 보호
trap 'rm -f "$BOARD_INCIDENTS"' EXIT
# PDF 단계는 실제 Chrome 을 띄우므로 시험에서는 끔(없는 스크립트 = 생략). PDF 를 보는 시험은 Conf(pdf=가짜 스크립트)로 켬
export LRM_REPORT_PDF="${LRM_REPORT_PDF:-$HERE/no-pdf-in-tests.sh}"
cd "$HERE" && python3 -m unittest discover -v -s . -p 'test_*.py' "$@"
