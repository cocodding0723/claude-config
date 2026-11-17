#!/bin/bash
# 작업판 주기 정리기 진입점. 본체는 같은 폴더의 board_sweep.py. launchd(ai.lrm.boardsweep)가 30분마다 호출.
# 사용: board_sweep.sh [--dry-run] [--json] | --help
# 종료 코드: 0 정상 · 2 인자·규칙 표 오류 · 3 bd 목록 실패 · 4 다른 실행이 잠금 보유
set -eu
. "$(dirname "$0")/lib/py.sh"  # PY = 3.10 이상 python3 (launchd 짧은 PATH 의 3.9 방지)
unset SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_USER_ID
# launchd 의 짧은 PATH 에서도 bd·gh 를 찾도록 뒤에 덧붙임(앞의 PATH 가 우선)
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"
export PYTHONDONTWRITEBYTECODE=1
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$HERE/board_sweep.py" ] || { echo "board_sweep.py 없음 — 같은 폴더에 링크 필요(links.tsv)" >&2; exit 2; }
exec "$PY" -I "$HERE/board_sweep.py" "$@"
