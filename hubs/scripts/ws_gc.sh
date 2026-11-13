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
CFG_GAME_REPO_DIR="${WS_GC_GAME_REPO:-LoveRevolutionMaker}"  # 부모 자동 연결 대상 저장소 폴더(<root>/ 바로 아래)
CFG_TICKET_RE='LRM-[0-9]+'                               # 같은 티켓 판정에 쓰는 이름·브랜치·폴더 패턴
CFG_PROTECTED_BRANCHES="main master develop dev"         # 이 브랜치(와 release/*)가 붙은 곳은 PR 이력이 있어도 제외
# Unity 재임포트 부산물: 미커밋 0 으로 치는 경로. .meta 와 TMP 폰트 아틀라스 파일만(폴더 이름만으로는 부산물이 아님)
CFG_NOISE_RE='(\.meta$)|(SDF[^/]*\.asset$)'
# 사운드 라이선스 파일은 줄바꿈 변경뿐일 때만 부산물로 침(role-config 의 LICENSE.txt 예외)
CFG_LICENSE_RE='^Assets/(.*/)?Sound/.*\.LICENSE\.txt$'
# ── 결과물 허용 목록: 추적 안 되는(미추적·gitignore) 파일이 여기에 들 때만 보관 뒤 삭제. 목록 밖은 전부 "미확인"이라 작업 공간을 보존 ──
CFG_OUTPUT_DIRS_RE='^(docs/out|evidence|TestResults)/|/TestResults/'   # 결과물 폴더(대소문자 구분)
CFG_OUTPUT_ROOT_RE='^[^/]+\.(html|md|png|jpe?g|gif|mp4|txt|log)$'       # 작업 공간 루트의 결과물 파일(확장자 대소문자 무시)
# 게임 소스 폴더 아래는 허용 목록과 겹쳐도 항상 보존(새 에셋·코드일 수 있음)
CFG_SOURCE_DIR_RE='^(Assets|Packages|ProjectSettings)/'
# 다시 만들어지는 gitignore 캐시 후보(폴더는 끝에 /). 폴더 이름만으로 캐시라고 단정하지 않음: 아래 CFG_CACHE_GENERATED_RE 밖 파일이 안에 있으면 보존
CFG_CACHE_RE='^(Library|Temp|obj|Logs|UserSettings|\.vs|\.idea|node_modules)/$|(^|/)\.dart_tool/$|^\.flutter-plugins(-dependencies)?$|^\.claude/settings\.local\.json$|^\.DS_Store$'
# 캐시 폴더 안에서 "지워도 되는 것" 목록(도구가 다시 만드는 하위 경로). 캐시 폴더 안의 파일은 이 목록에 든 것만 버리고,
# 목록 밖은 확장자와 상관없이 전부 미확인으로 보존함(.kt·.mp4 같은 목록에 없는 파일도 보존)
CFG_CACHE_GENERATED_RE='^(Library/(PackageCache|Artifacts|Bee|ScriptAssemblies|ShaderCache|StateCache|BuildCache|PlayerDataCache|APIUpdater|Search|SourceAssetDB)/|node_modules/|(.*/)?\.dart_tool/|Temp/|Logs/|\.vs/|obj/(Debug|Release)/)|^Library/([^/.]+|[^/]+\.(asset|json|meta|txt|log))$|^obj/(project\.assets\.json|[^/]+\.nuget\.[^/]+)$'
CFG_INBOX="${WS_GC_INBOX:-$HOME/.config/lrm-hubs/hub-inbox}"  # 허브별 보관 색인(<허브 slug>.tsv). 허브 폴더 coord-<slug>-hub 에서 slug 를 뗌
CFG_KEEP_CODE_LOG_MAX=5                                  # 보존 근거로 로그·JSON 에 남길 경로 수
CFG_RM_DIRTY_RE='modified or untracked|contain(s|ing) submodules|uncommitted|dirty'  # orca rm 거부 문구 중 미커밋 변경 계열만 force 허용(잠금·사용 중 등은 제외)
# Orca 실측 거부 문구: "Failed to delete worktree at <경로>. M Assets/…" (마침표 뒤 git status 짧은 형식 'XY 경로'). 대소문자 구분(소문자 문장 오인 방지)
CFG_RM_DIRTY_STATUS_RE='\.[[:space:]]+[ MADRCTU?!]{1,2}[[:space:]]+[^[:space:]]'
CFG_NOISE_LOG_MAX=20                                     # 부산물 근거로 로그에 남길 경로 수
CFG_LOG_MAX_BYTES=2097152                                # 로그가 이 크기를 넘으면 .1 로 돌림
CFG_MAX_PAGES=50                                         # worker-list 쪽 수 상한
CFG_NOTIFY="${WS_GC_NOTIFY:-$HOME/.config/lrm-hubs/slack_notify.sh}"
CFG_SLACK_TAG="ws-gc"
# ───────────────────────────────────────────────────────────────────────────────────────────

