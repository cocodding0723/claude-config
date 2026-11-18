#!/bin/bash
# HTML 을 모바일 폭(390) 미리보기 PNG 로. 사용: html_preview.sh <html> [출력.png] [높이(기본 2400)]
set -eu
IN="$1"; OUT="${2:-${IN%.*}-preview.png}"; H="${3:-2400}"
C="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$C" ] || { echo "Chrome 없음" >&2; exit 2; }
URL=$(python3 -c 'import pathlib,sys;print(pathlib.Path(sys.argv[1]).resolve().as_uri())' "$IN")  # 경로의 # ? 공백이 URL 로 잘리지 않게
TMP=$(mktemp -d); trap 'rm -rf -- "${TMP:?}"' EXIT
"$C" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size=390,"$H" --user-data-dir="$TMP" --screenshot="$OUT" "$URL" >/dev/null 2>&1
echo "$OUT"
