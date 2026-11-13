#!/bin/bash
# 허브 코디네이터를 새 세션으로 재시작. 새 세션이 첫 출력을 낸 뒤에만 옛 세션(같은 허브 폴더의 다른 claude 코디네이터)을 정리함.
# 환경: HUB_ROOT(허브 상위 폴더), ROLE_FILE(역할 파일), COMMON_FILE(공통 규칙 파일, 빈 문자열이면 읽지 않음), READY_TIMEOUT(준비 대기 초, 기본 60)
# 기본값은 기존 허브용. 새 문서 허브는 HUB_ROOT=claude-config 워크스페이스 폴더, ROLE_FILE=$HUB/hubs/roles/role-doc-kit.txt, COMMON_FILE= 로 호출
set -eu

SRC="${BASH_SOURCE[0]}"
while [ -L "$SRC" ]; do T=$(readlink "$SRC"); case "$T" in /*) SRC="$T" ;; *) SRC="$(dirname "$SRC")/$T" ;; esac; done
HERE=$(cd "$(dirname "$SRC")" && pwd -P)
DUPS="${HUB_DUPS_PY:-$HERE/hub_dups.py}"

usage() {
  cat <<'E'
사용: restart_hub.sh [--dry-run] <옛 터미널> <허브 폴더명> <role 이름>
      restart_hub.sh --help

  새 코디네이터를 띄우고 첫 출력을 확인한 뒤, 옛 터미널과 같은 허브 폴더의 다른 claude 코디네이터를 닫는다.
  닫기 전에 각 터미널 마지막 화면을 ~/.config/lrm-hubs/hub-inbox/<slug>/ 에 보관한다(LRM_HUB_INBOX 로 변경).
  orchestration 워커(active·retained Dispatch)는 닫지 않는다. 새 세션이 준비되지 않으면 옛 세션을 그대로 둔다.
  --dry-run   띄우거나 닫지 않고 새로 띄울 명령 요약만 출력
출력: 새 터미널 핸들 한 줄(stdout)
종료 코드: 0 성공, 1 기동·준비 실패 또는 일부 정리 실패(옛 세션 유지), 2 인자·파일 오류
예: restart_hub.sh term_abc123 coord-test-qa-hub test
E
}

DRY=0
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --dry-run) DRY=1; shift ;;
esac
[ $# -eq 3 ] || { usage >&2; exit 2; }
OLD="$1"; HUB="${HUB_ROOT:-$HOME/orca/workspaces/LoveRevolutionMaker}/$2"; ROLE="${ROLE_FILE:-$HOME/.config/lrm-hubs/roles/role-$3.txt}"
COMMON="${COMMON_FILE-$HOME/.claude/skills/orca-hub-create/references/common-rules.txt}"
[ -f "$HUB/HANDOFF.md" ] || { echo "ERROR: $HUB/HANDOFF.md 없음 — 인계 파일을 먼저 작성" >&2; exit 2; }
[ -f "$DUPS" ] || { echo "ERROR: hub_dups.py 없음: $DUPS — HUB_DUPS_PY 로 경로 지정" >&2; exit 2; }
if [ -n "$COMMON" ]; then ORDER="1) 역할 파일 $ROLE 2) 공통 규칙 $COMMON 3) 인계 $HUB/HANDOFF.md"; else ORDER="1) 역할 파일 $ROLE 2) 인계 $HUB/HANDOFF.md"; fi
P="너는 이 허브의 코디네이터다(재시작). 순서: $ORDER 를 읽고 이어서 진행한다. 다 읽으면 메인 코디네이터에 '[허브→메인] 재시작 완료' 한 줄을 보낸다."

if [ "$DRY" = 1 ]; then
  echo "새 세션: orca agent launch --agent claude --worktree path:$HUB --model claude-sonnet-5-5 --effort medium --surface terminal"
  echo "준비 확인 뒤 정리 대상(옛 터미널 $OLD 포함, 워커 제외):"
  python3 "$DUPS" list --path "$HUB" || true
  exit 0
fi

# 옛 세션은 새 세션이 준비된 뒤에 닫음. 기동·준비가 실패하면 그대로 남김
NEW=$(orca agent launch --agent claude --worktree "path:$HUB" --model claude-sonnet-5-5 --effort medium --surface terminal --prompt "$P" --json | jq -r 'first(.. | objects | .handle? // empty | select(startswith("term_")))')
[ -n "$NEW" ] || { echo "ERROR: 새 세션 핸들을 얻지 못함 — 옛 세션 유지. orca agent launch 출력 확인" >&2; exit 1; }
python3 "$DUPS" ready --handle "$NEW" --timeout "${READY_TIMEOUT:-60}" || { echo "ERROR: 새 세션 $NEW 첫 출력 확인 실패 — 옛 세션 유지. 새 세션 상태를 확인하고 필요하면 직접 닫기" >&2; exit 1; }
python3 "$DUPS" clean --path "$HUB" --keep "$NEW" --also "$OLD" >&2 || { echo "ERROR: 옛 세션 정리 일부 실패 — 위 출력 확인. 새 세션은 $NEW" >&2; echo "$NEW"; exit 1; }
echo "$NEW"
