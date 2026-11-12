#!/usr/bin/env bash
# 허브 하위 워커용 워크스페이스 생성(기본 sparse·detached). 출력: 경로
set -euo pipefail
REPO="${REPO:-$HOME/orca/LoveRevolutionMaker}"
WS_ROOT="${WS_ROOT:-$HOME/orca/workspaces/LoveRevolutionMaker}"
HUB="" NAME="" PATHS="" BRANCH="" FULL=0 BASE="origin/dev"
while [ $# -gt 0 ]; do case "$1" in
  --hub) HUB="$2"; shift 2;; --name) NAME="$2"; shift 2;; --paths) PATHS="$2"; shift 2;;
  --branch) BRANCH="$2"; shift 2;; --base) BASE="$2"; shift 2;; --full) FULL=1; shift;;
  *) echo "알 수 없는 인자: $1" >&2; exit 2;; esac; done
: "${HUB:?--hub 필요}" "${NAME:?--name 필요}"
# 허브당 하위 작업 공간 상한(설정은 이 블록 한 곳). 넘으면 그 허브 하위만 ws_gc 로 1회 정리하고, 그래도 넘으면 경고·기록(생성은 계속)
CFG_HUB_MAX="${LRM_HUB_MAX_CHILDREN:-10}"
CFG_WS_GC="${LRM_WS_GC:-$HOME/.config/lrm-hubs/ws_gc.sh}"
CFG_GC_TIMEOUT="${LRM_HUB_GC_TIMEOUT:-60}"   # 상한 정리에 줄 제한 시간(초). 넘기면 정리를 끊고 생성을 계속함
CFG_GC_GRACE="${LRM_HUB_GC_GRACE:-5}"        # 시간 초과 때 TERM 뒤 KILL 까지 유예(초)
CFG_HUB_REPORT="${LRM_HUB_REPORT:-$HOME/.config/lrm-hubs/hub_report.py}"
count_kids() { # 허브 경로 → 등록된 하위 수. 조회 실패 시 빈 값
  orca worktree list --limit 1000 --json 2>/dev/null | jq -r --arg h "$1" \
    '[.result.worktrees[].id] as $ids | [.result.worktrees[] | select(.path == $h) | (.childWorktreeIds // []) | map(select(. as $c | $ids | index($c))) | length][0] // empty' 2>/dev/null || true
}
kids=$(count_kids "$HUB")
if [ -n "$kids" ] && [ "$kids" -ge "$CFG_HUB_MAX" ]; then
  echo "하위 작업 공간 ${kids}개(상한 ${CFG_HUB_MAX}) — 이 허브 하위만 정리 1회 실행" >&2
  gc_rc=0
  # 제한 시간 안에 못 끝나면 정리기와 그 자식 프로세스 그룹 전체를 종료(TERM 뒤 그룹이 비지 않고 유예가 지나면 KILL). TERM 을 무시하는 자식도 남기지 않음. 정리 실패·시간 초과여도 생성은 계속
  python3 -c 'import os, signal, subprocess, sys, time