usage() {
  cat <<'EOF'
사용: ws_gc.sh [--dry-run | --apply] [--days N] [--only 부분경로 | --path 경로 | --parent 허브경로] [--assume-idle id] [--no-slack] [--quiet] [--json]
              [--root 폴더] [--archive-dir 폴더] [--log 파일]
쓰임이 끝난 Orca 워크스페이스(<root>/<저장소>/<폴더>)를 찾아 지움. 기본은 --dry-run 이라 아무것도 지우지 않음.

대상에서 빼는 곳: coord-* 허브, 메인 체크아웃, 살아 있는 터미널·워커가 붙은 곳, 하위 워크스페이스가 남은 곳,
  main·master·develop·dev·release/* 브랜치, Orca 에 등록되지 않은 폴더, 지금 이 명령을 실행 중인 폴더.
지우는 조건(전부 충족):
  1 브랜치 PR 이 MERGED·CLOSED(OPEN 이 하나라도 있으면 제외)이고 현재 HEAD 가 origin 에 있음(원격 브랜치가 지워졌으면 병합된 PR 의 마지막 커밋과 같을 때). PR 이 없어도 HEAD 가 origin 에 있어야 함(detached 포함)
  2 미커밋 변경 0. Unity 재임포트 부산물(.meta·TMP 폰트 아틀라스, 줄바꿈만 바뀐 사운드 LICENSE.txt)은 0 으로 치고 목록을 로그에 남김.
    추적 안 되는 결과물만 있어도 후보: 허용 목록(docs/out·evidence·TestResults 폴더, 루트의 html·md·png·jpg·gif·mp4·txt·log)에 든 것만.
    추적 파일 수정, 허용 목록 밖 미추적·gitignore 파일, Assets·Packages·ProjectSettings 아래 파일이 하나라도 있으면 보존하고
    KEEP_CODE 로 기록(--json 의 keep_code)
  3 마지막 활동(Orca 활동 시각·마지막 커밋 중 늦은 것)이 --days 일 이상 전
  4 서브모듈 안에 미커밋·미push 가 없음
절차(--apply): 허용 목록 결과물(미추적·gitignore)을 <archive>/<저장소>/<폴더>/ 로 복제 → byte 단위 대조(불일치·복사 실패면 삭제 안 함)
  → orca worktree rm(브랜치는 지우지 않음) → 허브 색인 한 줄(~/.config/lrm-hubs/hub-inbox/<허브 slug>.tsv: 시각·작업 공간·보관 경로·파일 수)
  → 쉬고 있는 워커 기록 worker-release.
  rm 거부 시 서브모듈이 깨끗하거나 확인된 사운드 LICENSE 줄바꿈만 남은 곳은 보관·변경 재확인 후 --force 로 재시도함.

부모 없는 작업 공간 연결(전체 점검에서만): <root>/LoveRevolutionMaker/ 아래 허브가 아닌 작업 공간 중 부모가 없고 같은 티켓(LRM-n, 표시 이름·브랜치·폴더에서 찾음)의
  다른 작업 공간들이 가진 부모 허브가 하나로 정해지면 --apply 때 그 허브에 연결하고 경고 한 줄을 남김(--dry-run 은 ADOPT_CANDIDATE 기록만).
  티켓 근거가 없거나 허브가 갈리면 연결하지 않고 WARN_NO_PARENT·WARN_PARENT_SPLIT 경고만 남김.

옵션:
  --dry-run        (기본) 후보·건너뜀 이유만 기록
  --apply          실제로 보관·삭제
  --days N         활동 없는 최소 일수(기본 2, WS_GC_MIN_IDLE_DAYS)
  --only 부분경로  경로에 이 글자가 든 곳만 평가(시험·부분 점검용)
  --path 경로      이 경로와 정확히 같은 곳만 평가(release_ws.sh 용)
  --parent 허브경로 이 허브의 하위 작업 공간만 평가(create_worker_ws.sh 의 상한 정리용)
  --assume-idle id 이 dispatch 의 워커·터미널은 쉬는 중으로 침(release 전 시험 점검용)
  --no-slack       Slack 요약 생략
  --quiet          화면 출력 생략(로그 파일에만 기록. launchd 용)
  --json           화면에는 요약 JSON 만 출력(상세는 stderr·로그)
  --root·--archive-dir·--log  위치 덮어쓰기(WS_GC_ROOT·WS_GC_ARCHIVE·WS_GC_LOG). 색인 폴더는 WS_GC_INBOX
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

ORIG_ARGS=("$@")
APPLY=0 DAYS="$CFG_MIN_IDLE_DAYS" ONLY="" ONLYPATH="" PARENT="" ASSUME_IDLE="" SLACK=1 QUIET=0 JSON=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --dry-run) APPLY=0; shift ;;
    --apply) APPLY=1; shift ;;
    --days) [ $# -ge 2 ] || die "--days 값 필요 — 예 --days 2"; DAYS="$2"; shift 2 ;;
    --only) [ $# -ge 2 ] || die "--only 값 필요 — 경로에 든 글자"; ONLY="$2"; shift 2 ;;
    --path) [ $# -ge 2 ] || die "--path 값 필요 — 작업 공간 절대 경로"; ONLYPATH="$2"; shift 2 ;;
    --parent) [ $# -ge 2 ] || die "--parent 값 필요 — 허브 폴더 절대 경로"; PARENT="$2"; shift 2 ;;
    --assume-idle) [ $# -ge 2 ] || die "--assume-idle 값 필요 — 워커 dispatch id"; ASSUME_IDLE="$2"; shift 2 ;;
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

# 동시 실행 방지(launchd 회차·release_ws 가 겹칠 때). 파일 잠금(flock)을 쥔 채 자기 자신을 다시 실행함.
# 잠금은 프로세스가 죽으면 커널이 풀어 stale 판정·잠금 탈취가 없고, 잠금 파일은 지우지 않음(지우면 두 실행이 서로 다른 파일을 잠금)
if [ -z "${WS_GC_LOCKED:-}" ]; then
  command -v python3 >/dev/null 2>&1 || die "python3 없음 — 잠금에 필요. 아무것도 지우지 않음" 3
  export WS_GC_LOCKED=1
  exec python3 -c 'import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o644)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print("이미 실행 중 — 이번 회차 건너뜀")
    sys.exit(0)
os.set_inheritable(fd, True)
os.execv("/bin/bash", ["bash"] + sys.argv[2:])' "$CFG_ARCHIVE/.ws_gc.flock" "$0" "${ORIG_ARGS[@]}"
fi
[ -f "$CFG_LOG" ] && [ "$(wc -c < "$CFG_LOG")" -gt "$CFG_LOG_MAX_BYTES" ] && mv -f "$CFG_LOG" "$CFG_LOG.1"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/ws_gc.XXXXXX") || die "임시 폴더 생성 실패" 3
trap 'rm -rf "${TMP:?}"' EXIT

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
awk -F'\t' -v AS="$ASSUME_IDLE" 'FILENAME == ARGV[1] { if ($2 == "reclaimable" || (AS != "" && $1 == AS)) idle[$3] = 1; else if ($2 != "released") print $4; next } !($1 in idle) { print $2 }' "$TMP/workers.tsv" "$TMP/terms.tsv" | sort -u > "$TMP/live"

echo "$WL" | jq -r --arg root "$CFG_ROOT/" '
  [.result.worktrees[].id] as $ids
  | ([.result.worktrees[] | .path as $pp | (.childWorktreeIds // [])[] | {key: ., value: $pp}] | from_entries) as $par
  | .result.worktrees[]
  | select((.isMainWorktree | not) and (.path | startswith($root)))
  | [.path, (.lastActivityAt // 0), ((.childWorktreeIds // []) | map(select(. as $c | $ids | index($c))) | length), ($par[.id] // "")] | @tsv' > "$TMP/rows.tsv"
echo "$WL" | jq -r '.result.worktrees[].path' | sort -u > "$TMP/registered"

# ── 판정 ──────────────────────────────────────────────────────────────────────────────────
REASON="" DETAIL="" PR_LABEL="" N_NOISE=0 N_LIC=0 N_OUT=0 N_TRACKED=0 N_UNVERIFIED=0
# 변경 분류(확신 없으면 보존): 추적 수정·허용 목록 밖 파일(미추적·gitignore) → N_REAL(N_TRACKED·N_UNVERIFIED, 근거 $TMP/keep.txt),
# 허용 목록의 결과물 → $TMP/outputs.txt(보관 대상, 그중 미추적 파일 수 N_OUT), 확인된 부산물 → $TMP/noise.txt(근거 로그용).
# 한 줄 = 종류 TAB 경로. 종류: N 부산물·L LICENSE 후보·O 미추적 결과물·G 무시 결과물·C 캐시·U 미확인 미추적·I 미확인 무시·R 추적 수정
CLASSIFY_AWK='
  function allowed(q) { return (q ~ ENVIRON["DZ"] || tolower(q) ~ ENVIRON["RZ"]) && q !~ ENVIRON["SZ"] }
  skip { skip = 0; next }
  MODE_ == "tracked" {
    xy = substr($0, 1, 2); q = substr($0, 4); if (xy ~ /^[RC]/ || xy ~ /^.[RC]/) skip = 1
    # 부산물 예외는 미추적 파일에만. 추적 파일 수정은 확장자·폴더와 무관하게 R(보존)
    if (xy == "??") print (q ~ ENVIRON["NZ"] ? "N" : (allowed(q) ? "O" : "U")) "\t" q
    else if (q ~ ENVIRON["LZ"] && xy ~ /^[ M][ M]$/) print "L\t" q
    else print "R\t" q
    next }
  { q = $0; if (q == "") next
    if (substr(q, 1, 1) == "!") print "I\t" substr(q, 2)
    else if (q ~ ENVIRON["SZ"]) print "I\t" q
    else if (q ~ ENVIRON["CZ"]) print "C\t" q
    else print (allowed(q) ? "G" : "I") "\t" q }'
# gitignore 파일 목록. git 이 통째로 묶어 보여 주는 폴더(docs/ 처럼 안에 결과물만 든 경우 포함)는 허용·캐시·소스 판정이 서지 않으면 파일 단위로 풀어서 봄
ignored_entries() {
  local p="$1" e
  git -C "$p" ls-files -o -i --exclude-standard --directory -z 2>/dev/null | tr '\0' '\n' | while IFS= read -r e; do
    case "$e" in
      */) if printf '%s' "$e" | grep -Eq -e "$CZ"; then
            # 캐시 후보 폴더: 이름만 믿지 않고, 생성 목록(CFG_CACHE_GENERATED_RE)에 없는 파일을 "!경로" 로 내보내 미확인 처리
            (cd "$p" && find "${e%/}" \( -type f -o -type l \) -print 2>/dev/null) | GZ="$CFG_CACHE_GENERATED_RE" awk '$0 ~ ENVIRON["GZ"] { next } { print "!" $0 }'
            printf '%s\n' "$e"
          elif printf '%s' "$e" | grep -Eq -e "$DZ" -e "$SZ"; then printf '%s\n' "$e"
          else git -C "$p" ls-files -o -i --exclude-standard -z -- "$e" 2>/dev/null | tr '\0' '\n'; fi ;;
      *) printf '%s\n' "$e" ;;
    esac
  done
}
classify_changes() {
  local p="$1" k path
  : > "$TMP/noise.txt"; : > "$TMP/licenses.txt"; : > "$TMP/outputs.txt"; : > "$TMP/keep.txt"
  N_REAL=0 N_NOISE=0 N_LIC=0 N_OUT=0 N_TRACKED=0 N_UNVERIFIED=0
  export NZ="$CFG_NOISE_RE" LZ="$CFG_LICENSE_RE" DZ="$CFG_OUTPUT_DIRS_RE" RZ="$CFG_OUTPUT_ROOT_RE" SZ="$CFG_SOURCE_DIR_RE" CZ="$CFG_CACHE_RE"
  if ! { git -C "$p" status --porcelain=v1 -z --untracked-files=all --ignore-submodules=all 2>/dev/null | tr '\0' '\n' | awk -v MODE_=tracked "$CLASSIFY_AWK" \
      && ignored_entries "$p" | awk -v MODE_=ignored "$CLASSIFY_AWK"; } > "$TMP/chg.tsv"; then
    N_REAL=1; return 1
  fi
  while IFS=$'\t' read -r k path; do
    case "$k" in
      N) N_NOISE=$((N_NOISE + 1)); printf '부산물 %s\n' "$path" >> "$TMP/noise.txt" ;;
      L) if git -C "$p" diff HEAD --ignore-all-space --ignore-cr-at-eol --quiet -- "$path" 2>/dev/null; then
           N_LIC=$((N_LIC + 1)); printf '줄바꿈만 %s\n' "$path" >> "$TMP/noise.txt"
           printf '%s\n' "$path" >> "$TMP/licenses.txt"
         else N_REAL=$((N_REAL + 1)); N_TRACKED=$((N_TRACKED + 1)); printf '추적 수정 %s\n' "$path" >> "$TMP/keep.txt"; fi ;;
      O) N_OUT=$((N_OUT + 1)); printf '%s\n' "$path" >> "$TMP/outputs.txt" ;;
      G) printf '%s\n' "$path" >> "$TMP/outputs.txt" ;;
      C) ;;
      U) N_REAL=$((N_REAL + 1)); N_UNVERIFIED=$((N_UNVERIFIED + 1)); printf '미확인 미추적 %s\n' "$path" >> "$TMP/keep.txt" ;;
      I) N_REAL=$((N_REAL + 1)); N_UNVERIFIED=$((N_UNVERIFIED + 1)); printf '미확인 무시파일 %s\n' "$path" >> "$TMP/keep.txt" ;;
      *) N_REAL=$((N_REAL + 1)); N_TRACKED=$((N_TRACKED + 1)); printf '추적 수정 %s\n' "$path" >> "$TMP/keep.txt" ;;
    esac
  done < "$TMP/chg.tsv"
  # 중복·폴더 안에 이미 든 항목 제거(같은 파일을 두 번 세지 않음)
  LC_ALL=C sort -u "$TMP/outputs.txt" | awk 'last != "" && index($0, last) == 1 { next } /\/$/ { last = $0 } { print }' > "$TMP/outputs.dedup" && mv "$TMP/outputs.dedup" "$TMP/outputs.txt"
}
# 서브모듈 안에 미커밋·미push 가 없으면 0. 서브모듈이 없어도 0
submodules_clean() {
  local p="$1" line sp
  [ -f "$p/.gitmodules" ] || return 0
  while IFS= read -r line; do
    case "$line" in -*) continue ;; esac            # 초기화 안 된 서브모듈은 작업물이 없음
    sp="$p/$(printf '%s' "$line" | sed -E 's/^.[0-9a-f]+ //; s/ \(.*\)$//')"
    # 서브모듈 안도 상위와 같은 분류를 적용(미추적·gitignore 코드 포함). 보관 대상은 상위 폴더만 보관하므로 결과물·부산물도 있으면 안 됨.
    # 서브셸에서 임시 폴더를 바꿔 호출해 상위 판정 상태를 건드리지 않음
    ( TMP="$TMP/sub"; mkdir -p "$TMP" && classify_changes "$sp" && [ "$N_REAL" = 0 ] && [ "$N_OUT" = 0 ] && [ "$N_NOISE" = 0 ] && [ "$N_LIC" = 0 ] && [ ! -s "$TMP/outputs.txt" ] ) || return 1
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
  if [ "$N_REAL" -gt 0 ]; then REASON=dirty; DETAIL="미커밋 ${N_REAL}건(추적 수정 ${N_TRACKED}·미확인 ${N_UNVERIFIED})"; return 1; fi
  if [ -n "$br" ]; then
    prs=$(cd "$p" && gh pr list --head "$br" --state all --json number,state,headRefOid --limit 30 2>/dev/null) && echo "$prs" | jq -e 'type == "array"' >/dev/null 2>&1 \
      || { REASON=gh_fail; DETAIL="gh pr list 실패(로그인·네트워크 확인)"; return 1; }
    PR_LABEL=$(echo "$prs" | jq -r 'map("#\(.number) \(.state)") | join(",")')
    if echo "$prs" | jq -e 'any(.[]; .state == "OPEN")' >/dev/null; then REASON=pr_open; DETAIL="$PR_LABEL"; return 1; fi
  fi
  # PR 상태와 별개로 현재 HEAD 가 원격에 올라가 있어야 함(병합된 PR 뒤에 쌓은 push 안 한 커밋 보호).
  # 원격 ref 에 없어도 HEAD 가 병합된 PR 의 마지막 커밋과 같으면 원격 브랜치가 지워진 경우라 허용
  if [ "$(git -C "$p" rev-list --count HEAD --not --remotes 2>/dev/null || echo 1)" != 0 ]; then
    head_sha=$(git -C "$p" rev-parse HEAD 2>/dev/null)
    if [ -z "$PR_LABEL" ] || ! echo "$prs" | jq -e --arg h "$head_sha" 'any(.[]; .state == "MERGED" and .headRefOid == $h)' >/dev/null 2>&1; then
      REASON=unmerged; DETAIL="${PR_LABEL:+PR $PR_LABEL 이후 }origin 에 없는 커밋 있음"; return 1
    fi
  fi
  submodules_clean "$p" || { REASON=submodule; DETAIL="서브모듈 안 미커밋·미push"; return 1; }
  DETAIL="idle=${age}d branch=${br:-detached} pr=${PR_LABEL:--}"
  [ "$N_OUT" -gt 0 ] && DETAIL="$DETAIL 결과물=$N_OUT"
  return 0
}

