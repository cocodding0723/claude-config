#!/bin/bash
# 가짜 Slack 전송 스크립트(WS_GC_NOTIFY 로 지정). 실전송 없이 인자를 notify.calls 에 한 줄로 기록(줄바꿈은 ⏎).
printf '%s\n' "$(printf '%s ' "$@" | tr '\n' '⏎')" >> "${FAKE_DIR:?}/notify.calls"
