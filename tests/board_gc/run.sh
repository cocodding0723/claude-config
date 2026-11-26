#!/usr/bin/env bash
# board_gc.sh 시험. 가짜 bd·가짜 Slack 전송만 씀. 실제 작업판·Slack 은 건드리지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
GC="$HERE/../../hubs/scripts/board_gc.sh"
PASS=0 FAIL=0
chk() { if eval "$2"; then PASS=$((PASS + 1)); echo "PASS  $1"; else FAIL=$((FAIL + 1)); echo "FAIL  $1"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-boardgc.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
# 가짜 bd: 호출 기록. export 는 FAKE_N 줄(gc 뒤엔 FAKE_N_AFTER), gc 는 FAKE_GC_RC 로 종료
cat > "$SB/bd" <<'EOS'
#!/bin/bash
echo "$*" >> "$FAKE_DIR/calls"
case "$1" in
  export) o=""; p=""; for a in "$@"; do [ "$p" = -o ] && o="$a"; p="$a"; done
          n=$FAKE_N; [ -f "$FAKE_DIR/gc_done" ] && n=${FAKE_N_AFTER:-$FAKE_N}
          [ "$n" -lt 0 ] && exit 1
          : > "$o"; for ((i=0;i<n;i++)); do echo "{}" >> "$o"; done ;;
  gc) touch "$FAKE_DIR/gc_done"; exit "${FAKE_GC_RC:-0}" ;;
esac
EOS
printf '#!/bin/bash\necho "$*" >> "%s/notified"\n' "$SB" > "$SB/notify"
chmod +x "$SB/bd" "$SB/notify"

setup() {  # 새 작업판·백업 폴더
  rm -rf "$SB/board" "$SB/bk" "$SB/calls" "$SB/notified" "$SB/gc_done" "$SB/log"
  mkdir -p "$SB/board/.beads/embeddeddolt" "$SB/bk"; echo x > "$SB/board/.beads/embeddeddolt/data"
}
run() {
  FAKE_DIR="$SB" BOARD_GC_BD="$SB/bd" BOARD_GC_BOARD="$SB/board" BOARD_GC_BACKUP_ROOT="$SB/bk" \
    BOARD_GC_NOTIFY="$SB/notify" BOARD_GC_LOG="$SB/log" "$GC" "$@" >/dev/null 2>&1
}

setup; for d in 20260101-000000 20260102-000000 20260103-000000; do mkdir "$SB/bk/$d-board-gc"; done; mkdir "$SB/bk/20250101-000000-pre-gc"
FAKE_N=5 run; rc=$?
chk "정상: 종료 0" '[ $rc = 0 ]'
chk "정상: --skip-decay 로 gc" 'grep -qx "gc --skip-decay --force" "$SB/calls"'
chk "정상: 자동 백업 3개만 남김" '[ $(ls -d "$SB"/bk/*-board-gc | wc -l) = 3 ] && [ ! -e "$SB/bk/20260101-000000-board-gc" ]'
chk "정상: 수동 백업 보존" '[ -d "$SB/bk/20250101-000000-pre-gc" ]'
chk "정상: 새 백업에 데이터" 'ls "$SB"/bk/2026*-board-gc/.beads/embeddeddolt/data | grep -qv 2026010'
chk "정상: 알림 없음" '[ ! -e "$SB/notified" ]'
chk "정상: 잠금 해제" '[ ! -e "$SB/board/.board-gc.lock" ]'

setup; FAKE_N=-1 run; rc=$?
chk "백업 확인 실패: 종료 3" '[ $rc = 3 ]'
chk "백업 확인 실패: gc 안 함" '! grep -q "^gc" "$SB/calls"'
chk "백업 확인 실패: 경고" 'grep -q "fail-3" "$SB/notified"'

setup; FAKE_N=0 run; rc=$?
chk "카드 0장: gc 안 함" '[ $rc = 3 ] && ! grep -q "^gc" "$SB/calls"'

setup; FAKE_N=5 FAKE_GC_RC=1 run; rc=$?
chk "gc 실패: 종료 5·경고" '[ $rc = 5 ] && grep -q "fail-5" "$SB/notified"'
chk "gc 실패: 백업 남김" '[ $(ls -d "$SB"/bk/*-board-gc | wc -l) = 1 ]'

setup; FAKE_N=5 FAKE_N_AFTER=4 run; rc=$?
chk "카드 감소: 종료 6·경고" '[ $rc = 6 ] && grep -q "fail-6" "$SB/notified"'

setup; FAKE_N=5 FAKE_N_AFTER=7 run; rc=$?
chk "정리 중 카드 증가: 정상" '[ $rc = 0 ]'

setup; mkdir "$SB/board/.board-gc.lock"; FAKE_N=5 run; rc=$?
chk "잠금 보유: 종료 4·아무것도 안 함" '[ $rc = 4 ] && [ ! -e "$SB/calls" ] && [ -d "$SB/board/.board-gc.lock" ]'

setup; FAKE_N=5 run --dry-run; rc=$?
chk "--dry-run: 백업만" '[ $rc = 0 ] && ! grep -q "^gc" "$SB/calls" && [ $(ls -d "$SB"/bk/*-board-gc | wc -l) = 1 ]'

setup; run --nope; rc=$?
chk "알 수 없는 인자: 종료 2" '[ $rc = 2 ]'

echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