# ── 삭제 절차 ─────────────────────────────────────────────────────────────────────────────
# 결과물 보관: $TMP/outputs.txt 의 경로를 tar 로 복제한 뒤 항목마다 원본과 byte 단위로 대조(파일 cmp·폴더 diff -r).
# 복사 실패·내용 불일치·보관본에 남는 파일은 모두 1(삭제하지 않음). 성공하면 ARCHIVED_FILES 에 파일 수
archive_outputs() {
  local p="$1" dest="$2" e src n total=0
  ARCHIVED_FILES=0
  [ -s "$TMP/outputs.txt" ] || return 0
  mkdir -p "$dest" || { log "ARCHIVE_MISMATCH 보관 폴더 생성 실패 → $dest"; return 1; }
  (cd "$p" && tar -cf - -T "$TMP/outputs.txt") 2>/dev/null | tar -xf - -C "$dest" 2>/dev/null || { log "ARCHIVE_MISMATCH 복제 실패 → $dest"; return 1; }
  while IFS= read -r e; do
    e="${e%/}"; src="$p/$e"
    if [ -L "$src" ]; then [ "$(readlink "$src")" = "$(readlink "$dest/$e" 2>/dev/null)" ] || { log "ARCHIVE_MISMATCH $e 링크가 다름"; return 1; }; n=1
    elif [ -d "$src" ]; then diff -rq "$src" "$dest/$e" >/dev/null 2>&1 || { log "ARCHIVE_MISMATCH $e 원본 $(count_files "$src")개와 보관본의 내용·파일 수가 다름"; return 1; }; n=$(count_files "$src")
    elif [ -f "$src" ]; then cmp -s "$src" "$dest/$e" || { log "ARCHIVE_MISMATCH $e 원본과 보관본의 내용이 다름"; return 1; }; n=1
    else log "ARCHIVE_MISMATCH $e 원본이 사라짐"; return 1; fi
    total=$((total + n)); log "ARCHIVE $e 파일 $n → $dest/$e"
  done < "$TMP/outputs.txt"
  ARCHIVED_FILES=$total
  return 0
}
# 보관 대상 미리보기(dry-run 용)
preview_outputs() { tr '\n' ',' < "$TMP/outputs.txt" | sed 's/,$//'; }
# 허브 폴더 coord-<slug>-hub → slug. 부모가 없으면 _none
hub_slug() { local b; [ -n "$1" ] || { echo _none; return; }; b=$(basename "$1"); b="${b#"$CFG_HUB_PREFIX"}"; echo "${b%-hub}"; }
# 허브 색인에 한 줄(시각·작업 공간·보관 경로·파일 수). 실패해도 삭제 결과에 영향 없음
inbox_add() { # 부모경로 이름 보관경로 파일수
  local slug; slug=$(hub_slug "$1")
  mkdir -p "$CFG_INBOX" && printf '%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$2" "$3" "$4" >> "$CFG_INBOX/$slug.tsv" \
    && log "INBOX $slug $2 파일 $4" || log "INBOX_FAIL $CFG_INBOX/$slug.tsv 기록 실패(권한 확인). 보관본은 남음"
}

