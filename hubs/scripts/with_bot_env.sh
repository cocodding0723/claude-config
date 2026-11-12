#!/bin/bash
# slack-bot.env 를 환경 변수로 읽은 뒤 인자로 받은 명령 실행. 출력 없음, 값 노출 없음.
# launchd plist 와 같은 방식. 예: with_bot_env.sh python3 -I slack_list_create.py --json
set -a
# shellcheck disable=SC1091
. "${LRM_BOT_ENV:-$HOME/.config/lrm-hubs/slack-bot.env}" 2>/dev/null || { echo "오류: slack-bot.env 를 읽지 못함. 파일 존재·권한 확인" >&2; exit 2; }
set +a
[ "$#" -gt 0 ] || { echo "사용: with_bot_env.sh <명령> [인자…]" >&2; exit 2; }
exec "$@"
