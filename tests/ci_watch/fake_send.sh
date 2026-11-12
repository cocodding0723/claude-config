#!/bin/bash
# 가짜 알림. 인자 전체를 FAKE_DIR/sent.log 에 한 건씩 기록
printf '%s\n===\n' "$*" >> "${FAKE_DIR:?}/sent.log"
echo "ok 1700000000.000100"