remove_ws() {
  local p="$1" dest="$2" out path
  classify_changes "$p"
  [ "$N_REAL" = 0 ] || return 1
  # 판정 뒤 새로 생긴 결과물도 지우기 전에 보관·대조(복제는 되풀이해도 같은 결과)
  archive_outputs "$p" "$dest" || return 1
  while IFS= read -r path; do
    git -C "$p" -c core.autocrlf=false checkout -- "$path" >/dev/null 2>&1 || return 1
  done < "$TMP/licenses.txt"
  cp "$TMP/noise.txt" "$TMP/noise0.txt"
  out=$(orca worktree rm --worktree "path:$p" --json 2>&1) && return 0
  # 거부 사유가 미커밋 변경 계열일 때만 force. 잠금·사용 중 같은 다른 사유는 우회하지 않음.
  if printf '%s' "$out" | grep -Eiq -- "$CFG_RM_DIRTY_RE" || printf '%s' "$out" | grep -Eq -- "$CFG_RM_DIRTY_STATUS_RE"; then
    # 혼합 줄바꿈 index 와 eol=lf 속성은 checkout 후에도 dirty. 강제 삭제 전 보관·변경 재확인 필요.
    classify_changes "$p"
    [ "$N_REAL" = 0 ] || return 1
    cp "$TMP/outputs.txt" "$TMP/outputs0.txt"
    archive_outputs "$p" "$dest" || return 1
    classify_changes "$p"
    # 기존 부산물은 허용, 거부 뒤 새로 생긴 부산물·보관 뒤 새로 생긴 결과물은 차단
    [ "$N_REAL" = 0 ] && [ -z "$(sort "$TMP/noise.txt" | comm -13 <(sort "$TMP/noise0.txt") -)" ] && cmp -s "$TMP/outputs0.txt" "$TMP/outputs.txt" && submodules_clean "$p" || return 1
    if [ "$N_LIC" -gt 0 ] || [ "$N_OUT" -gt 0 ] || [ -f "$p/.gitmodules" ]; then
      log "RM_RETRY_FORCE 보관 파일 수 일치·실변경 0·새 부산물 0·서브모듈 clean 확인, LICENSE ${N_LIC}건·결과물 ${N_OUT}건 → --force"
      orca worktree rm --worktree "path:$p" --force --json >/dev/null 2>&1 && return 0
    fi
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

# ── 부모 없는 작업 공간 자동 연결 ────────────────────────────────────────────────────────
# create_worker_ws.sh 의 부모 연결이 경합으로 빠지면(11-13 실측) 허브 하위가 아닌 채 남음. 같은 티켓(LRM-n)을 가진 다른 작업 공간들의
# 부모 허브가 하나로 정해지면 그 허브에 연결하고, 근거가 없거나 허브가 갈리면 연결하지 않고 경고만 남김. 전체 점검에서만 실행(--only·--path·--parent 제외)
adopt_orphans() {
  echo "$WL" | jq -r --arg root "$CFG_ROOT/$CFG_GAME_REPO_DIR/" --arg hub "$CFG_HUB_PREFIX" --arg tre "$CFG_TICKET_RE" '
    ([.result.worktrees[] | .path as $pp | (.childWorktreeIds // [])[] | {key: ., value: $pp}] | from_entries) as $par
    | def base: split("/") | last;
    def ticket: ([.displayName, .branch, (.path | base)] | map(. // "") | join(" ") | capture("(?<t>" + $tre + ")") | .t) // "";
    [.result.worktrees[] | {id, path, t: ticket, par: ($par[.id] // "")}] as $all
    | $all[] | select((.path | startswith($root)) and (.path | base | startswith($hub) | not) and .par == "" and .t != "") as $o
    | [$all[] | select(.t == $o.t and .par != "" and (.par | base | startswith($hub))) | .par] | unique as $hubs
    | [$o.path, $o.t, ($hubs | join(","))] | @tsv' | while IFS=$'\t' read -r op ot oh; do
    [ -n "$op" ] || continue
    case "$oh" in
      "") log "WARN_NO_PARENT ${op#"$CFG_ROOT"/} — 부모 없음, 같은 티켓($ot) 작업 공간에서 허브 근거를 못 찾아 연결 안 함"
          echo "⚠️ 부모 없는 작업 공간: $op — 허브를 정해 수동 연결: orca worktree set --worktree path:$op --parent-worktree path:<허브 경로>" >&2 ;;
      *,*) log "WARN_PARENT_SPLIT ${op#"$CFG_ROOT"/} — 같은 티켓($ot) 작업 공간의 허브가 갈려 연결 안 함: $oh"
          echo "⚠️ 부모 없는 작업 공간: $op — 같은 티켓($ot)의 허브가 여럿($oh). 하나를 골라 수동 연결: orca worktree set --worktree path:$op --parent-worktree path:<허브 경로>" >&2 ;;
      *)  if [ "$APPLY" = 0 ]; then log "ADOPT_CANDIDATE ${op#"$CFG_ROOT"/} → ${oh#"$CFG_ROOT"/} (티켓 $ot, 시험이라 연결 안 함)"
          elif orca worktree set --worktree "path:$op" --parent-worktree "path:$oh" --json >/dev/null 2>&1; then
            log "ADOPT ${op#"$CFG_ROOT"/} → ${oh#"$CFG_ROOT"/} (같은 티켓 $ot 의 부모 허브)"
            echo "⚠️ 부모 없던 작업 공간을 같은 티켓($ot) 허브에 연결함: $op → $oh" >&2
          else log "ADOPT_FAIL ${op#"$CFG_ROOT"/} → $oh — orca worktree set 실패, 수동 연결 필요"
            echo "⚠️ 부모 연결 실패: orca worktree set --worktree path:$op --parent-worktree path:$oh" >&2; fi ;;
    esac
  done
}
[ -n "$ONLY$ONLYPATH$PARENT" ] || adopt_orphans

# ── 본 흐름 ───────────────────────────────────────────────────────────────────────────────
: > "$TMP/skips"; : > "$TMP/items.jsonl"; : > "$TMP/keepcode.jsonl"; : > "$TMP/failed.txt"
N_CAND=0 N_CAND_OUT=0 N_DEL=0 N_DEL_OUT=0 N_FAIL=0 FREED_KB=0 CAND_KB=0
# 작업 공간별 결과 한 줄(--json 의 items). 판정: skip·candidate·deleted·failed
item() { jq -nc --arg n "$name" --arg p "$p" --arg v "$1" --arg r "$2" --arg d "$3" '{name:$n,path:$p,verdict:$v,reason:$r,detail:$d}' >> "$TMP/items.jsonl"; }
log "START root=$CFG_ROOT days=$DAYS only=${ONLY:--} path=${ONLYPATH:--} parent=${PARENT:--}"

# 등록되지 않은 폴더(Orca 밖)는 건드리지 않고 사유만 셈. 한 곳·한 허브만 보는 호출에서는 생략
[ -n "$ONLYPATH$PARENT" ] || for d in "$CFG_ROOT"/*/*/; do
  d="${d%/}"; [ -d "$d" ] || continue
  { [ -e "$d/.git" ] && ! grep -Fxq -- "$d" "$TMP/registered"; } && { echo not_orca >> "$TMP/skips"; log "SKIP not_orca ${d#"$CFG_ROOT"/}"; }
done

while IFS=$'\t' read -r p last_ms kids parent; do
  [ -n "$p" ] || continue
  [ -z "$ONLY" ] || case "$p" in *"$ONLY"*) ;; *) continue ;; esac
  [ -z "$ONLYPATH" ] || [ "$p" = "$ONLYPATH" ] || continue
  [ -z "$PARENT" ] || [ "$parent" = "$PARENT" ] || continue
  name="${p#"$CFG_ROOT"/}"
  if ! evaluate "$p" "$last_ms" "$kids"; then
    echo "$REASON" >> "$TMP/skips"; log "SKIP $REASON $name ${DETAIL:+— $DETAIL}"
    item skip "$REASON" "$DETAIL"
    [ "$REASON" = dirty ] && [ "$N_NOISE$N_LIC" != 00 ] && log "NOISE $name 부산물 ${N_NOISE}건·줄바꿈만 ${N_LIC}건은 제외하고 셈"
    if [ "$REASON" = dirty ]; then
      log "KEEP_CODE $name 추적 수정 ${N_TRACKED}·미확인 ${N_UNVERIFIED}: $(head -n "$CFG_KEEP_CODE_LOG_MAX" "$TMP/keep.txt" | tr '\n' ',' | sed 's/,$//')"
      jq -nc --arg n "$name" --argjson t "$N_TRACKED" --argjson u "$N_UNVERIFIED" --rawfile f "$TMP/keep.txt" --argjson max "$CFG_KEEP_CODE_LOG_MAX" \
        '{name:$n, tracked:$t, unverified:$u, files:($f | split("\n") | map(select(length > 0)) | .[:$max])}' >> "$TMP/keepcode.jsonl"
    fi
    continue
  fi
  N_CAND=$((N_CAND + 1)); [ "$N_OUT" -gt 0 ] && N_CAND_OUT=$((N_CAND_OUT + 1))
  size_kb=$(du -sk "$p" 2>/dev/null | cut -f1); size_kb="${size_kb:-0}"; CAND_KB=$((CAND_KB + size_kb))
  if [ "$N_NOISE$N_LIC" != 00 ]; then
    log "NOISE $name 미커밋 0 으로 친 부산물 ${N_NOISE}건·줄바꿈만 ${N_LIC}건"
    head -n "$CFG_NOISE_LOG_MAX" "$TMP/noise.txt" | while IFS= read -r l; do log "NOISE   $l"; done
    extra=$(( $(wc -l < "$TMP/noise.txt" | tr -d ' ') - CFG_NOISE_LOG_MAX )); [ "$extra" -gt 0 ] && log "NOISE   …외 ${extra}건"
  fi
  if [ "$APPLY" = 0 ]; then
    log "CANDIDATE $name $DETAIL size=$(human "$size_kb") 보관예정=[$(preview_outputs "$p")]"
    item candidate ok "$DETAIL"
    continue
  fi
  dest="$CFG_ARCHIVE/$(dirname "$name")/$(basename "$name")"
  ARCHIVED_FILES=0
  if ! archive_outputs "$p" "$dest"; then echo archive_fail >> "$TMP/skips"; N_FAIL=$((N_FAIL + 1)); log "SKIP archive_fail $name — 보관 복제·대조 실패, 삭제 안 함(디스크 여유 확인)"; echo "$name" >> "$TMP/failed.txt"; item failed archive_fail "보관 복제·대조 실패"; continue; fi
  if remove_ws "$p" "$dest"; then
    N_DEL=$((N_DEL + 1)); FREED_KB=$((FREED_KB + size_kb)); [ "$N_OUT" -gt 0 ] && N_DEL_OUT=$((N_DEL_OUT + 1))
    log "DELETE $name $DETAIL size=$(human "$size_kb") 보관파일=$ARCHIVED_FILES (브랜치는 유지)"
    item deleted ok "$DETAIL 보관파일=$ARCHIVED_FILES"
    [ "$ARCHIVED_FILES" -gt 0 ] && inbox_add "$parent" "$name" "$dest" "$ARCHIVED_FILES"
    release_workers "$p"
  else
    echo rm_fail >> "$TMP/skips"; N_FAIL=$((N_FAIL + 1)); log "SKIP rm_fail $name — orca worktree rm 거부, 보관본은 남음"
    echo "$name" >> "$TMP/failed.txt"; item failed rm_fail "orca worktree rm 거부"
  fi
done < "$TMP/rows.tsv"

# ── 요약 ──────────────────────────────────────────────────────────────────────────────────
label() { case "$1" in
  hub) echo 허브 ;; current) echo 현재작업 ;; children) echo 하위있음 ;; live) echo 터미널·워커 ;; idle) echo 최근활동 ;; dirty) echo 미커밋 ;;
  pr_open) echo PR열림 ;; unmerged) echo 미병합 ;; gh_fail) echo PR조회실패 ;; protected) echo 보호브랜치 ;; submodule) echo 서브모듈 ;;
  not_orca) echo 미등록 ;; missing) echo 폴더없음 ;; archive_fail) echo 보관실패 ;; rm_fail) echo 삭제실패 ;; *) echo "$1" ;; esac; }
