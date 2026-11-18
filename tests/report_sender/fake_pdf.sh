#!/bin/bash
# html_pdf.sh 대역: <html> <출력.pdf> 를 받아 작은 PDF 흉내 파일을 씀. FAKE_PDF_FAIL 이 있으면 실패
[ -z "${FAKE_PDF_FAIL:-}" ] || exit 1
printf '%%PDF-1.4' > "$2"
echo "$2"
