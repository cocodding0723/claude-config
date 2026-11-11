#!/bin/bash
# 가짜 orca. 인자 전체를 FAKE_DIR/orca.log 에 기록
printf "%s\n" "$*" >> "${FAKE_DIR:?}/orca.log"
