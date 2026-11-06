#!/usr/bin/env bash
# 설정 저장소 설치기. links.tsv 에 적힌 항목만 $HOME 아래에 링크·병합·복사하고 git 설정 4개 키를 맞춘다.
# 목록 밖 경로는 건드리지 않음. 덮어쓸 파일은 백업 폴더로 옮긴 뒤 처리하며 --restore 로 되돌림.
set -euo pipefail

REPO=$(cd "$(dirname "$0")" && pwd -P)
LINKS="$REPO/links.tsv"
GITCFG="$REPO/gitconfig.tsv"
BACKUP_ROOT="${CLAUDE_CONFIG_BACKUP_ROOT:-$HOME/.claude-config-backup}"
export GIT_CONFIG_GLOBAL="${GIT_CONFIG_GLOBAL:-$HOME/.gitconfig}"

usage() {
  cat <<'E'
사용: install.sh [--dry-run | --check | --restore <백업폴더>] [--reload-launchd]

  (옵션 없음)        links.tsv 항목 설치. 이미 같은 상태면 건너뜀(반복 실행 안전)
  --dry-run          바뀔 내용만 출력. 파일을 만들거나 바꾸지 않음
  --check            끊어진 링크·어긋난 설정 점검. 문제가 있으면 종료 코드 1
  --restore <폴더>   그 설치 때 만든 링크·병합 결과를 지우고 백업 원본 복원
  --reload-launchd   plist 가 바뀌면 launchctl 로 재적재(기본은 명령만 안내)

환경: HOME 아래만 처리. CLAUDE_CONFIG_BACKUP_ROOT 로 백업 위치 변경(기본 ~/.claude-config-backup).
예시: ./install.sh --dry-run && ./install.sh && ./install.sh --check
      ./install.sh --restore ~/.claude-config-backup/20261106-153000
E
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

DRY=0 ACTION=install RESTORE_DIR="" RELOAD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --check) ACTION=check; shift ;;
    --restore) ACTION=restore; RESTORE_DIR="${2:-}"; [ -n "$RESTORE_DIR" ] || die "--restore 에 백업 폴더 경로가 필요함"; shift 2 ;;
    --reload-launchd) RELOAD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "알 수 없는 옵션: $1" ;;
  esac
done

[ -n "${HOME:-}" ] && [ -d "$HOME" ] || die "HOME 이 비었거나 폴더가 아님"
[ -f "$LINKS" ] || die "links.tsv 없음: $LINKS"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/claude-config.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

BACKUP=""        # 첫 변경 때 만듦. 변경이 없으면 폴더도 없음
N_LINK=0 N_MERGE=0 N_COPY=0 N_SKIP=0

log() { printf '%s\n' "$*"; }
say() { if [ "$DRY" = 1 ]; then log "[dry-run] $*"; else log "$*"; fi; }
entries() { grep -v '^[[:space:]]*#' "$LINKS" | grep -v '^[[:space:]]*$' || true; }

# 백업 폴더 지연 생성. 같은 초에 두 번 실행해도 겹치지 않게 접미사를 붙임
ensure_backup() {
  [ -n "$BACKUP" ] && return 0
  local base n=0
  base="$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)"
  BACKUP="$base"
  while ! mkdir -p "$BACKUP_ROOT" || ! mkdir "$BACKUP" 2>/dev/null; do
    n=$((n + 1)); BACKUP="$base-$n"
    [ "$n" -lt 100 ] || die "백업 폴더를 만들 수 없음: $BACKUP_ROOT, 쓰기 권한 확인"
  done
  mkdir "$BACKUP/files"
  : > "$BACKUP/backup.tsv"
}

record() { printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}" >> "$BACKUP/backup.tsv"; }

# 대상을 백업으로 이동. 실패하면 링크를 만들지 않고 중단(기존 폴더 안에 링크가 생기는 사고 방지)
backup_move() {
  local dst="$1" t="$HOME/$1" stored
  ensure_backup
  stored="$BACKUP/files/$(($(wc -l < "$BACKUP/backup.tsv") + 1))"
  mv "$t" "$stored" || die "백업 이동 실패: $t, 아무것도 링크하지 않고 중단함. 권한·디스크 확인"
  record move "$dst" "$stored"
}

mark_new() { ensure_backup; record new "$1"; }

# 대상이 있으면 백업, 없으면 새로 만든 항목으로 기록
prepare_target() {
  local dst="$1" t="$HOME/$1"
  mkdir -p "$(dirname "$t")"
  if [ -L "$t" ] || [ -e "$t" ]; then backup_move "$dst"; else mark_new "$dst"; fi
}

same_file() { [ -f "$2" ] && [ ! -L "$2" ] && cmp -s "$1" "$2"; }

# __HOME__ 자리표시를 현재 HOME 으로 치환한 설정 기본값을 stdout 으로 출력
render_base() {
  jq --arg h "$HOME" 'walk(if type == "string" then gsub("__HOME__"; $h) else . end)' "$1"
}

