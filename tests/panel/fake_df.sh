#!/bin/bash
# 가짜 df -k. FAKE_DF_AVAIL 가용 KB(기본 100GB), FAKE_DF_MODE=fail|bad. 인자 기록
echo "$*" | sed 's|/.*|<path>|' >> "${FAKE_ORCA_DIR:?}/df.calls"
case "${FAKE_DF_MODE:-}" in fail) exit 1 ;; bad) echo "garbage"; exit 0 ;; esac
echo "Filesystem 1024-blocks Used Available Capacity iused ifree %iused Mounted on"
echo "/dev/disk3s5 970000000 800000000 ${FAKE_DF_AVAIL:-104857600} 89% 1 2 1% /System/Volumes/Data"
