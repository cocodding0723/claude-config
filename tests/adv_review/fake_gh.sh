#!/bin/bash
# 가짜 gh. pr view → 고정 JSON, pr diff → FAKE_DIR/diff.txt
case "$1 $2" in
  "pr view") echo '{"number":'"$3"',"title":"[LRM-400] eye","headRefName":"feat/LRM-400","body":"본문"}' ;;
  "pr diff") cat "${FAKE_DIR:?}/diff.txt" ;;
  *) exit 1 ;;
esac