timeout, grace = float(sys.argv[1]), float(sys.argv[2])
p = subprocess.Popen(sys.argv[3:], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
pg = p.pid  # start_new_session: 정리기와 자식이 한 프로세스 그룹
def alive():
    try:
        os.killpg(pg, 0)
        return True
    except (ProcessLookupError, PermissionError):  # macOS 는 좀비만 남은 그룹에 EPERM 을 돌려줌
        return False
rc = 0
try:
    rc = p.wait(timeout)
except subprocess.TimeoutExpired:
    rc = 124
p.poll()  # 종료한 직계 자식을 거둬 좀비로 남지 않게 함
if alive():  # 시간 초과거나 자식이 남은 경우: TERM 뒤 그룹이 비길 기다리고, 유예가 지나도 남으면 그룹 전체 KILL
    os.killpg(pg, signal.SIGTERM)
    end = time.time() + grace
    while alive() and time.time() < end:
        p.poll()
        time.sleep(0.1)
    if alive():
        os.killpg(pg, signal.SIGKILL)
sys.exit(rc)' "$CFG_GC_TIMEOUT" "$CFG_GC_GRACE" bash "$CFG_WS_GC" --parent "$HUB" --apply --quiet --no-slack || gc_rc=$?
  if [ "$gc_rc" = 124 ]; then echo "⚠️ 정리기가 ${CFG_GC_TIMEOUT}초 안에 끝나지 않아 중단 — 생성은 계속(~/orca/archive/ws_gc.log 확인)" >&2
  elif [ "$gc_rc" != 0 ]; then echo "⚠️ 정리기 실행 실패(종료 ${gc_rc}) — ~/orca/archive/ws_gc.log 확인" >&2; fi
  kids=$(count_kids "$HUB")
  if [ -n "$kids" ] && [ "$kids" -ge "$CFG_HUB_MAX" ]; then
    echo "⚠️ 정리 뒤에도 하위 작업 공간 ${kids}개(상한 ${CFG_HUB_MAX}) — 남길 것만으로 초과. 끝난 워커는 release_ws.sh 로 반납할 것" >&2
    hub_slug="$(basename "$HUB")"; hub_slug="${hub_slug#coord-}"; hub_slug="${hub_slug%-hub}"
    python3 "$CFG_HUB_REPORT" add --hub "$hub_slug" --task 없음 --kind block --text "하위 작업 공간 ${kids}개, 남길 것만으로 ${CFG_HUB_MAX}개 초과" >/dev/null 2>&1 || echo "⚠️ 허브 보고 기록 실패(생성은 계속)" >&2
  fi
fi
DIR_NAME="$(printf '%s' "${BRANCH:-$NAME}" | tr '/ []' '----' | tr -s '-' | sed 's/^-//;s/-$//')"
P="$WS_ROOT/$DIR_NAME"
git -C "$REPO" fetch -q origin 2>/dev/null || git -C "$REPO" -c credential.helper= -c credential.helper="!gh auth git-credential" fetch -q https://github.com/Basakansoft/LoveRevolutionMaker.git "+refs/heads/*:refs/remotes/origin/*" || echo "⚠️ fetch 실패 — 로컬 origin/* 기준으로 계속" >&2
if [ -n "$BRANCH" ]; then ARGS=(-b "$BRANCH"); else ARGS=(--detach); fi
if [ "$FULL" = 1 ]; then
  git -C "$REPO" worktree add -q "${ARGS[@]}" "$P" "$BASE"
else
  : "${PATHS:?sparse 는 --paths 필요}"
  GIT_LFS_SKIP_SMUDGE=1 git -C "$REPO" worktree add -q --no-checkout "${ARGS[@]}" "$P" "$BASE"
  # shellcheck disable=SC2086
  git -C "$P" sparse-checkout set --no-cone $PATHS
  GIT_LFS_SKIP_SMUDGE=1 git -C "$P" checkout -q
fi
orca worktree set --worktree "path:$P" --display-name "$NAME" --parent-worktree "path:$HUB" --json >/dev/null
echo "$P"

# 허브·워커는 기억 플러그인 끔(메모리 절약, 11-05). 메인 체크아웃만 켬
mkdir -p "$P/.claude" && python3 -c "import json,os,sys;f=sys.argv[1];d=json.load(open(f)) if os.path.exists(f) else {};d.setdefault(\"enabledPlugins\",{})[\"openviking-memory@openviking\"]=False;dn=d.setdefault(\"permissions\",{}).setdefault(\"deny\",[]);[dn.append(r) for r in [\"Read(/$HOME/.config/lrm-hubs/slack-bot.env)\",\"Bash(*slack-bot.env*)\",\"Bash(*slack_notify.sh*)\"] if r not in dn];json.dump(d,open(f,\"w\"),indent=2)" "$P/.claude/settings.local.json"
