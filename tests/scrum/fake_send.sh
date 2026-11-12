#!/bin/bash
# 가짜 알림. 인자를 FAKE_DIR/sent.log 에 한 건씩 기록. FAKE_SEND_FAILS 번까지는 종료 코드 1(API 실패)
printf '%s\n===\n' "$*" >> "${FAKE_DIR:?}/sent.log"
n=$(cat "$FAKE_DIR/send-calls" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE_DIR/send-calls"
[ "$n" -le "$(cat "$FAKE_DIR/send-fails" 2>/dev/null || echo 0)" ] && exit 1
echo "ok 1700000000.000100"
