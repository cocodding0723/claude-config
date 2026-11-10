#!/bin/bash
# 쓰임이 끝난 Orca 워크스페이스 자동 정리기. 기본은 --dry-run(후보·건너뜀 이유만 기록), 실삭제는 --apply 로만.
# 판정·절차·되돌리기는 ~/.config/lrm-hubs/docs/ws-gc.md(원본 hubs/docs/ws-gc.md).
set -uo pipefail
# launchd 의 짧은 PATH 에서도 orca·gh·jq 를 찾도록 뒤에 덧붙임(앞의 PATH 가 우선: 시험의 가짜 명령)
export PATH="$PATH:/Applications/Orca.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin"

# ── 설정(값은 이 블록 한 곳. WS_GC_* 환경 변수나 옵션으로 덮어씀) ─────────────────────────────
CFG_ROOT="${WS_GC_ROOT:-$HOME/orca/workspaces}"          # <저장소>/<워크스페이스>/ 두 단계 아래가 대상
CFG_ARCHIVE="${WS_GC_ARCHIVE:-$HOME/orca/archive}"       # 결과물 보관·로그·잠금 위치
CFG_LOG="${WS_GC_LOG:-$CFG_ARCHIVE/ws_gc.log}"
CFG_MIN_IDLE_DAYS="${WS_GC_MIN_IDLE_DAYS:-2}"            # 마지막 활동 후 이 일수 이상 지나야 후보
CFG_HUB_PREFIX="coord-"                                  # 허브 폴더 접두사(항상 제외)
CFG_PROTECTED_BRANCHES="main master develop dev"         # 이 브랜치(와 release/*)가 붙은 곳은 PR 이력이 있어도 제외
# Unity 재임포트 부산물: 미커밋 0 으로 치는 경로. .meta 와 TMP 폰트 아틀라스
CFG_NOISE_RE='(\.meta$)|(^|/)TextMesh Pro/|SDF[^/]*\.asset$'
# 사운드 라이선스 파일은 줄바꿈 변경뿐일 때만 부산물로 침(role-config 의 LICENSE.txt 예외)
CFG_LICENSE_RE='^Assets/(.*/)?Sound/.*\.LICENSE\.txt$'
# 삭제 전 보관할 gitignore 결과물(삭제 뒤 복구 불가라 먼저 복제)
CFG_ARCHIVE_RE='(^|/)TestResults(/|$)|^docs/out(/|$)|\.html$|\.mp4$'
CFG_NOISE_LOG_MAX=20                                     # 부산물 근거로 로그에 남길 경로 수
CFG_LOG_MAX_BYTES=2097152                                # 로그가 이 크기를 넘으면 .1 로 돌림
CFG_MAX_PAGES=50                                         # worker-list 쪽 수 상한
CFG_NOTIFY="${WS_GC_NOTIFY:-$HOME/.config/lrm-hubs/slack_notify.sh}"
CFG_SLACK_TAG="ws-gc"
# ───────────────────────────────────────────────────────────────────────────────────────────

