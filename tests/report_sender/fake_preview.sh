#!/bin/bash
# html_preview.sh 대역: <html> <출력.png> 를 받아 작은 PNG 흉내 파일을 씀. FAKE_PREVIEW_FAIL 이 있으면 실패
[ -z "${FAKE_PREVIEW_FAIL:-}" ] || exit 1
printf 'png' > "$2"
echo "$2"