SKIP_TXT=$(sort "$TMP/skips" | uniq -c | while read -r n r; do printf '%s %s·' "$(label "$r")" "$n"; done | sed 's/·$//')
N_SKIP=$(wc -l < "$TMP/skips" | tr -d ' ')
N_KEEP_CODE=$(wc -l < "$TMP/keepcode.jsonl" | tr -d ' ')
if [ "$APPLY" = 1 ]; then RESULT="삭제 ${N_DEL}개(결과물만 ${N_DEL_OUT}개)·줄인 용량 $(human "$FREED_KB")"; else RESULT="삭제 후보 ${N_CAND}개(결과물만 ${N_CAND_OUT}개)·예상 용량 $(human "$CAND_KB")(시험, 삭제 없음)"; fi
log "END $RESULT 건너뜀 ${N_SKIP}곳: ${SKIP_TXT:-없음} (코드 변경으로 보존 ${N_KEEP_CODE}곳)"

if [ "$JSON" = 1 ]; then
  jq -n --arg mode "$MODE" --argjson cand "$N_CAND" --argjson candout "$N_CAND_OUT" --argjson del "$N_DEL" --argjson delout "$N_DEL_OUT" --argjson fail "$N_FAIL" --argjson freed "$FREED_KB" --argjson candkb "$CAND_KB" \
    --argjson skips "$(sort "$TMP/skips" | uniq -c | awk '{printf "%s\"%s\":%d", (n++ ? "," : "{"), $2, $1} END{print n ? "}" : "{}"}')" \
    --slurpfile keep "$TMP/keepcode.jsonl" --slurpfile items "$TMP/items.jsonl" \
    '{mode:$mode, candidates:$cand, candidates_outputs_only:$candout, deleted:$del, deleted_outputs_only:$delout, failed:$fail, freed_kb:$freed, candidate_kb:$candkb, skipped:$skips, keep_code:$keep, items:$items}'
