#!/bin/bash
# feature_project.sh 등록 + feature_open.sh 프로젝트 선택(--dry-run). 실제 표·저장소는 건드리지 않음
set -u
SCR="$(cd "$(dirname "$0")/../../hubs/scripts" && pwd)"
SB=$(mktemp -d); trap 'rm -rf "$SB"' EXIT
P=0 F=0; chk() { if eval "$2"; then P=$((P+1)); echo "PASS  $1"; else F=$((F+1)); echo "FAIL  $1 — ${3:-}"; fi; }
git init -q -b main "$SB/repo"; git -C "$SB/repo" commit -q --allow-empty -m s
mkdir -p "$SB/board"; export LRM_FEATURE_PROJECTS="$SB/p.tsv" WS_ROOT="$SB/ws" LRM_BOARD="$SB/board" LRM_SPRINT_DATE=20261118
printf '#key\trepo\tws_root\tticket\tticket_re\tbranch\tbase\nlrm\t%s\t-\trequired\t^LRM-[0-9]+$\tsprint\tmain\n' "$SB/repo" > "$SB/p.tsv"
FP="$SCR/feature_project.sh"; FO="$SCR/feature_open.sh"

"$FP" add jm --repo "$SB/repo" >/dev/null 2>&1; RC=$?
chk "add: --jira 없으면 거부(연동은 반드시 고름)" '[ "$RC" = 2 ]'
"$FP" add jm --repo "$SB/repo" --jira optional >/dev/null 2>&1; RC=$?
chk "add: optional 인데 티켓 형식 없으면 거부" '[ "$RC" = 2 ]'
"$FP" add jm --repo "$SB/repo" --jira optional --ticket-re '^JM-[0-9+$' >/dev/null 2>&1; RC=$?
chk "add: 틀린 정규식 거부" '[ "$RC" = 2 ]'
"$FP" add cfg --repo "$SB/repo" --jira none >/dev/null; RC=$?
chk "add: none 등록, 브랜치 기본 type" '[ "$RC" = 0 ] && grep -q "^cfg	.*	none	-	type	main$" "$SB/p.tsv"'
"$FP" add cfg --repo "$SB/repo" --jira none >/dev/null 2>&1; RC=$?
chk "add: 같은 키 다시 등록은 4" '[ "$RC" = 4 ]'
"$FP" add jm --repo "$SB/repo" --jira optional --ticket-re '^JM-[0-9]+$' >/dev/null
OUT=$("$FO" --ticket JM-3 --name t --slug t --dry-run 2>&1)
chk "open: 티켓 형식으로 프로젝트 고름" 'grep -q "프로젝트: jm (JM-3)" <<< "$OUT" && grep -q "브랜치: feature/t " <<< "$OUT"' "$OUT"
OUT=$("$FO" --project cfg --name t --slug t --type fix --dry-run 2>&1)
chk "open: none 프로젝트는 티켓 없이 <타입>/<작업명>" 'grep -q "브랜치: fix/t " <<< "$OUT"' "$OUT"
"$FO" --project cfg --ticket JM-1 --name t --slug t --dry-run >/dev/null 2>&1; RC=$?
chk "open: none 프로젝트에 티켓 주면 거부" '[ "$RC" = 2 ]'
"$FO" --name t --slug t --dry-run >/dev/null 2>&1; RC=$?
chk "open: 기본(lrm, required)은 티켓 없으면 거부" '[ "$RC" = 2 ]'
OUT=$("$FO" --ticket LRM-5 --name t --slug t --dry-run 2>&1)
chk "open: lrm 은 기존 스프린트 규칙 그대로" 'grep -q "브랜치: sprint-20261118/feat-LRM-5-t " <<< "$OUT"' "$OUT"
"$FO" --project nope --name t --slug t --dry-run >/dev/null 2>&1; RC=$?
chk "open: 없는 프로젝트 거부" '[ "$RC" = 2 ]'
echo "통과 $P · 실패 $F"; [ "$F" = 0 ]
