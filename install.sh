#!/usr/bin/env bash
# 설정 저장소 설치기. links.tsv 에 적힌 항목만 $HOME 아래에 링크·병합·복사하고 git 설정 4개 키를 맞춘다.
# 목록 밖 경로는 건드리지 않음. 덮어쓸 파일은 백업 폴더로 옮긴 뒤 처리하며 --restore 로 되돌림.
set -euo pipefail
# bash 5.2+ 는 치환 문자열의 &(매치 문자열)을 확장함. 값에 &가 있어도 그대로 쓰도록 끔(3.2 는 해당 옵션 없음)
shopt -u patsub_replacement 2>/dev/null || true

REPO=$(cd "$(dirname "$0")" && pwd -P)
LINKS="$REPO/links.tsv"
GITCFG="$REPO/gitconfig.tsv"
LIVE_DIFF_RULES="$REPO/live-diff.tsv"
VALUES_EXAMPLE="$REPO/values.env.example"
VALUES="${CLAUDE_CONFIG_VALUES:-$HOME/.config/claude-config/values.env}"
BACKUP_ROOT="${CLAUDE_CONFIG_BACKUP_ROOT:-$HOME/.claude-config-backup}"
export GIT_CONFIG_GLOBAL="${GIT_CONFIG_GLOBAL:-$HOME/.gitconfig}"

usage() {
  cat <<'E'
사용: install.sh [--dry-run | --check | --diff-live | --restore <백업폴더>] [--reload-launchd] [--allow-live-diff]

  (옵션 없음)        links.tsv 항목 설치. 이미 같은 상태면 건너뜀(반복 실행 안전).
                     라이브가 저장소보다 앞선 항목이 있으면 변경 전에 중단
  --dry-run          바뀔 내용만 출력. 파일을 만들거나 바꾸지 않음
  --check            끊어진 링크·어긋난 설정 점검. 문제가 있으면 종료 코드 1
  --diff-live        라이브 파일이 저장소 원본과 다른(라이브가 더 최신인) 항목만 출력. 있으면 종료 코드 1
  --allow-live-diff  라이브가 앞선 항목이 있어도 설치를 진행(백업 후 저장소 내용으로 덮어씀)
  --restore <폴더>   그 설치 때 만든 링크·병합 결과를 지우고 백업 원본 복원
  --reload-launchd   plist 가 바뀌면 launchctl 로 재적재(기본은 명령만 안내)

render 항목(실명·채널 ID 가 든 파일)은 로컬 값 파일로 자리표시를 채워 복사한다.
값 파일: ~/.config/claude-config/values.env (CLAUDE_CONFIG_VALUES 로 변경). 양식은 values.env.example.

환경: HOME 아래만 처리. CLAUDE_CONFIG_BACKUP_ROOT 로 백업 위치 변경(기본 ~/.claude-config-backup).
예시: ./install.sh --dry-run && ./install.sh && ./install.sh --check
      ./install.sh --restore ~/.claude-config-backup/20261106-153000
E
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

DRY=0 ACTION=install RESTORE_DIR="" RELOAD=0 ALLOW_LIVE_DIFF="${CLAUDE_CONFIG_ALLOW_LIVE_DIFF:-0}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --check) ACTION=check; shift ;;
    --restore) ACTION=restore; RESTORE_DIR="${2:-}"; [ -n "$RESTORE_DIR" ] || die "--restore 에 백업 폴더 경로가 필요함"; shift 2 ;;
    --diff-live) ACTION=difflive; shift ;;
    --allow-live-diff) ALLOW_LIVE_DIFF=1; shift ;;
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

# 상위 폴더를 만들고 새로 만든 폴더만 기록(복원 때 비어 있으면 제거)
make_parent() {
  local list="" d
  d=$(dirname "$HOME/$1")
  while [ ! -d "$d" ]; do list="$d"$'\n'"$list"; d=$(dirname "$d"); done
  [ -n "$list" ] || return 0
  ensure_backup
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    mkdir "$d"; record newdir "${d#"$HOME"/}"
  done <<< "$list"
}