usage() {
  cat <<'EOF'
사용: ws_gc.sh [--dry-run | --apply] [--days N] [--only 부분경로] [--no-slack] [--quiet] [--json]
              [--root 폴더] [--archive-dir 폴더] [--log 파일]
쓰임이 끝난 Orca 워크스페이스(<root>/<저장소>/<폴더>)를 찾아 지움. 기본은 --dry-run 이라 아무것도 지우지 않음.

대상에서 빼는 곳: coord-* 허브, 메인 체크아웃, 살아 있는 터미널·워커가 붙은 곳, 하위 워크스페이스가 남은 곳,
  main·master·develop·dev·release/* 브랜치, Orca 에 등록되지 않은 폴더, 지금 이 명령을 실행 중인 폴더.
지우는 조건(전부 충족):
  1 브랜치 PR 이 MERGED·CLOSED(OPEN 이 하나라도 있으면 제외), 또는 PR 이 없고 커밋이 전부 origin 에 있음(detached 포함)
  2 미커밋 변경 0. Unity 재임포트 부산물(.meta·TMP 폰트 아틀라스, 줄바꿈만 바뀐 사운드 LICENSE.txt)은 0 으로 치고 목록을 로그에 남김
  3 마지막 활동(Orca 활동 시각·마지막 커밋 중 늦은 것)이 --days 일 이상 전
  4 서브모듈 안에 미커밋·미push 가 없음
절차(--apply): 결과물(TestResults·docs/out·*.html·*.mp4)을 <archive>/<저장소>/<폴더>/ 로 복제 → 파일 수 대조
  → orca worktree rm(브랜치는 지우지 않음) → 쉬고 있는 워커 기록 worker-release.
  서브모듈 때문에 rm 이 거부되면 4번을 통과한 곳만 --force 로 다시 시도함.

옵션:
  --dry-run        (기본) 후보·건너뜀 이유만 기록
  --apply          실제로 보관·삭제
  --days N         활동 없는 최소 일수(기본 2, WS_GC_MIN_IDLE_DAYS)
  --only 부분경로  경로에 이 글자가 든 곳만 평가(시험·부분 점검용)
  --no-slack       Slack 요약 생략
  --quiet          화면 출력 생략(로그 파일에만 기록. launchd 용)
  --json           화면에는 요약 JSON 만 출력(상세는 stderr·로그)
  --root·--archive-dir·--log  위치 덮어쓰기(WS_GC_ROOT·WS_GC_ARCHIVE·WS_GC_LOG)
로그: 기본 ~/orca/archive/ws_gc.log (한 줄에 시각·모드·판정·이유·경로). 반복 실행해도 안전함(이미 지운 곳은 대상이 아님).
Slack: 끝에 progress 요약 1줄(slack_notify.sh --kind progress). 후보·삭제가 없으면 보내지 않음.
종료 코드: 0 정상(건너뜀 포함), 1 삭제 단계 실패 있음, 2 인자 오류, 3 환경 오류(orca·gh·jq 없음, orca 응답 없음 → 판정 불가라 아무것도 안 지움)
예:
  ws_gc.sh                         # 시험 점검(삭제 없음)
  ws_gc.sh --only LRM-318          # 한 곳만 점검
  ws_gc.sh --apply --days 3        # 실삭제(3일 이상 쉰 곳)
EOF
}
die() { echo "$1" >&2; exit "${2:-2}"; }

APPLY=0 DAYS="$CFG_MIN_IDLE_DAYS" ONLY="" SLACK=1 QUIET=0 JSON=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --dry-run) APPLY=0; shift ;;
    --apply) APPLY=1; shift ;;
    --days) [ $# -ge 2 ] || die "--days 값 필요 — 예 --days 2"; DAYS="$2"; shift 2 ;;
    --only) [ $# -ge 2 ] || die "--only 값 필요 — 경로에 든 글자"; ONLY="$2"; shift 2 ;;
    --root) [ $# -ge 2 ] || die "--root 값 필요 — 폴더 경로"; CFG_ROOT="$2"; shift 2 ;;
    --archive-dir) [ $# -ge 2 ] || die "--archive-dir 값 필요 — 폴더 경로"; CFG_ARCHIVE="$2"; CFG_LOG="${WS_GC_LOG:-$2/ws_gc.log}"; shift 2 ;;
    --log) [ $# -ge 2 ] || die "--log 값 필요 — 파일 경로"; CFG_LOG="$2"; shift 2 ;;
    --no-slack) SLACK=0; shift ;;
    --quiet) QUIET=1; shift ;;
    --json) JSON=1; shift ;;
    *) die "알 수 없는 인자: $1 — --help 참고" ;;
  esac
done
case "$DAYS" in ''|*[!0-9]*) die "--days 는 0 이상 정수 — 받은 값: $DAYS" ;; esac
for c in jq git orca gh; do command -v "$c" >/dev/null 2>&1 || die "$c 없음 — 설치 필요(brew install $c). 판정 불가라 아무것도 지우지 않음" 3; done
[ -d "$CFG_ROOT" ] || die "워크스페이스 루트 없음: $CFG_ROOT — --root 로 지정" 3

MODE=$([ "$APPLY" = 1 ] && echo APPLY || echo DRY)
mkdir -p "$CFG_ARCHIVE" || die "보관 폴더 생성 실패: $CFG_ARCHIVE — 권한 확인" 3
mkdir -p "$(dirname "$CFG_LOG")" || die "로그 폴더 생성 실패: $CFG_LOG" 3
[ -f "$CFG_LOG" ] && [ "$(wc -c < "$CFG_LOG")" -gt "$CFG_LOG_MAX_BYTES" ] && mv -f "$CFG_LOG" "$CFG_LOG.1"

# 동시 실행 방지(launchd 회차가 겹칠 때). 죽은 pid 의 잠금은 치움
LOCK="$CFG_ARCHIVE/.ws_gc.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if kill -0 "$(cat "$LOCK/pid" 2>/dev/null)" 2>/dev/null; then echo "이미 실행 중 — 이번 회차 건너뜀"; exit 0; fi
  rm -rf "${LOCK:?}"; mkdir "$LOCK" 2>/dev/null || die "잠금 실패: $LOCK" 3
fi
echo $$ > "$LOCK/pid"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/ws_gc.XXXXXX") || die "임시 폴더 생성 실패" 3
trap 'rm -rf "${TMP:?}" "${LOCK:?}"' EXIT

NOW=$(date +%s)
log() { # log 판정 메시지
  local line; line="$(date '+%Y-%m-%dT%H:%M:%S') [$MODE] $*"
  printf '%s\n' "$line" >> "$CFG_LOG"
  if [ "$QUIET" = 0 ]; then if [ "$JSON" = 1 ]; then printf '%s\n' "$line" >&2; else printf '%s\n' "$line"; fi; fi
}
human() { awk -v k="$1" 'BEGIN{ if (k>=1048576) printf "%.1fGB",k/1048576; else if (k>=1024) printf "%.0fMB",k/1024; else printf "%dKB",k }'; }
count_files() { find "$1" \( -type f -o -type l \) 2>/dev/null | wc -l | tr -d ' '; }

# ── 원료 수집: 하나라도 못 읽으면 살아 있는 곳을 가려낼 수 없으므로 중단 ──────────────────────────
WL=$(orca worktree list --limit 1000 --json 2>/dev/null) && echo "$WL" | jq -e '.ok' >/dev/null 2>&1 || die "orca worktree list 실패 — Orca 실행 여부 확인(orca status). 아무것도 지우지 않음" 3
TL=$(orca terminal list --limit 1000 --json 2>/dev/null) && echo "$TL" | jq -e '.ok' >/dev/null 2>&1 || die "orca terminal list 실패 — Orca 실행 여부 확인(orca status). 아무것도 지우지 않음" 3
: > "$TMP/workers.tsv"
cursor="" page=0
while :; do
  if [ -n "$cursor" ]; then out=$(orca orchestration worker-list --full --limit 100 --cursor "$cursor" --json 2>/dev/null); else out=$(orca orchestration worker-list --full --limit 100 --json 2>/dev/null); fi
  echo "$out" | jq -e '.ok' >/dev/null 2>&1 || die "orca orchestration worker-list 실패 — Orca 실행 여부 확인. 아무것도 지우지 않음" 3
  echo "$out" | jq -r '.result.workers[] | [.dispatchId, .terminalState, (.agentTerminalHandle // ""), ((.resource.worktreeId // "") | sub("^.*::"; ""))] | @tsv' >> "$TMP/workers.tsv"
  [ "$(echo "$out" | jq -r '.result.page.hasMore // false')" = true ] || break
  cursor=$(echo "$out" | jq -r '.result.page.nextCursor // empty'); page=$((page + 1))
  [ -n "$cursor" ] && [ "$page" -lt "$CFG_MAX_PAGES" ] || die "worker-list 쪽 수 상한($CFG_MAX_PAGES) 초과 — 판정 불가라 중단" 3
done
echo "$TL" | jq -r '.result.terminals[] | select(.connected == true and (.orphaned != true)) | [.handle, .worktreePath] | @tsv' > "$TMP/terms.tsv"
# 살아 있는 곳 = 연결된 터미널이 붙은 곳 + 쉬는 중(reclaimable)이 아닌 워커가 붙은 곳. 쉬는 워커의 터미널은 제외(정리 뒤 worker-release)
awk -F'\t' 'NR==FNR { if ($2 == "reclaimable") idle[$3] = 1; else if ($2 != "released") print $4; next } !($1 in idle) { print $2 }' "$TMP/workers.tsv" "$TMP/terms.tsv" | sort -u > "$TMP/live"

echo "$WL" | jq -r --arg root "$CFG_ROOT/" '
  [.result.worktrees[].id] as $ids
  | .result.worktrees[]
  | select((.isMainWorktree | not) and (.path | startswith($root)))
  | [.path, (.lastActivityAt // 0), ((.childWorktreeIds // []) | map(select(. as $c | $ids | index($c))) | length)] | @tsv' > "$TMP/rows.tsv"
echo "$WL" | jq -r '.result.worktrees[].path' | sort -u > "$TMP/registered"

# ── 판정 ──────────────────────────────────────────────────────────────────────────────────
REASON="" DETAIL="" PR_LABEL="" N_NOISE=0 N_LIC=0
# 미커밋 분류: 진짜 변경 수 → N_REAL, 부산물 → $TMP/noise.txt (근거 로그용)
classify_changes() {
  local p="$1" k path
  : > "$TMP/noise.txt"; N_REAL=0 N_NOISE=0 N_LIC=0
  git -C "$p" status --porcelain=v1 -z --untracked-files=all --ignore-submodules=all 2>/dev/null | tr '\0' '\n' \
    | NZ="$CFG_NOISE_RE" LZ="$CFG_LICENSE_RE" awk '
        skip { skip = 0; next }
        { xy = substr($0, 1, 2); q = substr($0, 4); if (xy ~ /^[RC]/ || xy ~ /^.[RC]/) skip = 1
          if (q ~ ENVIRON["NZ"]) print "N\t" q; else if (q ~ ENVIRON["LZ"]) print "L\t" q; else print "R\t" q }' > "$TMP/chg.tsv"
  while IFS=$'\t' read -r k path; do
    case "$k" in
      N) N_NOISE=$((N_NOISE + 1)); printf '부산물 %s\n' "$path" >> "$TMP/noise.txt" ;;
      L) if git -C "$p" diff HEAD --ignore-all-space --ignore-cr-at-eol --quiet -- "$path" 2>/dev/null; then
           N_LIC=$((N_LIC + 1)); printf '줄바꿈만 %s\n' "$path" >> "$TMP/noise.txt"
         else N_REAL=$((N_REAL + 1)); fi ;;
      *) N_REAL=$((N_REAL + 1)) ;;
    esac
  done < "$TMP/chg.tsv"
}
# 서브모듈 안에 미커밋·미push 가 없으면 0. 서브모듈이 없어도 0
submodules_clean() {
  local p="$1" line sp
  [ -f "$p/.gitmodules" ] || return 0
  while IFS= read -r line; do
    case "$line" in -*) continue ;; esac            # 초기화 안 된 서브모듈은 작업물이 없음
    sp="$p/$(printf '%s' "$line" | sed -E 's/^.[0-9a-f]+ //; s/ \(.*\)$//')"
    [ -n "$(git -C "$sp" status --porcelain 2>/dev/null)" ] && return 1
    [ "$(git -C "$sp" rev-list --count HEAD --branches --not --remotes 2>/dev/null || echo 1)" = 0 ] || return 1
  done < <(git -C "$p" submodule status --recursive 2>/dev/null)
  return 0
}
protected_branch() { local b="$1" x; for x in $CFG_PROTECTED_BRANCHES; do [ "$b" = "$x" ] && return 0; done; case "$b" in release/*) return 0 ;; esac; return 1; }

# evaluate 경로 최근활동ms 살아있는하위수 → 0 후보, 1 건너뜀(REASON·DETAIL)
evaluate() {
  local p="$1" last_ms="$2" kids="$3" br last_c last age prs
  REASON="" DETAIL="" PR_LABEL=""
  case "$(basename "$p")" in "$CFG_HUB_PREFIX"*) REASON=hub; return 1 ;; esac
  [ -e "$p/.git" ] || { REASON=missing; return 1; }
  case "$PWD/" in "$p"/*) REASON=current; return 1 ;; esac
  [ "$kids" -gt 0 ] && { REASON=children; DETAIL="하위 $kids"; return 1; }
  grep -Fxq -- "$p" "$TMP/live" && { REASON=live; return 1; }
  br=$(git -C "$p" symbolic-ref -q --short HEAD 2>/dev/null) || br=""
  protected_branch "$br" && { REASON=protected; DETAIL="$br"; return 1; }
  last_c=$(git -C "$p" log -1 --format=%ct 2>/dev/null || echo 0)
  last=$((last_ms / 1000)); [ "$last_c" -gt "$last" ] && last="$last_c"
  age=$(( (NOW - last) / 86400 ))
  if [ "$age" -lt "$DAYS" ]; then REASON=idle; DETAIL="${age}일 전 활동"; return 1; fi
  classify_changes "$p"
  if [ "$N_REAL" -gt 0 ]; then REASON=dirty; DETAIL="미커밋 ${N_REAL}건"; return 1; fi
  if [ -n "$br" ]; then
    prs=$(cd "$p" && gh pr list --head "$br" --state all --json number,state --limit 30 2>/dev/null) && echo "$prs" | jq -e 'type == "array"' >/dev/null 2>&1 \
      || { REASON=gh_fail; DETAIL="gh pr list 실패(로그인·네트워크 확인)"; return 1; }
    PR_LABEL=$(echo "$prs" | jq -r 'map("#\(.number) \(.state)") | join(",")')
    if echo "$prs" | jq -e 'any(.[]; .state == "OPEN")' >/dev/null; then REASON=pr_open; DETAIL="$PR_LABEL"; return 1; fi
  fi
  if [ -z "$PR_LABEL" ] && [ "$(git -C "$p" rev-list --count HEAD --not --remotes 2>/dev/null || echo 1)" != 0 ]; then
    REASON=unmerged; DETAIL="PR 없음, origin 에 없는 커밋 있음"; return 1
  fi
  submodules_clean "$p" || { REASON=submodule; DETAIL="서브모듈 안 미커밋·미push"; return 1; }
  DETAIL="idle=${age}d branch=${br:-detached} pr=${PR_LABEL:--}"
  return 0
}

# ── 삭제 절차 ─────────────────────────────────────────────────────────────────────────────
# 결과물 보관: 복제 → 파일 수 대조. 실패하면 1(삭제하지 않음)
archive_outputs() {
  local p="$1" dest="$2" rel src n_src n_dst total=0
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    rel="${rel%/}"; src="$p/$rel"; [ -e "$src" ] || continue
    mkdir -p "$dest/$(dirname "$rel")" || return 1
    cp -cR "$src" "$dest/$(dirname "$rel")/" 2>/dev/null || cp -R "$src" "$dest/$(dirname "$rel")/" || return 1
    n_src=$(count_files "$src"); n_dst=$(count_files "$dest/$rel")
    [ "$n_src" = "$n_dst" ] || { log "ARCHIVE_MISMATCH $rel 원본 $n_src 복제 $n_dst"; return 1; }
    total=$((total + n_src)); log "ARCHIVE $rel 파일 $n_src → $dest/$rel"
  done < <(git -C "$p" ls-files -o -i --exclude-standard --directory -z 2>/dev/null | tr '\0' '\n' | ARZ="$CFG_ARCHIVE_RE" awk '$0 ~ ENVIRON["ARZ"]')
  ARCHIVED_FILES=$total
  return 0
}
# 보관 대상 미리보기(dry-run 용): 개수만
preview_outputs() { git -C "$1" ls-files -o -i --exclude-standard --directory -z 2>/dev/null | tr '\0' '\n' | ARZ="$CFG_ARCHIVE_RE" awk '$0 ~ ENVIRON["ARZ"]' | sed 's#/$##' | tr '\n' ',' | sed 's/,$//'; }

remove_ws() { # 0 성공. 서브모듈 거부 때만 --force 재시도
  local p="$1" out
  out=$(orca worktree rm --worktree "path:$p" --json 2>&1) && return 0
  if [ -f "$p/.gitmodules" ] && submodules_clean "$p"; then
    log "RM_RETRY_FORCE 서브모듈 때문에 거부됨, 서브모듈 안 미커밋·미push 0 확인 → --force"
    orca worktree rm --worktree "path:$p" --force --json >/dev/null 2>&1 && return 0
  fi
  log "RM_FAIL $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-200)"
  return 1
}
release_workers() { # 이 워크스페이스에 붙었던 쉬는 워커 기록 정리(기록 정리 실패는 삭제 결과에 영향 없음)
  local p="$1" d st h wp
  while IFS=$'\t' read -r d st h wp; do
    [ "$wp" = "$p" ] && [ "$st" = reclaimable ] || continue
    if orca orchestration worker-release --dispatch "$d" --json >/dev/null 2>&1; then log "WORKER_RELEASE $d"; else log "WORKER_RELEASE_FAIL $d (워커 기록은 Orca 가 자동 해제)"; fi
  done < "$TMP/workers.tsv"
}

# ── 본 흐름 ───────────────────────────────────────────────────────────────────────────────
: > "$TMP/skips"; N_CAND=0 N_DEL=0 N_FAIL=0 FREED_KB=0 CAND_KB=0
log "START root=$CFG_ROOT days=$DAYS only=${ONLY:--}"

# 등록되지 않은 폴더(Orca 밖)는 건드리지 않고 사유만 셈
for d in "$CFG_ROOT"/*/*/; do
  d="${d%/}"; [ -d "$d" ] || continue
  { [ -e "$d/.git" ] && ! grep -Fxq -- "$d" "$TMP/registered"; } && { echo not_orca >> "$TMP/skips"; log "SKIP not_orca ${d#"$CFG_ROOT"/}"; }
done

while IFS=$'\t' read -r p last_ms kids; do
  [ -n "$p" ] || continue
  [ -z "$ONLY" ] || case "$p" in *"$ONLY"*) ;; *) continue ;; esac
  name="${p#"$CFG_ROOT"/}"
  if ! evaluate "$p" "$last_ms" "$kids"; then
    echo "$REASON" >> "$TMP/skips"; log "SKIP $REASON $name ${DETAIL:+— $DETAIL}"
    [ "$REASON" = dirty ] && [ "$N_NOISE$N_LIC" != 00 ] && log "NOISE $name 부산물 ${N_NOISE}건·줄바꿈만 ${N_LIC}건은 제외하고 셈"
    continue
  fi
  N_CAND=$((N_CAND + 1))
  size_kb=$(du -sk "$p" 2>/dev/null | cut -f1); size_kb="${size_kb:-0}"; CAND_KB=$((CAND_KB + size_kb))
  if [ "$N_NOISE$N_LIC" != 00 ]; then
    log "NOISE $name 미커밋 0 으로 친 부산물 ${N_NOISE}건·줄바꿈만 ${N_LIC}건"
    head -n "$CFG_NOISE_LOG_MAX" "$TMP/noise.txt" | while IFS= read -r l; do log "NOISE   $l"; done
    extra=$(( $(wc -l < "$TMP/noise.txt" | tr -d ' ') - CFG_NOISE_LOG_MAX )); [ "$extra" -gt 0 ] && log "NOISE   …외 ${extra}건"
  fi
  if [ "$APPLY" = 0 ]; then
    log "CANDIDATE $name $DETAIL size=$(human "$size_kb") 보관예정=[$(preview_outputs "$p")]"
    continue
  fi
  dest="$CFG_ARCHIVE/$(dirname "$name")/$(basename "$name")"
  ARCHIVED_FILES=0
  if ! archive_outputs "$p" "$dest"; then echo archive_fail >> "$TMP/skips"; N_FAIL=$((N_FAIL + 1)); log "SKIP archive_fail $name — 보관 복제·대조 실패, 삭제 안 함(디스크 여유 확인)"; continue; fi
  if remove_ws "$p"; then
    N_DEL=$((N_DEL + 1)); FREED_KB=$((FREED_KB + size_kb))
    log "DELETE $name $DETAIL size=$(human "$size_kb") 보관파일=$ARCHIVED_FILES (브랜치는 유지)"
    release_workers "$p"
  else
    echo rm_fail >> "$TMP/skips"; N_FAIL=$((N_FAIL + 1)); log "SKIP rm_fail $name — orca worktree rm 거부, 보관본은 남음"
  fi
