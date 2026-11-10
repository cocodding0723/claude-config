#!/bin/bash
# 가짜 du -sk. 호출 기록만 남기고 2GB 를 돌려줌
echo "$*" >> "${FAKE_ORCA_DIR:?}/du.calls"
printf '2097152\t%s\n' "${2:-}"