# 대상이 있으면 백업, 없으면 새로 만든 항목으로 기록
prepare_target() {
  local dst="$1" t="$HOME/$1"
  make_parent "$dst"
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

# launchd 처럼 PATH 가 짧은 곳에서 쓸 python3 절대 경로. 설치하는 셸의 python3 기준
PYTHON3="${CLAUDE_CONFIG_PYTHON3:-$(command -v python3 || true)}"

# 파일을 바이트 그대로 읽어 __HOME__·__PYTHON3__ 과 값 파일의 __KEY__ 를 치환해 $2 로 씀(끝 개행 유지)
subst_file() {
  local src="$1" out="$2" content k v
  content=$(cat "$src"; printf x); content=${content%x}
  content=${content//__HOME__/$HOME}
  if [[ $content == *__PYTHON3__* ]]; then
    [ -n "$PYTHON3" ] || die "python3 없음: $src 의 __PYTHON3__ 를 채울 수 없음. python3 설치 또는 CLAUDE_CONFIG_PYTHON3=<절대 경로>"
    content=${content//__PYTHON3__/$PYTHON3}
  fi
  if [ "${3:-}" = values ]; then
    while IFS='=' read -r k v; do
      case "$k" in ''|'#'*) continue ;; esac
      content=${content//"__${k}__"/$v}
    done < "$VALUES"
  fi
  printf '%s' "$content" > "$out"
}

# 값 파일에 values.env.example 의 모든 키가 채워져 있어야 함
check_values() {
  local k v missing=""
  [ -f "$VALUES" ] || die "값 파일 없음: $VALUES. values.env.example 을 복사해 채운 뒤 다시 실행 (권한 600 권장)"
  while IFS='=' read -r k _; do
    case "$k" in ''|'#'*) continue ;; esac
    v=$(grep -E "^${k}=" "$VALUES" | head -1 | cut -d= -f2- | tr -d '[:space:]')
    [ -n "$v" ] || missing="$missing $k"
  done < "$VALUES_EXAMPLE"
  [ -z "$missing" ] || die "값 파일에 빈 키:$missing ($VALUES)"
}

do_copy() {
  local src="$1" dst="$2" t="$HOME/$2" out="$TMP/copied"
  subst_file "$REPO/$src" "$out"
  if same_file "$out" "$t"; then
    N_SKIP=$((N_SKIP + 1)); say "SKIP   $dst (내용 동일)"; return
  fi
  N_COPY=$((N_COPY + 1))
  if [ "$DRY" = 1 ]; then log "[dry-run] COPY   $dst"; return; fi
  prepare_target "$dst"
  cp "$out" "$t"
  log "COPY   $dst"
  reload_hint "$dst"
}

do_render() {
  local src="$1" dst="$2" t="$HOME/$2" out="$TMP/rendered"
  subst_file "$REPO/$src" "$out" values
  if same_file "$out" "$t"; then
    N_SKIP=$((N_SKIP + 1)); say "SKIP   $dst (렌더 결과 동일)"; return
  fi
  N_COPY=$((N_COPY + 1))
  if [ "$DRY" = 1 ]; then log "[dry-run] RENDER $dst"; return; fi
  prepare_target "$dst"
  cp "$out" "$t"
  log "RENDER $dst"
}

# launchd 재적재는 부작용이 커서 플래그가 있을 때만 실행.
# LaunchAgents 밖에 둔 plist(예: Socket Mode 수신기)는 켜지 않는 보관본이라 재적재·안내 모두 하지 않음
reload_hint() {
  local plist="$HOME/$1" label
  case "$1" in Library/LaunchAgents/*.plist) ;; *) return 0 ;; esac
  label=$(basename "$plist" .plist)
  if [ "$RELOAD" = 1 ]; then
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist" || die "launchd 적재 실패: $plist"
    log "       launchd 재적재 완료: $label"
  else
    log "       launchd 재적재 필요: launchctl bootout gui/$(id -u)/$label; launchctl bootstrap gui/$(id -u) $plist"
  fi
}

# 실행 전 검증. 하나라도 틀리면 아무것도 바꾸지 않고 중단
validate() {
  local bad=0 src dst mode
  command -v jq >/dev/null || die "jq 가 필요함: brew install jq"
  while IFS=$'\t' read -r src dst mode; do
    case "$mode" in link|merge|copy|render) ;; *) log "목록 오류: mode '$mode' ($src)" >&2; bad=1; continue ;; esac
    case "$dst" in /*|*..*) log "목록 오류: dst 는 HOME 기준 상대경로여야 함 ($dst)" >&2; bad=1 ;; esac
    [ -e "$REPO/$src" ] || { log "목록 오류: 원본 없음 $src" >&2; bad=1; }
  done < <(entries)
  [ "$bad" = 0 ] || die "links.tsv 검증 실패. 위 항목을 고친 뒤 다시 실행"
  if entries | cut -f3 | grep -qx render; then check_values; fi
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

# 라이브가 저장소보다 앞선 항목 출력(내용만 비교, 값은 출력하지 않음). 의도된 경로 치환은 live-diff.tsv 규칙으로 흡수
live_differs() {
  local key="$1" r="$2" l="$3" expr=""
  [ -f "$LIVE_DIFF_RULES" ] && expr=$(awk -F'\t' -v k="$key" '$1 !~ /^#/ && $1 == k {print $2; exit}' "$LIVE_DIFF_RULES")
  if [ -n "$expr" ]; then ! perl -pe "$expr" "$l" | cmp -s - "$r"; else ! cmp -s "$r" "$l"; fi
}

live_diff() {
  local src dst mode t f rel n=0
  while IFS=$'\t' read -r src dst mode; do
    t="$HOME/$dst"
    case "$mode" in
      link)
        if [ -L "$t" ] && [ "$(readlink "$t")" = "$REPO/$src" ]; then continue; fi
        [ -e "$t" ] || continue
        if [ -d "$REPO/$src" ]; then
          [ -d "$t" ] || { log "LIVE-NEWER $dst (폴더가 아님)"; n=$((n + 1)); continue; }
          while IFS= read -r f; do
            rel=${f#"$REPO/$src/"}
            [ -e "$t/$rel" ] || continue
            if live_differs "$dst/$rel" "$f" "$t/$rel"; then log "LIVE-NEWER $dst/$rel"; n=$((n + 1)); fi
          done < <(find "$REPO/$src" -type f ! -name '*.bak*' ! -name .DS_Store ! -path '*__pycache__*')
          while IFS= read -r f; do
            rel=${f#"$t/"}
            [ -e "$REPO/$src/$rel" ] || { log "LIVE-ONLY  $dst/$rel (라이브에만 있음)"; n=$((n + 1)); }
          done < <(find "$t" -type f ! -name '*.bak*' ! -name .DS_Store ! -name '*.pyc' ! -path '*__pycache__*' ! -path '*/state/*')
        elif [ -f "$t" ] && live_differs "$dst" "$REPO/$src" "$t"; then
          log "LIVE-NEWER $dst"; n=$((n + 1))
        fi ;;
      copy)
        subst_file "$REPO/$src" "$TMP/ld"
        if [ -f "$t" ] && ! cmp -s "$TMP/ld" "$t"; then log "LIVE-NEWER $dst"; n=$((n + 1)); fi ;;
      render)
        subst_file "$REPO/$src" "$TMP/ld" values
        if [ -f "$t" ] && ! cmp -s "$TMP/ld" "$t"; then log "LIVE-NEWER $dst"; n=$((n + 1)); fi ;;
    esac
  done < <(entries)
  [ "$n" = 0 ] || return 1
}

# 변경 전 점검. 라이브가 앞서 있으면 저장소 내용으로 덮어쓰지 않도록 중단
guard_live_diff() {
  [ "$ALLOW_LIVE_DIFF" = 1 ] && return 0
  live_diff > "$TMP/live-diff.txt" || {
    cat "$TMP/live-diff.txt"
    die "라이브가 저장소보다 앞선 항목 $(wc -l < "$TMP/live-diff.txt" | tr -d ' ')건. 라이브 변경을 저장소로 옮긴 뒤 다시 실행. 백업 후 덮어써도 되면 --allow-live-diff"
  }
}

do_install() {
  validate
  guard_live_diff
  local src dst mode
  while IFS=$'\t' read -r src dst mode; do
    case "$mode" in
      link) do_link "$src" "$dst" ;;
      merge) do_merge "$src" "$dst" ;;
      copy) do_copy "$src" "$dst" ;;
      render) do_render "$src" "$dst" ;;
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
        subst_file "$REPO/$src" "$TMP/copied"
        same_file "$TMP/copied" "$t" || { log "DRIFT  $dst (복사본과 다름)"; bad=$((bad + 1)); } ;;
      render)
        subst_file "$REPO/$src" "$TMP/rendered" values
        same_file "$TMP/rendered" "$t" || { log "DRIFT  $dst (렌더 결과와 다름)"; bad=$((bad + 1)); } ;;
    esac
  done < <(entries)
  while IFS=$'\t' read -r key val; do
    cur=$(git config --global --get "$key" || true)
    [ "$cur" = "$val" ] || { log "DRIFT  git config $key (현재 '${cur:-없음}', 기대 '$val')"; bad=$((bad + 1)); }
  done < <(grep -v '^#' "$GITCFG" | grep -v '^[[:space:]]*$')
  if [ "$bad" = 0 ]; then log "점검 통과: 문제 없음"; else log "점검 실패: $bad 건"; return 1; fi
}

# 복원은 백업을 소모하지 않는다(cp). 백업 원본이 하나라도 없으면 아무것도 지우지 않고 중단하므로
# 두 번 실행하거나 중간에 실패한 뒤 다시 실행해도 안전함
do_restore() {
  local dir="$RESTORE_DIR" kind dst stored t
  [ -f "$dir/backup.tsv" ] || die "백업 폴더가 아님: $dir (backup.tsv 없음)"
  while IFS=$'\t' read -r kind dst stored; do
    case "$kind" in move|copy) [ -e "$stored" ] || [ -L "$stored" ] || die "복원 중단: 백업 원본 없음 $stored ($dst). 아무것도 지우지 않음" ;; esac
  done < "$dir/backup.tsv"
  while IFS=$'\t' read -r kind dst stored; do
    t="$HOME/$dst"
    case "$kind" in
      move|copy) restore_one "$dst" "$stored" ;;
      new)
        if [ -L "$t" ] || [ -f "$t" ]; then
          if [ "$DRY" = 1 ]; then log "[dry-run] REMOVE  $dst"; else rm -f "$t"; log "REMOVE  $dst (이 설치가 새로 만든 항목)"; fi
        fi ;;
    esac
  done < "$dir/backup.tsv"
  # 이 설치가 새로 만든 폴더는 비어 있을 때만 안쪽부터 제거
  while IFS= read -r dst; do
    [ -n "$dst" ] || continue
    if [ "$DRY" = 1 ]; then log "[dry-run] RMDIR   $dst (비어 있으면)"; continue; fi
    if [ -d "$HOME/$dst" ] && rmdir "$HOME/$dst" 2>/dev/null; then log "RMDIR   $dst"
    elif [ -d "$HOME/$dst" ]; then log "남김    $dst (비어 있지 않음)"; fi
  done < <(awk -F'\t' '$1=="newdir" {a[NR]=$2} END {for (i=NR; i>0; i--) if (a[i] != "") print a[i]}' "$dir/backup.tsv")
}

# 임시 이름으로 먼저 복사한 뒤 교체. 이미 같은 내용이면 건너뜀
restore_one() {
  local dst="$1" stored="$2" t="$HOME/$1" tmp="$HOME/$1.restore-tmp"
  if [ -L "$stored" ]; then
    if [ -L "$t" ] && [ "$(readlink "$t")" = "$(readlink "$stored")" ]; then log "SKIP    $dst (이미 복원됨)"; return; fi
  elif [ -d "$stored" ]; then
    if [ -d "$t" ] && [ ! -L "$t" ] && diff -rq "$stored" "$t" >/dev/null 2>&1; then log "SKIP    $dst (이미 복원됨)"; return; fi
  elif [ -f "$t" ] && [ ! -L "$t" ] && cmp -s "$stored" "$t"; then
    log "SKIP    $dst (이미 복원됨)"; return
  fi
  if [ -d "$t" ] && [ ! -L "$t" ]; then
    die "복원 중단: $t 가 백업과 다른 실제 폴더라 덮어쓰지 않음. 직접 확인 후 다시 실행"
  fi
  if [ "$DRY" = 1 ]; then log "[dry-run] RESTORE $dst"; return; fi
  mkdir -p "$(dirname "$t")"
  if [ -e "$tmp" ] || [ -L "$tmp" ]; then rm -rf "${tmp:?}"; fi
  cp -a "$stored" "$tmp" || die "복원 복사 실패: $stored, 대상은 그대로 둠"
  if [ -L "$t" ] || [ -f "$t" ]; then rm -f "$t"; fi
  mv "$tmp" "$t" || die "복원 교체 실패: $t, 임시본 $tmp 가 남아 있음. 다시 실행하면 이어서 복원"
  log "RESTORE $dst"
}

case "$ACTION" in
  install) do_install ;;
  difflive) validate; if live_diff; then log "라이브가 앞선 항목 없음"; else exit 1; fi ;;
  check) do_check ;;
  restore) do_restore ;;
esac