# 병합 결과를 $2 에 생성. 현재 파일이 있으면 그 안의 Orca 항목을 얹음
build_merge() {
  local src="$1" out="$2" cur="$HOME/$3"
  render_base "$src" > "$TMP/base.json" || die "설정 기본값 JSON 오류: $src"
  if [ -f "$cur" ]; then
    jq -e . "$cur" >/dev/null 2>&1 || die "현재 설정이 비었거나 JSON 이 아님: $cur, 파일을 고친 뒤 다시 실행"
    jq -f "$REPO/merge/orca-keep.jq" "$cur" > "$TMP/keep.json" || die "현재 설정 JSON 오류: $cur, 파일을 고친 뒤 다시 실행"
  else
    echo '{}' > "$TMP/keep.json"
  fi
  jq -s -f "$REPO/merge/merge.jq" "$TMP/base.json" "$TMP/keep.json" > "$out" || die "설정 병합 실패: $src"
}

hook_count() { jq '[.hooks[]?[]] | length' "$1"; }

do_link() {
  local src="$1" dst="$2" s="$REPO/$1" t="$HOME/$2"
  if [ -L "$t" ] && [ "$(readlink "$t")" = "$s" ]; then
    N_SKIP=$((N_SKIP + 1)); say "SKIP   $dst (이미 링크됨)"; return
  fi
  if [ "$DRY" = 1 ]; then
    if [ -L "$t" ] || [ -e "$t" ]; then log "[dry-run] BACKUP+LINK $dst"; else log "[dry-run] LINK   $dst"; fi
    N_LINK=$((N_LINK + 1)); return
  fi
  prepare_target "$dst"
  ln -s "$s" "$t"
  N_LINK=$((N_LINK + 1)); log "LINK   $dst"
}

do_merge() {
  local src="$1" dst="$2" t="$HOME/$2" out="$TMP/merged.json"
  build_merge "$REPO/$src" "$out" "$dst"
  if same_file "$out" "$t"; then
    N_SKIP=$((N_SKIP + 1)); say "SKIP   $dst (병합 결과 동일, 훅 $(hook_count "$out")개)"; return
  fi
  N_MERGE=$((N_MERGE + 1))
  if [ "$DRY" = 1 ]; then log "[dry-run] MERGE  $dst (훅 $(hook_count "$out")개)"; return; fi
  prepare_target "$dst"
  cp "$out" "$t"
  log "MERGE  $dst (훅 $(hook_count "$out")개)"
}

do_copy() {
  local src="$1" dst="$2" t="$HOME/$2" out="$TMP/copied" content
  content=$(< "$REPO/$src")
  printf '%s\n' "${content//__HOME__/$HOME}" > "$out"
  if same_file "$out" "$t"; then
    N_SKIP=$((N_SKIP + 1)); say "SKIP   $dst (내용 동일)"; return
  fi
  N_COPY=$((N_COPY + 1))
  if [ "$DRY" = 1 ]; then log "[dry-run] COPY   $dst"; return; fi
  prepare_target "$dst"
  cp "$out" "$t"
  log "COPY   $dst"
  reload_hint "$t"
}

# launchd 재적재는 부작용이 커서 플래그가 있을 때만 실행
reload_hint() {
  local plist="$1" label
  case "$plist" in *.plist) ;; *) return 0 ;; esac
  label=$(basename "$plist" .plist)
  if [ "$RELOAD" = 1 ]; then
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist" || die "launchd 적재 실패: $plist"
    log "       launchd 재적재 완료: $label"
  else
    log "       launchd 재적재 필요: launchctl bootout gui/$(id -u)/$label; launchctl bootstrap gui/$(id -u) $plist"
  fi
}

do_seed() {
  local src="$1" dst="$2" t="$HOME/$2"
  if [ -L "$t" ] || [ -e "$t" ]; then
    N_SKIP=$((N_SKIP + 1)); say "SKIP   $dst (이미 있음, 로컬 값 유지)"; return
  fi
  N_COPY=$((N_COPY + 1))
  if [ "$DRY" = 1 ]; then log "[dry-run] SEED   $dst"; return; fi
  mkdir_new "$dst"
  cp "$REPO/$src" "$t"
  log "SEED   $dst"
}

mkdir_new() { mkdir -p "$(dirname "$HOME/$1")"; mark_new "$1"; }

