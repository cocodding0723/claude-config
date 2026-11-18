#!/bin/bash
# HTML 을 PDF 로. Slack 은 HTML 을 웹 페이지로 열지 않아(이진·소스 표시) 바로 열리는 PDF 를 함께 올리는 데 씀.
# 종이 크기로 쪽을 나누면 카드·표·도식이 쪽 경계에서 잘려서, 문서 길이에 맞춘 한 장짜리 긴 PDF 로 만듦(폭은 휴대폰에서 읽히는 값).
# 사용: html_pdf.sh <html> [출력.pdf]   종료 코드: 0 성공(출력 경로 한 줄), 1 PDF 가 안 만들어짐, 2 Chrome 없음
# 환경: LRM_PDF_WIDTH 쪽 폭 px(기본 480)
set -eu
IN="$1"; OUT="${2:-${IN%.*}.pdf}"; W="${LRM_PDF_WIDTH:-480}"
C="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$C" ] || { echo "Chrome 없음" >&2; exit 2; }
[ -f "$IN" ] || { echo "PDF 생성 실패: $IN" >&2; exit 1; }
TMP=$(mktemp -d); trap 'rm -rf -- "${TMP:?}"' EXIT
# 원본은 건드리지 않고 사본에 쪽 크기 지정을 넣음. 상대 경로 그림은 <base> 로 원본 폴더를 가리키게 함
python3 - "$IN" "$TMP/doc.html" "$W" <<'PY' || { echo "PDF 생성 실패: $IN" >&2; exit 1; }
import pathlib, re, sys
src, dst, width = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]), int(sys.argv[3])
MAX_H = 18000  # PDF 한 쪽 높이 상한(200인치 = 19200px) 안쪽. 더 긴 문서는 이 높이로 여러 쪽
html = src.read_text(encoding="utf-8", errors="replace")
base = f'<base href="{src.parent.as_uri()}/">'
size = (
    "<script>addEventListener('load',()=>{const d=document.documentElement,b=document.body;"
    f"const h=Math.min(Math.ceil(Math.max(d.scrollHeight,b?b.scrollHeight:0)*1.02)+40,{MAX_H});"
    "const s=document.createElement('style');"
    f"s.textContent='@page{{size:{width}px '+h+'px;margin:0}}html{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}';"
    "document.head.appendChild(s);});</script>"
)
for pat in (r"<head[^>]*>", r"<html[^>]*>", r"<!doctype[^>]*>"):  # <base> 는 doctype 앞에 두면 호환 모드가 됨
    m = re.search(pat, html, re.I)
    if m:
        html = html[:m.end()] + base + html[m.end():]
        break
else:
    html = base + html
dst.write_text(html + size, encoding="utf-8")
PY
"$C" --headless=new --disable-gpu --hide-scrollbars --no-pdf-header-footer --window-size="$W",1000 --user-data-dir="$TMP/chrome" \
  --print-to-pdf="$OUT" "file://$TMP/doc.html" >/dev/null 2>&1 || true
[ -s "$OUT" ] || { echo "PDF 생성 실패: $IN" >&2; exit 1; }
echo "$OUT"
