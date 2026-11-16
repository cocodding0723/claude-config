#!/usr/bin/env bash
# 가짜 gh: FAKE_GH_DIR 의 merged.json·open.json·view-<번호>.json 을 그대로 출력. fail 파일이 있으면 실패. 호출 기록은 calls.log
d="${FAKE_GH_DIR:?}"
echo "$*" >> "$d/calls.log"
[ -f "$d/fail" ] && { echo "auth required" >&2; exit 4; }
[ "$1 $2" = "pr list" ] || [ "$1 $2" = "pr view" ] || { echo "unsupported: $*" >&2; exit 2; }
case "$*" in
  *"--state merged"*) cat "$d/merged.json" ;;
  *"--state open"*) cat "$d/open.json" ;;
  "pr view"*) f="$d/view-$3.json"; [ -f "$f" ] && cat "$f" || { echo "not found" >&2; exit 1; } ;;
esac
