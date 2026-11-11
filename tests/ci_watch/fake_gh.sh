#!/bin/bash
# 가짜 gh. api → comments-N.json, pr view → 고정 JSON, run download → artifact/ 복사. pr list → FAKE_DIR/prs.json, pr checks N → FAKE_DIR/checks-N.json, run view → FAKE_DIR/log.txt
D="${FAKE_DIR:?}"
case "$1 $2" in
  "pr list") cat "$D/prs.json" ;;
  "pr checks") cat "$D/checks-$3.json" ;;
  "run view") cat "$D/log.txt" ;;
  "run download") [ -d "$D/artifact" ] && cp -R "$D/artifact/." "$9" ;;
  "pr view") echo "{\"number\":$3,\"title\":\"t\",\"headRefName\":\"b\"}" ;;
  api\ *) n=$(echo "$2" | sed -E 's#.*/issues/([0-9]+)/.*#\1#'); cat "$D/comments-$n.json" 2>/dev/null || echo '[]' ;;
  *) echo "미지원: $*" >&2; exit 1 ;;
esac
