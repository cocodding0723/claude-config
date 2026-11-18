#!/bin/bash
# HTML 을 PDF 로. Slack 은 HTML 을 웹 페이지로 열지 않아(이진·소스 표시) 바로 열리는 PDF 를 함께 올리는 데 씀.
# 사용: html_pdf.sh <html> [출력.pdf]   종료 코드: 0 성공(출력 경로 한 줄), 1 PDF 가 안 만들어짐, 2 Chrome 없음
set -eu
IN="$1"; OUT="${2:-${IN%.*}.pdf}"
C="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$C" ] || { echo "Chrome 없음" >&2; exit 2; }
TMP=$(mktemp -d); trap 'rm -rf -- "${TMP:?}"' EXIT
"$C" --headless=new --disable-gpu --no-pdf-header-footer --user-data-dir="$TMP" --print-to-pdf="$OUT" "file://$IN" >/dev/null 2>&1 || true
[ -s "$OUT" ] || { echo "PDF 생성 실패: $IN" >&2; exit 1; }
echo "$OUT"
