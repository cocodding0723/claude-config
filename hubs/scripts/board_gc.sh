#!/bin/bash
# 작업판 DB 하루 1회 정리 진입점. launchd(ai.lrm.boardgc)가 새벽에 호출.
# 이유: 내장 Dolt 저장소가 쓰기마다 찌꺼기를 남겨 9일 만에 742MB 로 커졌고, 모든 bd 명령이 느려져 잠금 대기열이 20개 넘게 쌓임(11-25).
#       bd gc 로 280MB 로 줄었고 bd show 처리 CPU 가 약 1.7초→0.2초. 이력(커밋)은 지우지 않음 — bd history 를 지표가 씀.
# 순서: 잠금 → .beads 복제 백업 → 백업 사본에서 카드 수 확인 → bd gc --skip-decay → 카드 수 비교 → 오래된 백업 정리.
# 종료 코드: 0 정상 · 2 인자 오류 · 3 백업 실패·확인 불가(정리 안 함) · 4 다른 실행이 잠금 보유 · 5 bd gc 실패 · 6 정리 뒤 카드 수 감소
set -euo pipefail
# 설정(값은 이 블록 한 곳). 환경 변수는 시험용 재정의
CFG_BOARD="${BOARD_GC_BOARD:-$HOME/orca/board}"
CFG_BACKUP_ROOT="${BOARD_GC_BACKUP_ROOT:-$HOME/orca/board-backup}"
CFG_KEEP="${BOARD_GC_KEEP:-3}"                 # 남길 자동 백업 수(복제라 바뀐 블록만 디스크를 씀)
CFG_GC_TIMEOUT="${BOARD_GC_TIMEOUT:-1800}"     # bd gc 제한(초). 잠금 대기 포함, 11-25 실측 4분
CFG_SUFFIX="board-gc"                          # 자동 백업 폴더 접미사. 이 이름만 정리 대상(수동 백업 보호)
CFG_NOTIFY="${BOARD_GC_NOTIFY:-$HOME/.config/lrm-hubs/slack_notify.sh}"
CFG_LOG="${BOARD_GC_LOG:-$HOME/.config/lrm-hubs/board-gc.log}"
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"  # launchd 짧은 PATH 에서도 bd·timeout 찾기
BD="${BOARD_GC_BD:-bd}"

usage() {
  cat <<'EOF'
사용: board_gc.sh [--dry-run] | --help
  작업판(~/orca/board/.beads)을 백업한 뒤 bd gc --skip-decay 로 저장소 찌꺼기를 지움. 카드·이력은 남김.
  --dry-run  백업·확인만 하고 정리는 안 함(백업 폴더는 남음)
실패하면 Slack 경고 1줄(slack_notify.sh --kind warn, 하루 1회). 기록: ~/.config/lrm-hubs/board-gc.log
되돌리기: 작업판을 쓰는 프로그램을 멈춘 뒤 ~/orca/board-backup/<시각>-board-gc/.beads 를 ~/orca/board/.beads 로 복사
EOF
}
DRY=0
while [ $# -gt 0 ]; do case "$1" in
  --dry-run) DRY=1; shift ;; -h|--help) usage; exit 0 ;;
  *) echo "알 수 없는 인자: $1 — board_gc.sh --help 참고" >&2; exit 2 ;; esac; done

mkdir -p "$(dirname "$CFG_LOG")"
log() { printf '%s\t%s\n' "$(date +%FT%T%z)" "$*" >> "$CFG_LOG"; printf '%s\n' "$*"; }
fail() {  # 인자: 종료 코드, 원인. Slack 경고 뒤 종료
  log "FAIL rc=$1 $2"
  [ -x "$CFG_NOTIFY" ] && "$CFG_NOTIFY" --tag board-gc --kind warn --from board_gc --state "fail-$1" \
    $'작업판 DB 정리 실패\n원인: '"$2"$'\n조치: ~/.config/lrm-hubs/board-gc.log 확인 뒤 board_gc.sh 수동 실행' >/dev/null 2>&1 || log "SLACK_FAIL 경고 전송 실패"
  exit "$1"
}
count() {  # 인자: .beads 를 담은 폴더. 내보낸 카드 수. 실패 시 빈 값
  local out; out=$(mktemp "${TMPDIR:-/tmp}/board-gc.XXXXXX")
  if (cd "$1" && "$BD" export --all -o "$out" >/dev/null 2>&1); then wc -l < "$out" | tr -d ' '; fi
  rm -f "$out"
}

[ -d "$CFG_BOARD/.beads" ] || fail 3 "작업판 폴더 없음: $CFG_BOARD/.beads"
LOCK="$CFG_BOARD/.board-gc.lock"
mkdir "$LOCK" 2>/dev/null || { log "SKIP 다른 실행이 잠금 보유: $LOCK"; exit 4; }
trap 'rm -rf "$LOCK" "${CHECK:-}"' EXIT

# 백업: APFS 복제(cp -c)라 즉시 끝나고 바뀐 블록만 공간을 씀
mkdir -p "$CFG_BACKUP_ROOT"; chmod 700 "$CFG_BACKUP_ROOT"
DEST="$CFG_BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)-$CFG_SUFFIX"
mkdir "$DEST" && cp -Rc "$CFG_BOARD/.beads" "$DEST/.beads" || fail 3 "백업 복제 실패: $DEST"
# 확인은 백업의 사본에서. bd 가 상태 파일을 쓰므로 백업 자체는 건드리지 않음
CHECK=$(mktemp -d "${TMPDIR:-/tmp}/board-gc-check.XXXXXX")
cp -Rc "$DEST/.beads" "$CHECK/.beads" && chmod 700 "$CHECK/.beads" || fail 3 "확인용 사본 실패"
BEFORE=$(count "$CHECK")
[ -n "$BEFORE" ] && [ "$BEFORE" -gt 0 ] || fail 3 "백업에서 카드를 읽지 못함 — 정리 안 함: $DEST"
log "BACKUP $DEST cards=$BEFORE"

if [ "$DRY" = 1 ]; then log "DRY 정리 생략"; exit 0; fi

SIZE0=$(du -sm "$CFG_BOARD/.beads/embeddeddolt" 2>/dev/null | cut -f1 || true)
(cd "$CFG_BOARD" && timeout "$CFG_GC_TIMEOUT" "$BD" gc --skip-decay --force >> "$CFG_LOG" 2>&1) || fail 5 "bd gc 실패 또는 ${CFG_GC_TIMEOUT}초 초과. 백업: $DEST"
SIZE1=$(du -sm "$CFG_BOARD/.beads/embeddeddolt" 2>/dev/null | cut -f1 || true)
AFTER=$(count "$CFG_BOARD")
# 정리 중에도 다른 세션이 카드를 만들 수 있어 '같거나 많음'이 정상
[ -n "$AFTER" ] && [ "$AFTER" -ge "$BEFORE" ] || fail 6 "정리 뒤 카드 수 ${BEFORE}→${AFTER:-읽기 실패}. 백업: $DEST"
log "OK cards=${BEFORE}→${AFTER} size=${SIZE0}MB→${SIZE1}MB"

# 자동 백업만 최신 CFG_KEEP 개 남김(이름이 시각순)
ls -1d "$CFG_BACKUP_ROOT"/*-"$CFG_SUFFIX" 2>/dev/null | sort -r | tail -n +$((CFG_KEEP + 1)) | while IFS= read -r old; do
  rm -rf "$old" && log "PRUNE $old"
done
