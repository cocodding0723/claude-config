#!/usr/bin/env bash
# 옛 문서 스킬 이름 → doc-kit 치환. 대상 목록(rename-targets.tsv)·규칙(rename-rules.tsv)은 이 폴더에 있다.
# --dry-run 은 파일을 바꾸지 않고 바뀔 줄만 파일:줄로 출력한다(내용은 출력하지 않음).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/.." && pwd -P)
TARGETS="$HERE/rename-targets.tsv"
RULES="$HERE/rename-rules.tsv"
MEMORY_DIR="${DOC_KIT_MEMORY_DIR:-$HOME/.claude/projects/-Users-$(id -un)-orca-LoveRevolutionMaker/memory}"
BACKUP_ROOT="${CLAUDE_CONFIG_BACKUP_ROOT:-$HOME/.claude-config-backup}"
MODE="" REVERT_DIR=""

usage() {
  cat <<'E'
사용: rename_skills.sh --dry-run | --apply | --revert <백업폴더> [옵션]

  --dry-run          바뀔 줄을 파일:줄로만 출력. 아무것도 쓰지 않음
  --apply            백업 후 치환. 백업 폴더 경로를 마지막에 출력
  --revert <폴더>    그 백업의 치환 결과를 원본으로 되돌림(치환 뒤 파일이 다시 바뀌었으면 그 파일은 건너뛰고 알림)
옵션:
  --targets <파일>   대상 목록(기본 migrate/rename-targets.tsv)
  --rules <파일>     치환 규칙(기본 migrate/rename-rules.tsv)
  --memory-dir <폴더> 메모리 폴더(기본 ~/.claude/projects/-Users-<사용자>-orca-LoveRevolutionMaker/memory)
  --backup-root <폴더> 백업 위치(기본 ~/.claude-config-backup)

출력 표식: CHANGE 파일:줄 / REVIEW 파일:줄(치환 뒤 doc-kit 경로가 실재하지 않음, 직접 확인) / MISSING 파일
종료 코드: 0 성공, 1 REVIEW·MISSING 있음(dry-run), 2 인자·사전 조건 오류
예: rename_skills.sh --dry-run
E
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) MODE=dry; shift ;;
    --apply) MODE=apply; shift ;;
    --revert) MODE=revert; REVERT_DIR="${2:-}"; [ -n "$REVERT_DIR" ] || die "--revert 에 백업 폴더가 필요함"; shift 2 ;;
    --targets) TARGETS="${2:-}"; shift 2 ;;
    --rules) RULES="${2:-}"; shift 2 ;;
    --memory-dir) MEMORY_DIR="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "알 수 없는 인자: $1" ;;
  esac
done
[ -n "$MODE" ] || { usage >&2; die "--dry-run, --apply, --revert 중 하나가 필요함"; }
command -v perl >/dev/null || die "perl 필요"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/rename-skills.XXXXXX")
trap 'rm -rf "${TMP:?}"' EXIT

# 대상 한 줄의 실제 경로. 기준 repo|mem
resolve() {
  case "$1" in
    repo) printf '%s/%s' "$REPO" "$2" ;;
    mem) printf '%s/%s' "$MEMORY_DIR" "$2" ;;
    *) die "대상 목록의 기준이 repo|mem 이 아님: $1" ;;
  esac
}

# 표시용 이름(값 없이 위치만). repo 는 저장소 상대, mem 은 mem/ 접두
label() { if [ "$1" = repo ]; then printf '%s' "$2"; else printf 'mem/%s' "$2"; fi; }

entries() { grep -v '^[[:space:]]*#' "$TARGETS" | grep -v '^[[:space:]]*$' || true; }

# perl 실패를 빈 출력으로 착각해 전 줄 CHANGE 로 내지 않도록 즉시 중단(조건문 안에서 불려도 die 는 종료함)
transform() { perl "$HERE/rename_apply.pl" "$RULES" < "$1" > "$2" || die "치환 규칙 적용 실패: $1 (규칙: $RULES)"; }

# 치환 뒤 파일에서 doc-kit 경로가 실재하지 않는 줄을 REVIEW 로 출력. 개수를 반환값 대신 전역에 누적
# 경로 접두 없이 옛 참고 파일 이름만 남은 줄(예: "같은 폴더 house-style.md")도 doc-kit/references 에 있는지 본다
review_paths() {
  local new="$1" lab="$2" ln p
  while IFS=: read -r ln p; do
    [ -n "$ln" ] || continue
    [ -e "$REPO/skills/$p" ] || { printf 'REVIEW %s:%s\n' "$lab" "$ln"; N_REVIEW=$((N_REVIEW + 1)); }
  done < <(grep -noE 'doc-kit/(references|scripts|templates)/[A-Za-z0-9_.-]+' "$new")
  while IFS=: read -r ln p; do
    [ -n "$ln" ] || continue
    [ -e "$REPO/skills/doc-kit/references/$p" ] || { printf 'REVIEW %s:%s\n' "$lab" "$ln"; N_REVIEW=$((N_REVIEW + 1)); }
  done < <(grep -noE '(^|[^/A-Za-z0-9_.-])(house-style|sources|md-spec)\.md' "$new" | sed -E 's/^([0-9]+):.*(house-style|sources|md-spec)\.md$/\1:\2.md/')
}