done < "$TMP/rows.tsv"

# ── 요약 ──────────────────────────────────────────────────────────────────────────────────
label() { case "$1" in
  hub) echo 허브 ;; current) echo 현재작업 ;; children) echo 하위있음 ;; live) echo 터미널·워커 ;; idle) echo 최근활동 ;; dirty) echo 미커밋 ;;
  pr_open) echo PR열림 ;; unmerged) echo 미병합 ;; gh_fail) echo PR조회실패 ;; protected) echo 보호브랜치 ;; submodule) echo 서브모듈 ;;
  not_orca) echo 미등록 ;; missing) echo 폴더없음 ;; archive_fail) echo 보관실패 ;; rm_fail) echo 삭제실패 ;; *) echo "$1" ;; esac; }
SKIP_TXT=$(sort "$TMP/skips" | uniq -c | while read -r n r; do printf '%s %s·' "$(label "$r")" "$n"; done | sed 's/·$//')
N_SKIP=$(wc -l < "$TMP/skips" | tr -d ' ')
if [ "$APPLY" = 1 ]; then RESULT="삭제 ${N_DEL}개·줄인 용량 $(human "$FREED_KB")"; else RESULT="삭제 후보 ${N_CAND}개·예상 용량 $(human "$CAND_KB")(시험, 삭제 없음)"; fi
log "END $RESULT 건너뜀 ${N_SKIP}곳: ${SKIP_TXT:-없음}"