fi

# Slack: 양식 A(첫 줄 주제 → 빈 줄 → • 결과). 변화가 있을 때만: 시험은 후보가 있을 때, 실삭제는 삭제가 있거나 지난번과 다른 실패가 있을 때.
# 30분마다 도는 실삭제에서 같은 실패가 반복돼도 다시 보내지 않도록 실패 목록을 .ws_gc.lastfail 에 남겨 대조함. 전송 실패는 정리 결과에 영향 없음
FAIL_SIG=$(sort -u "$TMP/failed.txt" | tr '\n' ',')
SEND=0
if [ "$APPLY" = 1 ]; then
  [ "$N_DEL" -gt 0 ] && SEND=1
  if [ "$N_FAIL" -gt 0 ] && [ "$FAIL_SIG" != "$(cat "$CFG_ARCHIVE/.ws_gc.lastfail" 2>/dev/null)" ]; then SEND=1; fi
  [ -n "$ONLYPATH$PARENT" ] || printf '%s' "$FAIL_SIG" > "$CFG_ARCHIVE/.ws_gc.lastfail"
elif [ "$N_CAND" -gt 0 ] || [ "$N_FAIL" -gt 0 ]; then SEND=1; fi
if [ "$SLACK" = 1 ] && [ "$SEND" = 1 ]; then
  TITLE=$([ "$APPLY" = 1 ] && echo "워크스페이스 자동 정리 완료" || echo "워크스페이스 정리 시험 점검")
  MSG="$TITLE"$'\n\n'"• 결과: $RESULT, 건너뜀 ${N_SKIP}곳(${SKIP_TXT:-없음})"
  [ "$N_KEEP_CODE" -gt 0 ] && MSG="$MSG"$'\n'"• 코드 변경이 있어 남긴 곳 ${N_KEEP_CODE}곳(허브가 커밋 또는 정리)"
  [ "$N_FAIL" -gt 0 ] && MSG="$MSG"$'\n'"• 삭제 못 한 곳 ${N_FAIL}곳(보관본은 남음)"
  if [ -x "$CFG_NOTIFY" ] || command -v "$CFG_NOTIFY" >/dev/null 2>&1; then
    "$CFG_NOTIFY" --tag "$CFG_SLACK_TAG" --kind progress "$MSG" >/dev/null 2>&1 && log "SLACK sent" || log "SLACK_FAIL 전송 실패(토큰·네트워크 확인). 정리 결과는 유효"
  else log "SLACK_SKIP 전송 스크립트 없음: $CFG_NOTIFY"; fi
fi
[ "$N_FAIL" -eq 0 ] || exit 1
exit 0
