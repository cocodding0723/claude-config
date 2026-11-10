#!/bin/bash
# 시험용 가짜 slack_poll.sh: 호출 횟수만 기록하고 JSON 한 줄 출력
echo x >> "$(dirname "$0")/poll.calls"
echo '{"ok":1}'