# 원본과 치환본의 달라진 줄 번호를 출력
changed_lines() {
  local old="$1" new="$2" lab="$3"
  awk -v lab="$lab" 'NR==FNR { a[FNR]=$0; next } { if (a[FNR] != $0) printf "CHANGE %s:%d\n", lab, FNR }' "$old" "$new"
}

N_REVIEW=0 N_MISSING=0 N_CHANGED=0

scan_target() {
  local base="$1" rel="$2" path lab out
  path=$(resolve "$base" "$rel"); lab=$(label "$base" "$rel")
  if [ ! -f "$path" ]; then printf 'MISSING %s\n' "$lab"; N_MISSING=$((N_MISSING + 1)); return 1; fi
  out="$TMP/new.$$"
  transform "$path" "$out"
  if cmp -s "$path" "$out"; then return 1; fi
  changed_lines "$path" "$out" "$lab"
  review_paths "$out" "$lab"
  return 0
}

do_dry() {
  local base rel n=0
  while IFS=$'\t' read -r base rel; do
    if scan_target "$base" "$rel"; then n=$((n + 1)); fi
  done < <(entries)
  printf '요약: 바뀔 파일 %s, 확인 필요 %s, 없음 %s\n' "$n" "$N_REVIEW" "$N_MISSING"
  [ "$N_REVIEW" = 0 ] && [ "$N_MISSING" = 0 ]
}

do_apply() {
  local base rel path lab out stamp dir n=0
  stamp=$(date +%Y%m%d-%H%M%S)
  dir="$BACKUP_ROOT/rename-$stamp"
  # 치환 전에 대상 존재를 모두 확인해 일부만 바뀌는 상태를 막음
  while IFS=$'\t' read -r base rel; do
    [ -f "$(resolve "$base" "$rel")" ] || die "대상 없음: $(label "$base" "$rel"). 아무것도 바꾸지 않고 중단"
  done < <(entries)
  mkdir -p "$BACKUP_ROOT" || die "백업 위치를 만들 수 없음: $BACKUP_ROOT"
  mkdir "$dir" || die "백업 폴더를 만들 수 없음: $dir"
  : > "$dir/manifest.tsv"
  while IFS=$'\t' read -r base rel; do
    path=$(resolve "$base" "$rel"); lab=$(label "$base" "$rel"); out="$TMP/new.$$"
    transform "$path" "$out"
    cmp -s "$path" "$out" && continue
    n=$((n + 1))
    cp -p "$path" "$dir/$n.orig" || die "백업 복사 실패: $lab. 이 파일은 바꾸지 않고 중단"
    cp "$out" "$path.rename-tmp" && mv "$path.rename-tmp" "$path" || die "치환 쓰기 실패: $lab. 되돌리기: $0 --revert $dir"
    printf '%s\t%s\t%s\t%s\t%s\n' "$n" "$base" "$rel" "$(shasum "$dir/$n.orig" | cut -d' ' -f1)" "$(shasum "$path" | cut -d' ' -f1)" >> "$dir/manifest.tsv"
    printf 'APPLIED %s\n' "$lab"
  done < <(entries)
  printf '요약: 치환 %s파일\n백업: %s (되돌리기: %s --revert %s)\n' "$n" "$dir" "$0" "$dir"
}

do_revert() {
  local dir="$REVERT_DIR" n base rel before after path lab cur skipped=0 done_n=0
  [ -f "$dir/manifest.tsv" ] || die "백업 폴더가 아님: $dir (manifest.tsv 없음)"
  # 백업 원본이 하나라도 없으면 아무것도 바꾸지 않음
  while IFS=$'\t' read -r n base rel before after; do
    [ -f "$dir/$n.orig" ] || die "백업 원본 없음: $dir/$n.orig. 아무것도 바꾸지 않음"
  done < "$dir/manifest.tsv"
  while IFS=$'\t' read -r n base rel before after; do
    path=$(resolve "$base" "$rel"); lab=$(label "$base" "$rel")
    cur=$(shasum "$path" 2>/dev/null | cut -d' ' -f1 || true)
    if [ "$cur" = "$before" ]; then printf 'SKIP %s (이미 원본)\n' "$lab"; continue; fi
    if [ "$cur" != "$after" ]; then printf 'KEEP %s (치환 뒤 다시 바뀌어 건너뜀, 직접 확인)\n' "$lab"; skipped=$((skipped + 1)); continue; fi
    cp -p "$dir/$n.orig" "$path.rename-tmp" && mv "$path.rename-tmp" "$path" || die "복원 실패: $lab"
    done_n=$((done_n + 1)); printf 'RESTORED %s\n' "$lab"
  done < "$dir/manifest.tsv"
  printf '요약: 복원 %s, 건너뜀 %s\n' "$done_n" "$skipped"
  [ "$skipped" = 0 ]
}

case "$MODE" in
  dry) do_dry ;;
  apply) do_apply ;;
  revert) do_revert ;;
esac
