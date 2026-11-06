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
