#!/usr/bin/env bash
# 코디네이터 허브 생성: sparse 워크스페이스 + Orca 등록 + Opus 코디네이터 기동·제출 확인
set -euo pipefail
REPO="${REPO:-$HOME/orca/LoveRevolutionMaker}"
WS_ROOT="${WS_ROOT:-$HOME/orca/workspaces/LoveRevolutionMaker}"
SLUG="" NAME="" PATHS="/docs/ /.claude/ /CLAUDE.md /AGENTS.md" ROLE_FILE="" RESUME="" MODEL="claude-opus-5-5" FORCE=0
while [ $# -gt 0 ]; do case "$1" in
  --slug) SLUG="$2"; shift 2;; --name) NAME="$2"; shift 2;; --paths) PATHS="$2"; shift 2;;
  --role-file) ROLE_FILE="$2"; shift 2;; --resume) RESUME="$2"; shift 2;; --model) MODEL="$2"; shift 2;; --force) FORCE=1; shift;;
  *) echo "알 수 없는 인자: $1" >&2; exit 2;; esac; done
: "${SLUG:?--slug 필요}" "${NAME:?--name 필요}" "${ROLE_FILE:?--role-file 필요}"
HUB="$WS_ROOT/coord-${SLUG}-hub"
if [ ! -d "$HUB" ]; then
  git -C "$REPO" fetch -q origin dev || echo "⚠️ fetch 실패 — 로컬 origin/dev 로 진행"
  GIT_LFS_SKIP_SMUDGE=1 git -C "$REPO" worktree add -q --no-checkout --detach "$HUB" origin/dev
  # shellcheck disable=SC2086
  git -C "$HUB" sparse-checkout set --no-cone $PATHS
  GIT_LFS_SKIP_SMUDGE=1 git -C "$HUB" checkout -q
fi
# 이미 살아 있는 코디네이터가 있으면 중복 기동 방지
if [ -d "$HUB" ] && [ "$FORCE" = 0 ]; then
  LIVE=$(orca terminal list --worktree "path:$HUB" --json | jq '[.result.terminals[]? | select(.orphaned|not) | select(.connected)] | length')
  if [ "${LIVE:-0}" -gt 0 ]; then
    echo "중단: $HUB 에 살아 있는 터미널 ${LIVE}개. 중복 코디네이터를 막으려 기동하지 않음(의도면 --force)" >&2; exit 3
  fi
fi
orca worktree set --worktree "path:$HUB" --display-name "[관리] ${NAME} 코디네이터" \
  --parent-worktree "path:$REPO" --comment "sparse 관리 전용. ${NAME}" --json >/dev/null
COMMON="$(cat "$(dirname "$0")/../references/common-rules.txt")"
PROMPT="orca-coordinator 스킬로 이 허브의 ${NAME} 코디네이터를 맡아라.
${COMMON}
${RESUME:+이어받기: Orca 재시작 등으로 이전 세션이 끝났다. 먼저 다음을 읽고 이어서 진행: ${RESUME}
}$(cat "$ROLE_FILE")"
# 허브·워커는 기억 플러그인 끔(메모리 절약, 11-05). 메인 체크아웃만 켬
mkdir -p "$HUB/.claude" && python3 -c "import json,os,sys;f=sys.argv[1];d=json.load(open(f)) if os.path.exists(f) else {};d.setdefault(\"enabledPlugins\",{})[\"openviking-memory@openviking\"]=False;dn=d.setdefault(\"permissions\",{}).setdefault(\"deny\",[]);[dn.append(r) for r in [\"Read(/$HOME/.config/lrm-hubs/slack-bot.env)\",\"Bash(*slack-bot.env*)\",\"Bash(*slack_notify.sh*)\"] if r not in dn];json.dump(d,open(f,\"w\"),indent=2)" "$HUB/.claude/settings.local.json"
H=$(orca agent launch --agent claude --worktree "path:$HUB" --model "$MODEL" --effort high \
  --surface terminal --prompt "$PROMPT" --json | jq -r 'first(.. | objects | .handle? // empty | select(startswith("term_")))')
: "${H:?agent launch 결과에서 터미널 handle 을 찾지 못함}"
# 붙여넣기만 되고 제출 안 되는 경우 대비: 최대 5회 확인. 진행 표시(esc to interrupt·Skill 호출)만 제출로 인정
SUBMITTED=0
for _ in 1 2 3 4 5; do
  sleep 3
  T=$(orca terminal read --terminal "$H" --json | jq -r '(.result.terminal.tail // [])[-6:] | join("\n")')
  if printf '%s' "$T" | grep -qE 'Skill\(|esc to interrupt'; then SUBMITTED=1; break; fi
  if printf '%s' "$T" | grep -q 'paste again'; then orca terminal send --terminal "$H" --enter --json >/dev/null; fi
done
[ "$SUBMITTED" = 1 ] || echo "경고: 프롬프트 제출을 확인하지 못함. orca terminal send --terminal $H --enter 로 확인" >&2
echo "hub=$HUB size=$(du -sh "$HUB" | cut -f1) terminal=$H submitted=$SUBMITTED"

