#!/usr/bin/env bash
#
# UI 화면 단독 미리보기. 부팅·로그인·네트워크 없이 화면 프리팹 하나를 배치모드로 띄워 PNG 로 남긴다.
#
#   bash Tools/verify/ui-preview.sh <화면|all>[,<화면>…] [가로x세로 …] [--stage <오토마 스테이지 Id>] [--bundle] [--summary-at day-end|mission-issued] [--who <요청자>] [--compare <시안 PNG>] [--prefab-from-commit <작업공간>:<커밋>:<경로>] [--timeout <초>]
#   예) bash Tools/verify/ui-preview.sh automa-daily-summary
#       bash Tools/verify/ui-preview.sh all 1080x1920 1440x3120
#
# 실행: PROJ=<게임 작업 공간> bash <이 폴더>/ui-preview.sh <화면> … (게임 작업 공간 안에서 실행하면 PROJ 생략)
# 출력: TestResults/ui-preview/<요청자>/<화면>_<가로>x<세로>_t0|t1|t2.png (연출 시작·절반·끝), summary.json, unity.log
# 데이터: 로컬 게임 데이터 저장소(build/game-data-local, 시트 고정본으로 생성) + 데브 번역 표. 없으면 준비 명령을 안내하고 종료.
# 실행은 항상 build/ui-preview-lanes/lane-N(APFS 복제)에서 한다. 원본 작업 공간은 읽기만. 레인이 다 차면 순번 대기.
# 종료: 0 성공 / 1 캡처 실패 / 2 실행 불가(인자·데이터·Unity 없음·타임아웃)
#
# sh·zsh 로 부르면 배열·<<< 문법에서 깨짐 → bash 로 다시 실행
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -uo pipefail

# PROJ: 대상 Unity 프로젝트(게임 저장소 작업 공간). 기본은 현재 디렉터리의 git 최상위
PROJ="${PROJ:-$(git rev-parse --show-toplevel 2>/dev/null)}"
UNITY="${UNITY:-/Applications/Unity/Hub/Editor/$(sed -n 's/^m_EditorVersion: //p' "$PROJ/ProjectSettings/ProjectVersion.txt")/Unity.app/Contents/MacOS/Unity}"
TOOL_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="$TOOL_DIR/Editor"
INSTALL_REL="Assets/Tests/Editor/UiPreview"
STORE="build/game-data-local"
TIMEOUT=600

screen_names() {
  grep -rhoE 'PreviewScreen\("[a-z0-9-]+"' "$REGISTRY" "$TOOL_DIR/Editor-bundle" 2>/dev/null | sed -E 's/.*\("([^"]+)"/\1/' | tr '\n' ' '
}

usage() {
  awk 'NR > 1 && !/^#/ { exit } NR > 1' "$0"
  echo "#   가능한 화면: all $(screen_names)"
}

SCREENS=""; SIZES=(); STAGE=""; BUNDLE=0; OVERRIDES=(); SUMMARY_AT=""; WHO="default"; COMPARE=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --bundle) BUNDLE=1 ;;
    --prefab-from-commit) shift; OVERRIDES+=("${1:?--prefab-from-commit <작업공간>:<커밋>:<프로젝트 상대 경로> 필요}") ;;
    --stage) shift; STAGE="${1:?--stage 값 필요}" ;;
    --summary-at) shift; SUMMARY_AT="${1:?--summary-at day-end|mission-issued 필요}" ;;
    --who) shift; WHO="${1:?--who 이름 필요}" ;;
    --compare) shift; COMPARE="${1:?--compare <시안 PNG> 필요}" ;;
    --timeout) shift; TIMEOUT="${1:?--timeout 값 필요}" ;;
    [0-9]*x[0-9]*) SIZES+=("$1") ;;
    -*) echo "[ui-preview] 알 수 없는 인자: $1 — -h 로 사용법 확인" >&2; exit 2 ;;
    *) [ -z "$SCREENS" ] || { echo "[ui-preview] 화면은 하나의 인자에 쉼표로 묶음: $SCREENS,$1" >&2; exit 2; }; SCREENS="$1" ;;
  esac
  shift
done
[ -n "$SCREENS" ] || { usage >&2; exit 2; }
case "$WHO" in *[!A-Za-z0-9._-]*) echo "[ui-preview] --who 는 영문·숫자·._- 만" >&2; exit 2 ;; esac
OUT="$PROJ/TestResults/ui-preview/$WHO"

