#!/bin/bash
# 가짜 gh. pr list → FAKE_DIR/prs.json, pr checks N → FAKE_DIR/checks-N.json, run view → FAKE_DIR/log.txt
D="${FAKE_DIR:?}"
case "$1 $2" in
  "pr list") cat "$D/prs.json" ;;
  "pr checks") cat "$D/checks-$3.json" ;;
  "run view") cat "$D/log.txt" ;;
  *) echo "미지원: $*" >&2; exit 1 ;;
esac
