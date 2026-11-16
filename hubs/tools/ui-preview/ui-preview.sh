#!/usr/bin/env bash
#
# UI 화면 단독 미리보기. 부팅·로그인·네트워크 없이 화면 프리팹 하나를 배치모드로 띄워 PNG 로 남긴다.
#
#   bash Tools/verify/ui-preview.sh <화면|all>[,<화면>…] [가로x세로 …] [--stage <오토마 스테이지 Id>] [--bundle] [--timeout <초>]
#   예) bash Tools/verify/ui-preview.sh automa-daily-summary
#       bash Tools/verify/ui-preview.sh all 1080x1920 1440x3120
#
# 실행: PROJ=<게임 작업 공간> bash <이 폴더>/ui-preview.sh <화면> … (게임 작업 공간 안에서 실행하면 PROJ 생략)
# 출력: TestResults/ui-preview/<화면>_<가로>x<세로>_t0|t1|t2.png (연출 시작·절반·끝), summary.json, unity.log
# 데이터: 로컬 게임 데이터 저장소(build/game-data-local, 시트 고정본으로 생성) + 데브 번역 표. 없으면 준비 명령을 안내하고 종료.
# 에디터가 이 프로젝트를 열고 있으면 build/ui-preview-project 에 APFS 복제본을 만들어 그쪽에서 배치로 돈다(에디터 무관).
# 종료: 0 성공 / 1 캡처 실패 / 2 실행 불가(인자·데이터·Unity 없음·타임아웃)
#
set -uo pipefail

# PROJ: 대상 Unity 프로젝트(게임 저장소 작업 공간). 기본은 현재 디렉터리의 git 최상위
PROJ="${PROJ:-$(git rev-parse --show-toplevel 2>/dev/null)}"
UNITY="${UNITY:-/Applications/Unity/Hub/Editor/$(sed -n 's/^m_EditorVersion: //p' "$PROJ/ProjectSettings/ProjectVersion.txt")/Unity.app/Contents/MacOS/Unity}"
TOOL_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="$TOOL_DIR/Editor"
INSTALL_REL="Assets/Tests/Editor/UiPreview"
OUT="$PROJ/TestResults/ui-preview"
CLONE="$PROJ/build/ui-preview-project"
STORE="build/game-data-local"
TIMEOUT=600

screen_names() {
  grep -rhoE 'PreviewScreen\("[a-z0-9-]+"' "$REGISTRY" "$TOOL_DIR/Editor-bundle" 2>/dev/null | sed -E 's/.*\("([^"]+)"/\1/' | tr '\n' ' '
}

usage() {
  awk 'NR > 1 && !/^#/ { exit } NR > 1' "$0"
  echo "#   가능한 화면: all $(screen_names)"
}

SCREENS=""; SIZES=(); STAGE=""; BUNDLE=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --bundle) BUNDLE=1 ;;
    --stage) shift; STAGE="${1:?--stage 값 필요}" ;;
    --timeout) shift; TIMEOUT="${1:?--timeout 값 필요}" ;;
    [0-9]*x[0-9]*) SIZES+=("$1") ;;
    -*) echo "[ui-preview] 알 수 없는 인자: $1 — -h 로 사용법 확인" >&2; exit 2 ;;
    *) [ -z "$SCREENS" ] || { echo "[ui-preview] 화면은 하나의 인자에 쉼표로 묶음: $SCREENS,$1" >&2; exit 2; }; SCREENS="$1" ;;
  esac
  shift
done
[ -n "$SCREENS" ] || { usage >&2; exit 2; }

KNOWN=" all $(screen_names)"
for name in ${SCREENS//,/ }; do
  case "$KNOWN " in *" $name "*) ;; *) echo "[ui-preview] 알 수 없는 화면: $name — 가능:$KNOWN" >&2; exit 2 ;; esac
