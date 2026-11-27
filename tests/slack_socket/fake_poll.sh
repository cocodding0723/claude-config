#!/bin/bash
# 시험용 가짜 slack_poll.sh: 호출 횟수만 기록하고 JSON 한 줄 출력.
# FAKE_POLL_LOCKED_N=N 이면 처음 N회는 잠금 중 응답(skipped=locked), FAKE_POLL_SLEEP=초 면 응답 전 대기, FAKE_POLL_RAW 가 있으면 그 문자열을 그대로 출력
CALLS="$(dirname "$0")/poll.calls"
echo x >> "$CALLS"
[ -z "${FAKE_POLL_SLEEP:-}" ] || sleep "$FAKE_POLL_SLEEP"  # 수집이 오래 걸리는 상황 재현
if [ -n "${FAKE_POLL_RAW:-}" ]; then echo "$FAKE_POLL_RAW"; exit 0; fi
if [ "$(wc -l < "$CALLS" | tr -d ' ')" -le "${FAKE_POLL_LOCKED_N:-0}" ]; then
  echo '{"ok":true,"added":0,"skipped":"locked"}'
else
  echo '{"ok":1}'
fi