KNOWN=" all $(screen_names)"
for name in ${SCREENS//,/ }; do
  case "$KNOWN " in *" $name "*) ;; *) echo "[ui-preview] 알 수 없는 화면: $name — 가능:$KNOWN" >&2; exit 2 ;; esac
done
[ -x "$UNITY" ] || { echo "[ui-preview] Unity 없음: $UNITY — UNITY 환경 변수로 경로 지정" >&2; exit 2; }
[ -f "$PROJ/$STORE/manifest.json" ] || {
  echo "[ui-preview] 로컬 게임 데이터 없음: $STORE — node Tools/game-data/local/refresh.mjs --baseline pin 실행 후 재시도" >&2
  exit 2
}

# 동시 요청: 요청마다 원본을 건드리지 않고 APFS 복제 레인(최대 LANES 개)에서 병렬 실행. 레인이 다 차면 순번 대기
LANES_DIR="$PROJ/build/ui-preview-lanes"
LANES="${UI_PREVIEW_LANES:-${LRM_UNITY_SLOTS:-4}}"
QUEUE="$LANES_DIR/queue"
HISTORY="$LANES_DIR/history.tsv"
STATUS_EVERY=30
mkdir -p "$QUEUE"

alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
reap_lanes() {
  for lock in "$LANES_DIR"/lane-*.lock; do
    [ -d "$lock" ] || continue
    alive "$(cat "$lock/pid" 2>/dev/null)" || rm -rf -- "${lock:?}"
  done
  for q in "$QUEUE"/*; do
    [ -f "$q" ] || continue
    alive "$(cut -f1 "$q")" || rm -f -- "$q"
  done
}
avg_minutes() {
  [ -f "$HISTORY" ] || { echo "?"; return; }
  tail -10 "$HISTORY" | awk -F'\t' '{ s += $1; n++ } END { if (n) printf "%.0f", s / n / 60; else print "?" }'
}

TICKET="$QUEUE/$(date +%s%N)-$$"
printf '%s\t%s\t%s\t%s\n' "$$" "$WHO" "$SCREENS" "$(date '+%T')" > "$TICKET"
LANE=""; LOCK=""; SLOT=""
BACKUP="$(mktemp -d)"
RESTORE=()
CREATED=()
cleanup() {
  [ -z "${UNITY_PID:-}" ] || kill "$UNITY_PID" 2>/dev/null
  if [ -n "$LANE" ]; then
    rm -rf -- "${LANE:?}/$INSTALL_REL" "${LANE:?}/$INSTALL_REL.meta"
    for rel in "${RESTORE[@]+"${RESTORE[@]}"}"; do
      cp -p "$BACKUP/$rel" "$LANE/$rel" 2>/dev/null
    done
    for rel in "${CREATED[@]+"${CREATED[@]}"}"; do
      rm -f -- "${LANE:?}/$rel"
    done
  fi
  [ -z "$SLOT" ] || bash "$SLOT_TOOL" release "$SLOT" >/dev/null
  [ -z "$LOCK" ] || rm -rf -- "${LOCK:?}"
  rm -f -- "${TICKET:?}"
  rm -rf -- "${BACKUP:?}"
}
trap cleanup EXIT
trap 'exit 2' INT TERM

WAITED=0
while [ -z "$LANE" ]; do
  reap_lanes
  POS=$(ls "$QUEUE" | sort | grep -n -- "-$$\$" | cut -d: -f1)
  FREE_RANK=0
  if [ "${POS:-1}" -le "$LANES" ]; then
    for i in $(seq 1 "$LANES"); do
      if mkdir "$LANES_DIR/lane-$i.lock" 2>/dev/null; then
        printf '%s\n' "$$" > "$LANES_DIR/lane-$i.lock/pid"
        printf '%s\t%s\t%s\n' "$WHO" "$SCREENS" "$(date '+%T')" > "$LANES_DIR/lane-$i.lock/info"
        LOCK="$LANES_DIR/lane-$i.lock"; LANE="$LANES_DIR/lane-$i"
        break
      fi
    done
  fi
  [ -n "$LANE" ] && break
  if [ $((WAITED % STATUS_EVERY)) -eq 0 ]; then
    AHEAD=$(for lock in "$LANES_DIR"/lane-*.lock; do [ -f "$lock/info" ] && awk -F'\t' '{ printf "%s(%s, %s 시작) ", $2, $1, $3 }' "$lock/info"; done)
    echo "[ui-preview] 대기 ${POS:-?}번째. 실행 중: ${AHEAD:-없음}. 예상 대기 약 $(avg_minutes)분(최근 실행 평균)"
  fi
  sleep 1; WAITED=$((WAITED + 1))
done
rm -f -- "$TICKET"

# 레인 준비: 처음이면 APFS 클론, 이후엔 바뀐 파일만 동기화(원본 작업 공간은 읽기만)
if [ ! -d "$LANE/Library" ]; then
  SRC_LIB="$PROJ/Library"
  [ -d "$SRC_LIB" ] || SRC_LIB="$(git -C "$PROJ" worktree list --porcelain | sed -n '1s/^worktree //p')/Library"
  [ -d "$SRC_LIB" ] || { echo "[ui-preview] 복제할 Library 없음" >&2; exit 2; }
  echo "[ui-preview] 레인 생성(APFS 클론): $LANE"
  mkdir -p "$LANE"
  cp -cR "$SRC_LIB" "$LANE/Library" || { echo "[ui-preview] Library 클론 실패" >&2; exit 2; }
fi
# 패키지 매니페스트가 저장소 상대 경로(LocalPackages 등)를 참조하므로 프로젝트 전체를 동기화. 생성물·결과물만 제외
rsync -a --delete --exclude '/Library/' --exclude '/Temp/' --exclude '/Logs/' --exclude '/obj/' --exclude '/UserSettings/' \
  --exclude '/TestResults/' --exclude '/build/ui-preview-lanes/' --exclude '/build/ui-preview-project/' --exclude '/.git' \
  "$PROJ/" "$LANE/" || { echo "[ui-preview] 레인 동기화 실패" >&2; exit 2; }
RUN_PROJ="$LANE"
echo "[ui-preview] 실행 레인: $(basename "$LANE") (요청자 $WHO)"

# Unity 동시 실행 상한은 공용 슬롯 도구가 정함(있을 때만)
SLOT_TOOL="${SLOT_TOOL:-$HOME/.config/lrm-hubs/unity_slot.sh}"
if [ -f "$SLOT_TOOL" ]; then
  SLOT=$(LRM_UNITY_SLOTS="${LRM_UNITY_SLOTS:-4}" LRM_SLOT_OWNER_PID=$$ bash "$SLOT_TOOL" acquire "ui-preview-$WHO") || { echo "[ui-preview] Unity 슬롯 획득 실패" >&2; exit 2; }
fi

INSTALL="$RUN_PROJ/$INSTALL_REL"
rm -rf -- "${INSTALL:?}" "${INSTALL:?}.meta"
cp -R "$REGISTRY" "$INSTALL" || { echo "[ui-preview] 도구 복사 실패: $INSTALL" >&2; exit 2; }
# 다른 작업 공간의 커밋에 있는 프리팹(+.meta)을 실행 동안만 덮고 끝나면 원본 복구(체리픽 없이 수정 확인)
for spec in "${OVERRIDES[@]+"${OVERRIDES[@]}"}"; do
  SRC_WS="${spec%%:*}"; rest="${spec#*:}"; SRC_SHA="${rest%%:*}"; SRC_REL="${rest#*:}"
  SRC_WS="${SRC_WS/#\~/$HOME}"
  if [ -z "$SRC_WS" ] || [ -z "$SRC_SHA" ] || [ -z "$SRC_REL" ] || [ "$rest" = "$SRC_REL" ]; then
    echo "[ui-preview] 형식: --prefab-from-commit <작업공간>:<커밋>:<프로젝트 상대 경로> (받은 값: $spec)" >&2; exit 2
  fi
  git -C "$SRC_WS" rev-parse --verify -q "$SRC_SHA^{commit}" >/dev/null || { echo "[ui-preview] 커밋 없음: $SRC_SHA @ $SRC_WS" >&2; exit 2; }
  for rel in "$SRC_REL" "$SRC_REL.meta"; do
    if ! git -C "$SRC_WS" cat-file -e "$SRC_SHA:$rel" 2>/dev/null; then
      [ "$rel" = "$SRC_REL" ] && { echo "[ui-preview] 커밋에 없는 파일: $SRC_SHA:$rel" >&2; exit 2; }
      continue
    fi
    if [ -e "$RUN_PROJ/$rel" ]; then
      mkdir -p "$BACKUP/$(dirname "$rel")"
      cp -p "$RUN_PROJ/$rel" "$BACKUP/$rel"
      RESTORE+=("$rel")
    else
      # 커밋에서 새로 생긴 파일: 실행 뒤 지움
      mkdir -p "$RUN_PROJ/$(dirname "$rel")"
      CREATED+=("$rel")
    fi
    git -C "$SRC_WS" cat-file blob "$SRC_SHA:$rel" > "$RUN_PROJ/$rel"
  done
  echo "[ui-preview] 덮음: $SRC_REL @ $SRC_SHA"
done

# 묶음 브랜치 전용 화면은 dev 에 없는 타입을 써서 기본 목록 위에 덮어씀
if [ "$BUNDLE" = 1 ]; then
  cp -R "$TOOL_DIR/Editor-bundle/." "$INSTALL/" || { echo "[ui-preview] 묶음 화면 복사 실패" >&2; exit 2; }
fi

mkdir -p "$OUT"
rm -f -- "$OUT"/*.png "$OUT/summary.json"
LOG="$OUT/unity.log"
ARGS=(-batchmode -projectPath "$RUN_PROJ" -executeMethod Tests.Editor.UiPreview.UiPreviewEntry.Run
  -uiPreviewScreen "$SCREENS" -uiPreviewOut "$OUT" -logFile "$LOG")
[ ${#SIZES[@]} -eq 0 ] || ARGS+=(-uiPreviewSizes "$(IFS=,; echo "${SIZES[*]}")")
[ -z "$STAGE" ] || ARGS+=(-uiPreviewStage "$STAGE")
[ -z "$SUMMARY_AT" ] || ARGS+=(-uiPreviewSummaryAt "$SUMMARY_AT")

"$UNITY" "${ARGS[@]}" &
UNITY_PID=$!
MEM_LIMIT_KB=$((8 * 1024 * 1024))
SECONDS=0
while kill -0 "$UNITY_PID" 2>/dev/null; do
  RSS=$(ps -o rss= -p "$UNITY_PID" 2>/dev/null | tr -d ' ')
  if [ "${RSS:-0}" -gt "$MEM_LIMIT_KB" ]; then
    kill "$UNITY_PID" 2>/dev/null
    echo "[ui-preview] Unity 메모리 8GB 초과로 중단" >&2
    exit 2
  fi
  if [ "$SECONDS" -ge "$TIMEOUT" ]; then
    kill "$UNITY_PID" 2>/dev/null
    echo "[ui-preview] ${TIMEOUT}초 초과로 중단 — $LOG 확인" >&2
    exit 2
  fi
  sleep 1
done
wait "$UNITY_PID"; CODE=$?
UNITY_PID=""
printf '%s\t%s\t%s\n' "$SECONDS" "$WHO" "$SCREENS" >> "$HISTORY"

grep -E '\[UiPreview\]|error CS' "$LOG" 2>/dev/null | tail -20
ls -1 "$OUT"/*.png 2>/dev/null
[ -f "$OUT/summary.json" ] || { echo "[ui-preview] summary.json 없음(코드 $CODE) — $LOG 확인" >&2; exit 2; }
if [ -n "$COMPARE" ] && [ "$CODE" = 0 ]; then
  # 파일명 꼬리 규칙: _<대상 ref>+<덮은 커밋…>(+옵션)
  TAG="$(git -C "$PROJ" rev-parse --short=7 HEAD)"
  for spec in "${OVERRIDES[@]+"${OVERRIDES[@]}"}"; do
    sha="$(echo "$spec" | cut -d: -f2 | cut -c1-7)"
    case "+$TAG+" in *"+$sha+"*) ;; *) TAG="$TAG+$sha" ;; esac
  done
  [ -z "$STAGE" ] || TAG="$TAG+$STAGE"
  [ -z "$SUMMARY_AT" ] || TAG="$TAG+$SUMMARY_AT"
  mkdir -p "$OUT/compare"
  for name in ${SCREENS//,/ }; do
    for png in "$OUT/${name}"_*_t2.png; do
      [ -f "$png" ] && python3 -I "$TOOL_DIR/compare.py" "$COMPARE" "$png" "$OUT/compare/$(basename "$png" _t2.png)_$TAG.png" "$TAG"
    done
  done
fi
exit "$CODE"