# 실행 전 검증. 하나라도 틀리면 아무것도 바꾸지 않고 중단
validate() {
  local bad=0 src dst mode
  command -v jq >/dev/null || die "jq 가 필요함: brew install jq"
  while IFS=$'\t' read -r src dst mode; do
    case "$mode" in link|merge|copy|seed) ;; *) log "목록 오류: mode '$mode' ($src)" >&2; bad=1; continue ;; esac
    case "$dst" in /*|*..*) log "목록 오류: dst 는 HOME 기준 상대경로여야 함 ($dst)" >&2; bad=1 ;; esac
    [ -e "$REPO/$src" ] || { log "목록 오류: 원본 없음 $src" >&2; bad=1; }
  done < <(entries)
  [ "$bad" = 0 ] || die "links.tsv 검증 실패. 위 항목을 고친 뒤 다시 실행"
}

apply_git_config() {
  command -v git >/dev/null || { log "SKIP   git 없음, git 설정 건너뜀"; return 0; }
  local key val cur backed=0
  while IFS=$'\t' read -r key val; do
    cur=$(git config --global --get "$key" || true)
    if [ "$cur" = "$val" ]; then N_SKIP=$((N_SKIP + 1)); say "SKIP   git config $key (이미 $val)"; continue; fi
    if [ "$DRY" = 1 ]; then log "[dry-run] GITCFG $key=$val (현재: ${cur:-없음})"; continue; fi
    if [ "$backed" = 0 ]; then
      ensure_backup
      if [ -f "$GIT_CONFIG_GLOBAL" ]; then
        cp -p "$GIT_CONFIG_GLOBAL" "$BACKUP/gitconfig"; record copy "${GIT_CONFIG_GLOBAL#"$HOME"/}" "$BACKUP/gitconfig"
      else
        record new "${GIT_CONFIG_GLOBAL#"$HOME"/}"
      fi
      backed=1
    fi
    git config --global "$key" "$val"
    log "GITCFG $key=$val"
  done < <(grep -v '^#' "$GITCFG" | grep -v '^[[:space:]]*$')
}

do_install() {
  validate
  local src dst mode
  while IFS=$'\t' read -r src dst mode; do
    case "$mode" in
      link) do_link "$src" "$dst" ;;
      merge) do_merge "$src" "$dst" ;;
      copy) do_copy "$src" "$dst" ;;
      seed) do_seed "$src" "$dst" ;;
    esac
  done < <(entries)
  apply_git_config
  log "---"
  log "요약: 링크 $N_LINK, 병합 $N_MERGE, 복사 $N_COPY, 건너뜀 $N_SKIP"
  if [ -n "$BACKUP" ]; then log "백업: $BACKUP (되돌리기: ./install.sh --restore $BACKUP)"; fi
}

# 점검 전용. 문제 수를 종료 코드로 반환
do_check() {
  validate
  local src dst mode t bad=0 key val cur
  while IFS=$'\t' read -r src dst mode; do
    t="$HOME/$dst"
    case "$mode" in
      link)
        if [ ! -L "$t" ]; then log "NOT-LINK $dst"; bad=$((bad + 1))
        elif [ "$(readlink "$t")" != "$REPO/$src" ]; then log "OTHER-LINK $dst -> $(readlink "$t")"; bad=$((bad + 1))
        elif [ ! -e "$t" ]; then log "BROKEN $dst"; bad=$((bad + 1))
        fi ;;
      merge)
        build_merge "$REPO/$src" "$TMP/merged.json" "$dst"
        same_file "$TMP/merged.json" "$t" || { log "DRIFT  $dst (병합 결과와 다름)"; bad=$((bad + 1)); } ;;
      copy)
        local content; content=$(< "$REPO/$src"); printf '%s\n' "${content//__HOME__/$HOME}" > "$TMP/copied"
        same_file "$TMP/copied" "$t" || { log "DRIFT  $dst (복사본과 다름)"; bad=$((bad + 1)); } ;;
      seed)
        [ -e "$t" ] || { log "MISSING $dst"; bad=$((bad + 1)); } ;;
    esac
  done < <(entries)
  while IFS=$'\t' read -r key val; do
    cur=$(git config --global --get "$key" || true)
    [ "$cur" = "$val" ] || { log "DRIFT  git config $key (현재 '${cur:-없음}', 기대 '$val')"; bad=$((bad + 1)); }
  done < <(grep -v '^#' "$GITCFG" | grep -v '^[[:space:]]*$')
  if [ "$bad" = 0 ]; then log "점검 통과: 문제 없음"; else log "점검 실패: $bad 건"; return 1; fi
}

do_restore() {
  local dir="$RESTORE_DIR" kind dst stored t
  [ -f "$dir/backup.tsv" ] || die "백업 폴더가 아님: $dir (backup.tsv 없음)"
  while IFS=$'\t' read -r kind dst stored; do
    t="$HOME/$dst"
    if [ -L "$t" ] || [ -f "$t" ]; then
      [ "$DRY" = 1 ] && { log "[dry-run] REMOVE $dst"; } || rm -f "$t"
    elif [ -e "$t" ]; then
      die "복원 중단: $t 가 폴더라 지우지 않음. 직접 확인 후 다시 실행"
    fi
    case "$kind" in
      move|copy)
        if [ "$DRY" = 1 ]; then log "[dry-run] RESTORE $dst"; continue; fi
        mkdir -p "$(dirname "$t")"
        if [ "$kind" = move ]; then mv "$stored" "$t"; else cp -p "$stored" "$t"; fi
        log "RESTORE $dst" ;;
      new) [ "$DRY" = 1 ] || log "REMOVE  $dst (이 설치가 새로 만든 항목)" ;;
    esac
  done < "$dir/backup.tsv"
}

case "$ACTION" in
  install) do_install ;;
  check) do_check ;;
  restore) do_restore ;;
esac
