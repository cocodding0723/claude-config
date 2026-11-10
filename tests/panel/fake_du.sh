#!/bin/bash
# 가짜 du -sk. 호출 기록만 남기고 2GB 를 돌려줌
# FAKE_DU_SLEEP=<초> 면 자식 sleep 을 띄워 기다림(시간 초과 시 자식까지 정리되는지 시험). 자식 번호는 du.child
echo "$*" >> "${FAKE_ORCA_DIR:?}/du.calls"
if [ -n "${FAKE_DU_SLEEP:-}" ]; then
  sleep "$FAKE_DU_SLEEP" &
  echo $! > "$FAKE_ORCA_DIR/du.child"
  wait
fi
printf '2097152\t%s\n' "${2:-}"
