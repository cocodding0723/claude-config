#!/bin/bash
# 옆 패널 자동 칸 수집기 진입점. 본체는 같은 폴더의 panel_collect.py. launchd(ai.lrm.panelcollect)가 90초마다 --once 로 호출.
# 사용: panel_collect.sh --once | --print | --help   (자세한 설명은 --help)
# 종료 코드: 0 정상(일부 원료 실패 포함), 2 인자·설정 오류
set -eu
# 이 수집기는 Slack 토큰을 쓰지 않음. 상속된 값이 orca·gh 자식에 새지 않게 지움
unset SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_USER_ID
# launchd 의 짧은 PATH 에서도 orca·gh 를 찾도록 뒤에 덧붙임(앞의 PATH 가 우선)
export PATH="$PATH:/Applications/Orca.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin"
export PYTHONDONTWRITEBYTECODE=1
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$HERE/panel_collect.py" ] || { echo "panel_collect.py 없음 — 같은 폴더에 링크 필요(links.tsv)" >&2; exit 2; }
exec python3 -I "$HERE/panel_collect.py" "$@"
