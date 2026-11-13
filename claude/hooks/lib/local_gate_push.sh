#!/usr/bin/env bash
# pre-push 본체: 게임 저장소 push 때 Tools/ci/local-gate.sh 강제. stdin = pre-push 입력(<로컬ref> <로컬sha> <원격ref> <원격sha>), $1 = 원격 이름.
# 통과 기록 = $LG_STORE/<커밋>.<기준>.json(기준의 / 는 __). 같은 커밋도 기준이 다르면 다시 검사. 사용자 11-13 D-LOCALGATE.
# 설정: hooks.localGate(on|off) · hooks.localGateTimeout(초, 기본 300) · hooks.localGateUnity(on). 네트워크 단계는 항상 --offline(캐시 기준, 스크립트가 not-run 처리)
set -u
DIR=$(cd "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")" && pwd -P)
. "$DIR/local_gate_repo.sh"
remote=${1:-origin}
top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
case "$(lg_state "$top")" in
  skip) exit 0;;
  off)  echo "local-gate 경고: hooks.localGate=off 로 꺼져 있음 — 게임 저장소는 게이트 통과가 필수(사용자 11-13 D-LOCALGATE). 끄기는 우회 경로가 아님" >&2; exit 0;;
esac

die() { printf 'local-gate %s\n' "$1" >&2; shift; for l in "$@"; do printf '  %s\n' "$l" >&2; done; exit 1; }
timeout_s=$(lg_cfg "$top" hooks.localGateTimeout); [[ ${timeout_s:-} =~ ^[0-9]+$ ]] || timeout_s=300
# 원격 이름이 아닌 URL 로 push 하면 origin 기준
git config --get "remote.$remote.url" >/dev/null 2>&1 || remote=origin
mkdir -p "$LG_STORE" 2>/dev/null || die "환경 오류: 기록 폴더 생성 불가($LG_STORE)" "조치: 폴더 권한 확인"

while read -r lref lsha rref rsha; do
  [[ ${lsha:-0} =~ ^0+$ ]] && continue          # 삭제 push
  [[ $rref == refs/heads/* ]] || continue        # 태그 등 브랜치 아닌 ref 제외
  short=${lsha:0:8}
  report="$LG_STORE/$lsha.last.json"
  if ! git cat-file -e "$lsha:$LG_SCRIPT" 2>/dev/null; then
    echo "local-gate 경고: $short 에 $LG_SCRIPT 없음(게이트 도입 전 브랜치) — 검사 없이 통과. dev 를 받아오면 다음 push 부터 검사" >&2; continue
  fi
  branch=${rref#refs/heads/}; base=
  for c in "$remote/$branch" origin/dev; do git rev-parse -q --verify "refs/remotes/$c" >/dev/null && { base=$c; break; }; done
  [ -n "$base" ] || die "환경 오류: 기준 브랜치($remote/$branch·origin/dev) 없음" "조치: git fetch origin dev"
  record="$LG_STORE/$lsha.${base//\//__}.json"
  if [ -f "$record" ]; then echo "local-gate: $short 통과 기록 있음(기준 $base) — 다시 돌리지 않음" >&2; continue; fi
  [ "$(git rev-parse HEAD)" = "$lsha" ] || die "거부: $short 는 체크아웃된 커밋이 아님(체크아웃 안 된 ref·--all·여러 브랜치 push 포함)" "조치: 그 브랜치를 체크아웃해 한 브랜치씩 'git push $remote HEAD:<브랜치>' 형식으로 push"
  git -C "$top" diff --quiet HEAD 2>/dev/null || die "거부: 커밋 안 된 변경이 있어 검사 결과가 push 할 커밋과 다를 수 있음" "조치: 커밋하거나 stash 뒤 다시(미추적 파일은 무시)"

  args=(bash "$top/$LG_SCRIPT" --base "$base" --report "$report" --offline)
  [ "$(lg_cfg "$top" hooks.localGateUnity)" = on ] || args+=(--no-unity)   # 이 Mac 에서 Unity 는 기본 제외
  errf=$(mktemp); rm -f "$report"
  echo "local-gate: $short 실행(기준 $base, 상한 ${timeout_s}초)" >&2
  set -m
  ( cd "$top" && exec env -u GIT_DIR -u GIT_INDEX_FILE -u GIT_WORK_TREE LOCAL_GATE_ID_BASE=origin/dev "${args[@]}" </dev/null >&2 2>"$errf" ) &
  pid=$!
  ( for ((i = 0; i < timeout_s; i++)); do sleep 1; kill -0 "$pid" 2>/dev/null || exit 0; done
    : > "$errf.timeout"; kill -TERM -- "-$pid" 2>/dev/null; sleep 2; kill -KILL -- "-$pid" 2>/dev/null ) &
  wd=$!
  wait "$pid"; rc=$?
  kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
  set +m
  cause=$(head -n1 "$errf" 2>/dev/null); timed=0; [ -f "$errf.timeout" ] && timed=1
  rm -f "$errf" "$errf.timeout"
  rpt="보고서: $report"; [ -f "$report" ] || rpt="보고서 없음"

  [ "$timed" = 1 ] && die "거부: 시간 초과(${timeout_s}초, Unity 제외)" "$rpt" "조치: hooks.localGateTimeout 을 늘리거나 게이트 스크립트를 직접 실행해 느린 단계 확인"
  case $rc in
    0) ;;
    1) die "실패: ${cause:-원인 출력 없음}" "$rpt";;
    *) die "환경 오류(종료 $rc): ${cause:-원인 출력 없음}" "$rpt" "조치: 환경 점검(도구 설치·git fetch origin dev) 후 재시도";;
  esac
  if [ -f "$report" ] && [ "$(jq -r '.tree_dirty // false' "$report" 2>/dev/null)" = true ]; then
    die "거부: 검사 중 작업 트리에 커밋 안 된 변경이 있었음(보고서 tree_dirty) — 통과 기록을 남기지 않음" "$rpt" "조치: 커밋하거나 stash 뒤 다시"
  fi
  if [ -f "$report" ]; then cp "$report" "$record.tmp"
  else jq -n --arg h "$lsha" --arg b "$base" '{head:$h,base:$b,steps:[],total_seconds:null}' > "$record.tmp"; fi
  mv "$record.tmp" "$record"
  echo "local-gate: $short 통과, 기록 $record" >&2
done
exit 0
