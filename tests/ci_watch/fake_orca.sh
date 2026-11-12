#!/bin/bash
# 가짜 orca. 인자 전체를 FAKE_DIR/orca.log 에 기록. 'worktree list' 는 FAKE_DIR/worktrees.json 을 출력(없으면 실패)
printf "%s\n" "$*" >> "${FAKE_DIR:?}/orca.log"
if [ "$1 $2" = "worktree list" ]; then
  cat "$FAKE_DIR/worktrees.json" 2>/dev/null || exit 1
fi