done
[ -x "$UNITY" ] || { echo "[ui-preview] Unity 없음: $UNITY — UNITY 환경 변수로 경로 지정" >&2; exit 2; }
[ -f "$PROJ/$STORE/manifest.json" ] || {
  echo "[ui-preview] 로컬 게임 데이터 없음: $STORE — node Tools/game-data/local/refresh.mjs --baseline pin 실행 후 재시도" >&2
  exit 2
}

# 에디터가 이 프로젝트를 열고 있으면 Library 락 때문에 같은 경로로는 배치를 못 띄움 → 복제본에서 실행
RUN_PROJ="$PROJ"
if pgrep -f -- "-projectPath $PROJ( |\$)" >/dev/null 2>&1; then
  RUN_PROJ="$CLONE"
  if pgrep -f -- "-projectPath $CLONE( |\$)" >/dev/null 2>&1; then
    echo "[ui-preview] 복제본도 다른 Unity 가 사용 중: $CLONE — 그 실행이 끝난 뒤 재시도" >&2
    exit 2
  fi
  mkdir -p "$CLONE"
  [ -d "$CLONE/Library" ] || { [ -d "$PROJ/Library" ] && cp -cR "$PROJ/Library" "$CLONE/Library"; }
  for dir in Assets Packages ProjectSettings "$STORE" Tools/localization/dev-preview; do
    mkdir -p "$CLONE/$dir"
    rsync -a --delete --exclude 'Temp/' "$PROJ/$dir/" "$CLONE/$dir/" || { echo "[ui-preview] 복제 실패: $dir" >&2; exit 2; }
  done
  echo "[ui-preview] 에디터가 열려 있어 복제본에서 실행: $CLONE"
fi

# Library 가 없는 새 작업 공간은 메인 체크아웃 Library 를 APFS 클론(메인은 읽기만)
if [ ! -d "$RUN_PROJ/Library" ]; then
  MAIN=$(git -C "$PROJ" worktree list --porcelain | sed -n '1s/^worktree //p')
  if [ -n "$MAIN" ] && [ "$MAIN" != "$RUN_PROJ" ] && [ -d "$MAIN/Library" ]; then
    echo "[ui-preview] Library 클론: $MAIN/Library"
    cp -cR "$MAIN/Library" "$RUN_PROJ/Library" || { echo "[ui-preview] Library 클론 실패" >&2; exit 2; }
  fi
fi

# 도구 코드를 실행 동안만 대상 프로젝트에 넣고 끝나면 지움(게임 저장소 커밋 0)
INSTALL="$RUN_PROJ/$INSTALL_REL"
[ ! -e "$INSTALL" ] || { echo "[ui-preview] 이미 있음: $INSTALL — 이전 실행 잔여물이면 지우고 재시도" >&2; exit 2; }
cleanup() { rm -rf -- "${INSTALL:?}" "${INSTALL:?}.meta"; }
trap cleanup EXIT
cp -R "$REGISTRY" "$INSTALL" || { echo "[ui-preview] 도구 복사 실패: $INSTALL" >&2; exit 2; }
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

"$UNITY" "${ARGS[@]}" &
UNITY_PID=$!
trap 'kill "$UNITY_PID" 2>/dev/null; cleanup' INT TERM
SECONDS=0
while kill -0 "$UNITY_PID" 2>/dev/null; do
  if [ "$SECONDS" -ge "$TIMEOUT" ]; then
    kill "$UNITY_PID" 2>/dev/null
    echo "[ui-preview] ${TIMEOUT}초 초과로 중단 — $LOG 확인" >&2
    exit 2
  fi
  sleep 1
done
wait "$UNITY_PID"; CODE=$?

grep -E '\[UiPreview\]|error CS' "$LOG" 2>/dev/null | tail -20
ls -1 "$OUT"/*.png 2>/dev/null
[ -f "$OUT/summary.json" ] || { echo "[ui-preview] summary.json 없음(코드 $CODE) — $LOG 확인" >&2; exit 2; }
exit "$CODE"
