#!/bin/bash
# 가짜 git -C <경로> merge-base --is-ancestor HEAD <기준>. 경로에 -merged 가 있으면 병합됨(0), 아니면 1
# 경로를 뺀 인자만 기록(읽기 명령만 쓰는지 시험)
echo "${*:3}" >> "${FAKE_ORCA_DIR:?}/git.calls"
case "$2" in *-merged) exit 0 ;; *) exit 1 ;; esac