if [ "$JSON" = 1 ]; then
  jq -n --arg mode "$MODE" --argjson cand "$N_CAND" --argjson del "$N_DEL" --argjson fail "$N_FAIL" --argjson freed "$FREED_KB" --argjson candkb "$CAND_KB" \
    --argjson skips "$(sort "$TMP/skips" | uniq -c | awk '{printf "%s\"%s\":%d", (n++ ? "," : "{"), $2, $1} END{print n ? "}" : "{}"}')" \
    '{mode:$mode, candidates:$cand, deleted:$del, failed:$fail, freed_kb:$freed, candidate_kb:$candkb, skipped:$skips}'
fi

# Slack: 양식 A(첫 줄 주제 → 빈 줄 → • 결과). 보낼 내용이 없으면 생략. 전송 실패는 정리 결과에 영향 없음
if [ "$SLACK" = 1 ] && { [ "$N_CAND" -gt 0 ] || [ "$N_DEL" -gt 0 ] || [ "$N_FAIL" -gt 0 ]; }; then
  TITLE=$([ "$APPLY" = 1 ] && echo "워크스페이스 자동 정리 완료" || echo "워크스페이스 정리 시험 점검")
  MSG="$TITLE"$'\n\n'"• 결과: $RESULT, 건너뜀 ${N_SKIP}곳(${SKIP_TXT:-없음})"
  if [ -x "$CFG_NOTIFY" ] || command -v "$CFG_NOTIFY" >/dev/null 2>&1; then
    "$CFG_NOTIFY" --tag "$CFG_SLACK_TAG" --kind progress "$MSG" >/dev/null 2>&1 && log "SLACK sent" || log "SLACK_FAIL 전송 실패(토큰·네트워크 확인). 정리 결과는 유효"
  else log "SLACK_SKIP 전송 스크립트 없음: $CFG_NOTIFY"; fi
fi
[ "$N_FAIL" -eq 0 ] || exit 1
exit 0
