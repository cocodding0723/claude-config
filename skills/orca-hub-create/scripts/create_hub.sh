#!/usr/bin/env bash
# 코디네이터 허브 생성: sparse 워크스페이스 + Orca 등록 + Opus 코디네이터 기동·제출 확인
set -euo pipefail
REPO="${REPO:-$HOME/orca/LoveRevolutionMaker}"
WS_ROOT="${WS_ROOT:-$HOME/orca/workspaces/LoveRevolutionMaker}"
SLUG="" NAME="" PATHS="/docs/ /.claude/ /CLAUDE.md /AGENTS.md" ROLE_FILE="" RESUME="" MODEL="claude-opus-5-5" FORCE=0
BASE="origin/dev" BRANCH="" DRY=0
# 코디네이터 자동 압축 기준(토큰). 호출마다 지난 대화를 다시 읽는 비용을 줄이려 기본 20만. 0·off 면 설정에 넣지 않음. claude 최소값 100000
AUTOCOMPACT="${LRM_HUB_AUTOCOMPACT:-200000}"
while [ $# -gt 0 ]; do case "$1" in
  --slug) SLUG="$2"; shift 2;; --name) NAME="$2"; shift 2;; --paths) PATHS="$2"; shift 2;;
  --role-file) ROLE_FILE="$2"; shift 2;; --resume) RESUME="$2"; shift 2;; --model) MODEL="$2"; shift 2;; --force) FORCE=1; shift;;
  --base) BASE="$2"; shift 2;; --branch) BRANCH="$2"; shift 2;; --dry-run) DRY=1; shift;;
  *) echo "알 수 없는 인자: $1" >&2; exit 2;; esac; done
: "${SLUG:?--slug 필요}" "${NAME:?--name 필요}" "${ROLE_FILE:?--role-file 필요}"
# 공통 규칙은 설치 때 값을 채워 만든 파일이라 저장소에는 틀(.tmpl)만 있음. 저장소 사본으로 실행돼도 설치본을 읽고,
# 작업 공간을 만들기 전에 확인함(만든 뒤 실패하면 반쯤 만들어진 허브가 남음). 종료 3 은 feature_open.sh 가 '이미 실행 중'으로 읽으므로 4
COMMON_FILE="${COMMON_FILE:-}"
[ -z "$COMMON_FILE" ] || [ -f "$COMMON_FILE" ] || { echo "COMMON_FILE 로 지정한 공통 규칙 파일 없음: $COMMON_FILE" >&2; exit 4; }   # 오타면 다른 규칙으로 기동하지 않고 멈춤
case "$AUTOCOMPACT" in 0|off) AUTOCOMPACT=0;; *[!0-9]*|"") AUTOCOMPACT=bad;; *) [ "$AUTOCOMPACT" -ge 100000 ] || AUTOCOMPACT=bad;; esac
[ "$AUTOCOMPACT" != bad ] || { echo "LRM_HUB_AUTOCOMPACT 는 100000 이상 정수 또는 off: $LRM_HUB_AUTOCOMPACT" >&2; exit 2; }
[ -n "${PATHS// /}" ] || { echo "--paths 가 비어 있음 — 받을 경로를 하나 이상 지정" >&2; exit 2; }
for c in "$COMMON_FILE" "$(dirname "$0")/../references/common-rules.txt" "$HOME/.claude/skills/orca-hub-create/references/common-rules.txt"; do
  if [ -n "$c" ] && [ -f "$c" ]; then COMMON_FILE="$c"; break; fi
done
[ -f "$COMMON_FILE" ] || { echo "공통 규칙 파일 없음(common-rules.txt) — claude-config 의 install.sh 를 먼저 실행하거나 COMMON_FILE 로 지정" >&2; exit 4; }
HUB="$WS_ROOT/coord-${SLUG}-hub"
# --branch: 그 브랜치를 허브에 체크아웃(없으면 BASE 에서 생성). 기본은 BASE 에 detached
if [ -n "$BRANCH" ]; then
  if git -C "$REPO" show-ref -q --verify "refs/heads/$BRANCH"; then WT_OPT=(); WT_REF="$BRANCH"; else WT_OPT=(-b "$BRANCH"); WT_REF="$BASE"; fi
else WT_OPT=(--detach); WT_REF="$BASE"; fi
if [ "$DRY" = 1 ]; then
  [ -d "$HUB" ] && echo "hub=$HUB (있음, 생성 생략)" || echo "hub=$HUB 생성: git worktree add --no-checkout ${WT_OPT[*]:-} $WT_REF / sparse: $PATHS"
  echo "기동: orca agent launch --agent claude --worktree path:$HUB --model $MODEL --effort high (role: $ROLE_FILE)"
  exit 0
fi
if [ ! -d "$HUB" ]; then
  git -C "$REPO" fetch -q origin || echo "⚠️ fetch 실패 — 로컬 ${BASE} 로 진행"
  GIT_LFS_SKIP_SMUDGE=1 git -C "$REPO" worktree add -q --no-checkout ${WT_OPT[@]+"${WT_OPT[@]}"} "$HUB" "$WT_REF"
  read -r -a SPARSE <<<"$PATHS"   # 따옴표 없이 넘기면 "/*" 가 컴퓨터 최상위 폴더 목록으로 풀림
  git -C "$HUB" sparse-checkout set --no-cone "${SPARSE[@]}"
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
COMMON="$(cat "$COMMON_FILE")"
PROMPT="orca-coordinator 스킬로 이 허브의 ${NAME} 코디네이터를 맡아라.
${COMMON}
${RESUME:+이어받기: Orca 재시작 등으로 이전 세션이 끝났다. 먼저 다음을 읽고 이어서 진행: ${RESUME}
인계를 다 읽으면 사용자에게 묻거나 계획만 말하고 멈추지 말고, 인계의 '다음 할 일'부터 바로 진행한다. 승인이 필요한 일만 결정 카드로 묻는다.
}$(cat "$ROLE_FILE")"
# 허브·워커는 기억 플러그인 끔(메모리 절약, 11-05). 메인 체크아웃만 켬. 자동 압축 기준은 허브만(메인·워커 제외)
mkdir -p "$HUB/.claude" && python3 -c "import json,os,sys;f=sys.argv[1];d=json.load(open(f)) if os.path.exists(f) else {};a=int(sys.argv[2]);d.pop(\"autoCompactWindow\",None) if a==0 else d.__setitem__(\"autoCompactWindow\",a);d.setdefault(\"enabledPlugins\",{})[\"openviking-memory@openviking\"]=False;dn=d.setdefault(\"permissions\",{}).setdefault(\"deny\",[]);[dn.append(r) for r in [\"Read(/$HOME/.config/lrm-hubs/slack-bot.env)\",\"Bash(*slack-bot.env*)\",\"Bash(*slack_notify.sh*)\"] if r not in dn];json.dump(d,open(f,\"w\"),indent=2)" "$HUB/.claude/settings.local.json" "$AUTOCOMPACT"
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

