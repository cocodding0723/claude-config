#!/usr/bin/env bash
# 새 문서 허브 생성: claude-config 의 Orca 워크스페이스(전용 브랜치, sparse) + 코디네이터 기동·제출 확인.
# 기본은 실행이고 --dry-run 은 실행할 명령만 출력한다(파일·브랜치·워크스페이스·터미널을 만들지 않음).
set -euo pipefail

REPO="${DOC_HUB_REPO:-$HOME/orca/claude-config}"
WS_ROOT="${DOC_HUB_WS_ROOT:-$HOME/orca/workspaces/claude-config}"
SLUG="docs-hub"
BRANCH="chore/docs-hub"
BASE="main"
NAME="문서"
PATHS="/docs/ /hubs/roles/ /skills/doc-kit/"
MODEL="claude-opus-5-5"
PROJECT=""
DRY=0

usage() {
  cat <<'E'
사용: create_doc_hub.sh [--dry-run] [--project <이름>] [--model <모델>] [--base <브랜치>]

  claude-config 에 문서 허브 워크스페이스(브랜치 chore/docs-hub)를 만들고 코디네이터를 기동한다.
  --dry-run          실행할 명령만 출력. 아무것도 만들지 않음
  --project <이름>   코디네이터 프롬프트에 "프로젝트: <이름>" 을 적음(프로젝트별 설정 선택, 기본 방법)
  --model <모델>     코디네이터 모델(기본 claude-opus-5-5)
  --base <브랜치>    새 브랜치의 시작점(기본 main)

환경: DOC_HUB_REPO(기본 ~/orca/claude-config), DOC_HUB_WS_ROOT(기본 ~/orca/workspaces/claude-config)
종료 코드: 0 성공, 2 인자·사전 조건 오류, 3 이미 살아 있는 터미널이 있어 중단
예: create_doc_hub.sh --dry-run --project 시험A
E
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --project) PROJECT="${2:-}"; [ -n "$PROJECT" ] || die "--project 에 이름이 필요함"; shift 2 ;;
    --model) MODEL="${2:-}"; [ -n "$MODEL" ] || die "--model 에 값이 필요함"; shift 2 ;;
    --base) BASE="${2:-}"; [ -n "$BASE" ] || die "--base 에 브랜치가 필요함"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "알 수 없는 인자: $1" ;;
  esac
done

HUB="$WS_ROOT/$SLUG"
ROLE_REL="hubs/roles/role-doc-kit.txt"
HANDOFF_TMPL_REL="hubs/roles/handoff-template.md"

# 실행 명령 출력. dry-run 이면 실행하지 않음
run() {
  if [ "$DRY" = 1 ]; then printf '[dry-run] %s\n' "$*"; else "$@"; fi
}

if [ "$DRY" = 0 ]; then
  command -v orca >/dev/null || die "orca CLI 없음"
  command -v jq >/dev/null || die "jq 필요: brew install jq"
fi
if [ ! -e "$REPO/.git" ]; then
  if [ "$DRY" = 1 ]; then printf '[dry-run] 경고: 저장소 없음 %s\n' "$REPO" >&2; else die "저장소 없음: $REPO"; fi
fi

# 1. 워크스페이스. 폴더가 있으면 재사용
if [ -d "$HUB" ]; then
  printf '재사용: %s\n' "$HUB"
else
  run mkdir -p "$WS_ROOT"
  if [ "$DRY" = 1 ] || ! git -C "$REPO" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    run git -C "$REPO" worktree add -q --no-checkout -b "$BRANCH" "$HUB" "$BASE"
  else
    run git -C "$REPO" worktree add -q --no-checkout "$HUB" "$BRANCH"
  fi
  # shellcheck disable=SC2086
  run git -C "$HUB" sparse-checkout set --no-cone $PATHS
  run git -C "$HUB" checkout -q
fi

# 2. 인계 문서 틀. 이미 있으면 덮어쓰지 않음(HANDOFF.md 는 미추적)
if [ "$DRY" = 1 ] || [ ! -f "$HUB/HANDOFF.md" ]; then
  run cp "$HUB/$HANDOFF_TMPL_REL" "$HUB/HANDOFF.md"
fi

# 3. 이미 살아 있는 코디네이터가 있으면 중복 기동 방지
if [ "$DRY" = 0 ] && [ -d "$HUB" ]; then
  LIVE=$(orca terminal list --worktree "path:$HUB" --json | jq '[.result.terminals[]? | select(.orphaned|not) | select(.connected)] | length')
  if [ "${LIVE:-0}" -gt 0 ]; then
    printf '중단: %s 에 살아 있는 터미널 %s개. 중복 코디네이터를 막으려 기동하지 않음\n' "$HUB" "$LIVE" >&2
    exit 3
  fi
fi

# 4. Orca 표시 이름·부모 연결
run orca worktree set --worktree "path:$HUB" --display-name "[관리] ${NAME} 코디네이터" \
  --parent-worktree "path:$REPO" --comment "문서 허브. 프로젝트 비종속" --json

# 5. 코디네이터 기동. 이 허브는 프로젝트 공통 규칙 파일을 읽지 않고 역할 파일만 읽음
PROMPT="너는 문서 코디네이터다. 순서: 1) 역할 파일 $HUB/$ROLE_REL 2) 인계 $HUB/HANDOFF.md 를 읽고 시작한다. 문서 규칙은 doc-kit 스킬이 정본이다.${PROJECT:+
프로젝트: $PROJECT}"
if [ "$DRY" = 1 ]; then
  printf '[dry-run] orca agent launch --agent claude --worktree path:%s --model %s --effort high --surface terminal --prompt <아래>\n' "$HUB" "$MODEL"
  printf '[dry-run] 프롬프트:\n%s\n' "$PROMPT"
  printf '[dry-run] 제출 확인: 최대 5회 터미널 꼬리를 읽고 진행 표시가 없으면 Enter 재전송\n'
  exit 0
fi

H=$(orca agent launch --agent claude --worktree "path:$HUB" --model "$MODEL" --effort high \
  --surface terminal --prompt "$PROMPT" --json | jq -r 'first(.. | objects | .handle? // empty | select(startswith("term_")))')
: "${H:?agent launch 결과에서 터미널 handle 을 찾지 못함}"

# 붙여넣기만 되고 제출되지 않는 경우 대비. 진행 표시가 보이면 제출로 인정
SUBMITTED=0
for _ in 1 2 3 4 5; do
  sleep 3
  T=$(orca terminal read --terminal "$H" --json | jq -r '(.result.terminal.tail // [])[-6:] | join("\n")')
  if printf '%s' "$T" | grep -qE 'Skill\(|esc to interrupt'; then SUBMITTED=1; break; fi
  if printf '%s' "$T" | grep -q 'paste again'; then orca terminal send --terminal "$H" --enter --json >/dev/null; fi
done
[ "$SUBMITTED" = 1 ] || printf '경고: 프롬프트 제출을 확인하지 못함. orca terminal send --terminal %s --enter 로 확인\n' "$H" >&2
printf 'hub=%s terminal=%s submitted=%s\n' "$HUB" "$H" "$SUBMITTED"
printf '메인 코디네이터 허브 목록에 이 허브(%s) 추가 필요\n' "$HUB"
