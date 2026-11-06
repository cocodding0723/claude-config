#!/bin/zsh
# 게시 전에 한 번만 본다 — 아티팩트 뼈대를 씌워 headless Chrome 으로 찍는다.
# usage: preview.sh <index.html> <out.png> [height]
set -e
HTML=$1; OUT=$2; HEIGHT=${3:-6000}
DIR=$(cd "$(dirname "$HTML")" && pwd)
TMP="$DIR/.preview.html"
{ echo '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}img{max-width:100%}</style></head><body>'; cat "$HTML"; echo '</body></html>'; } > "$TMP"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --window-size=1280,${HEIGHT} --screenshot="$OUT" "file://$TMP" 2>/dev/null
rm -f "$TMP"
echo "$OUT"
