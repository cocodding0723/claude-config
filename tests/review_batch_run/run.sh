#!/bin/bash
# review_batch_run.sh: 실패·고칠 점이면 경고 보고, 겹친 실행은 건너뜀. 가짜 board-done·보고 도구만 씀
HERE=$(cd "$(dirname "$0")" && pwd -P); REPO=$(cd "$HERE/../.." && pwd -P); RUN="$REPO/hubs/scripts/review_batch_run.sh"
PASS=0; FAIL=0; ok() { PASS=$((PASS+1)); echo "PASS  $1"; }; ko() { FAIL=$((FAIL+1)); echo "FAIL  $1 — ${2:-}"; }
SB=$(mktemp -d); trap 'rm -rf "$SB"' EXIT
cat > "$SB/bd" <<'B'
#!/bin/bash
case "$1" in
  --review-batch-answers) a=${FAKE_ANS:-'{"answers":[]}'}; echo "$a"; exit "${FAKE_RC1:-0}";;
  --review-batch) echo "${FAKE_OUT:-모을 기능 없음}"; exit "${FAKE_RC2:-0}";;
esac
B
printf 'import sys\nopen(sys.argv[0].rsplit("/",1)[0]+"/warns.txt","a").write(sys.argv[sys.argv.index("--text")+1].splitlines()[0]+"\\n")\n' > "$SB/rep.py"
chmod +x "$SB/bd"
export LRM_BOARD_DONE="$SB/bd" LRM_HUB_REPORT="$SB/rep.py" LRM_HUB_DIR="$SB"
w() { cat "$SB/warns.txt" 2>/dev/null; }

bash "$RUN" >/dev/null 2>&1 && [ -z "$(w)" ] && ok "정상·할 일 없음: 경고 없음" || ko "정상" "$(w)"
FAKE_RC2=9 FAKE_OUT="모아 받기 카드 1/1장 올리지 못함" bash "$RUN" >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && w | grep -q "카드 올리기 실패" && ok "올리기 실패: 경고 보고·종료 0 아님" || ko "올리기 실패" "$rc $(w)"
rm -f "$SB/warns.txt"
FAKE_ANS='{"answers":[{"id":"lrm-a","verdict":"fix"},{"id":"lrm-b","verdict":"pass"}]}' bash "$RUN" >/dev/null 2>&1
w | grep -q "고칠 점 답 — 기능 lrm-a " && ! w | grep -q lrm-b && ok "고칠 점: 해당 기능만 경고" || ko "고칠 점 경고" "$(w)"
rm -f "$SB/warns.txt"; mkdir "$SB/review-batch.lock"
out=$(bash "$RUN" 2>&1); [ -z "$(w)" ] && echo "$out" | grep -q "건너뜀" && [ -d "$SB/review-batch.lock" ] && ok "잠금: 겹친 실행은 건너뛰고 남의 잠금을 지우지 않음" || ko "잠금" "$out"
echo "통과 $PASS · 실패 $FAIL"; [ "$FAIL" = 0 ]
