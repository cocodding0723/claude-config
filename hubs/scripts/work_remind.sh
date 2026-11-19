#!/bin/bash
# 남은 작업 리마인드 진입점. 본체는 같은 폴더의 work_remind.py. launchd(ai.lrm.workremind)가 30분마다 호출.
# 사용: work_remind.sh [--dry-run] [--json] | --help
# 종료 코드: 0 보냄·보낼 것 없음·조용한 시간 · 3 작업판 조회 실패 · 4 전송 실패
set -eu
. "$(dirname "$0")/lib/py.sh"  # PY = 3.10 이상 python3 (launchd 짧은 PATH 의 3.9 방지)
# launchd 의 짧은 PATH 에서도 bd 를 찾도록 뒤에 덧붙임(앞의 PATH 가 우선)
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"
export PYTHONDONTWRITEBYTECODE=1
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$HERE/work_remind.py" ] || { echo "work_remind.py 없음 — 같은 폴더에 링크 필요(links.tsv)" >&2; exit 2; }
exec "$PY" -I "$HERE/work_remind.py" "$@"
