#!/bin/bash
# 가짜 osascript. 알림 제목·본문(마지막 두 인자)을 FAKE_DIR/notify.log 에 한 줄씩
echo "${@: -2:1} | ${@: -1}" >> "${FAKE_DIR:?}/notify.log"
