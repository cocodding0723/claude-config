#!/bin/bash
# 허브 코디네이터를 새 세션으로 재시작. 사용: restart_hub.sh <옛 터미널> <허브 폴더명> <role 이름>
set -eu
OLD="$1"; HUB="${HUB_ROOT:-$HOME/orca/workspaces/LoveRevolutionMaker}/$2"; ROLE="$HOME/.config/lrm-hubs/roles/role-$3.txt"
[ -f "$HUB/HANDOFF.md" ] || { echo "HANDOFF.md 없음" >&2; exit 2; }
P="너는 이 허브의 코디네이터다(재시작). 순서: 1) 역할 파일 $ROLE 2) 공통 규칙 ~/.claude/skills/orca-hub-create/references/common-rules.txt 3) 인계 $HUB/HANDOFF.md 를 읽고 이어서 진행한다. 다 읽으면 메인 코디네이터에 '[허브→메인] 재시작 완료' 한 줄을 보낸다."
orca terminal close --terminal "$OLD" >/dev/null 2>&1 || true
orca agent launch --agent claude --worktree "path:$HUB" --model claude-sonnet-5-5 --effort medium --surface terminal --prompt "$P" --json | jq -r 'first(.. | objects | .handle? // empty | select(startswith("term_")))'
