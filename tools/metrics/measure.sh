#!/usr/bin/env bash
# 하네스 지표 재측정 한 줄. 4단계(세션 수집 → 분류·동시 실행 → 전송·자리 요청 → 자리 보유)를 순서대로 돌리고 결과를 한 파일에 모음.
set -euo pipefail
usage() {
  cat <<'H'
사용: measure.sh --since YYYY-MM-DD --until YYYY-MM-DD [--out <폴더>]
  ~/.claude/projects·~/.codex/sessions 의 기간 안(KST) 기록을 읽어 지표를 낸다. 읽기만 하고 원본은 바꾸지 않음.
  결과: <폴더>/report.txt (기본 폴더 ${TMPDIR:-/tmp}/harness-metrics-<since>-<until>)
예: tools/metrics/measure.sh --since 2026-11-10 --until 2026-11-16
종료: 0 완료 / 2 사용법 / 1 단계 실패(어느 단계인지 출력)
H
}
SINCE="" UNTIL="" OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --since) SINCE="${2:?--since 값 필요}"; shift 2 ;;
    --until) UNTIL="${2:?--until 값 필요}"; shift 2 ;;
    --out) OUT="${2:?--out 값 필요}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "오류: 알 수 없는 인자 '$1'. measure.sh --help 참고" >&2; exit 2 ;;
  esac
done
[ -n "$SINCE" ] && [ -n "$UNTIL" ] || { usage >&2; exit 2; }
HERE=$(cd "$(dirname "$0")" && pwd -P)
OUT="${OUT:-${TMPDIR:-/tmp}/harness-metrics-$SINCE-$UNTIL}"
mkdir -p "$OUT"
: > "$OUT/report.txt"
for s in sess conc msg slot; do
  echo "== $s" >> "$OUT/report.txt"
  python3 "$HERE/$s.py" --since "$SINCE" --until "$UNTIL" --work "$OUT" >> "$OUT/report.txt" 2>&1 \
    || { echo "오류: $s 단계 실패. $OUT/report.txt 끝 확인" >&2; exit 1; }
done
echo "결과: $OUT/report.txt"
