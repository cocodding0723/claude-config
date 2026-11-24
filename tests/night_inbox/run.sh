#!/usr/bin/env bash
# night_inbox_report.py 시험. 가짜 hub_report(성공·실패)만 쓰고 실제 결재함·Slack 은 건드리지 않음
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P); R="$HERE/../../hubs/scripts/night_inbox_report.py"
SB=$(mktemp -d "${TMPDIR:-/tmp}/night-inbox.XXXXXX"); trap 'rm -rf "$SB"' EXIT
export LRM_NIGHT_INBOX="$SB/night-inbox.jsonl" LRM_HUB_REPORT="$SB/report.py"
PASS=0 FAIL=0
chk() { if eval "$2"; then PASS=$((PASS+1)); echo "PASS  $1"; else FAIL=$((FAIL+1)); echo "FAIL  $1 — ${3:-}"; fi; }
fake() { printf 'import sys,json\nopen("%s/calls","a").write(json.dumps(sys.argv[1:])+"\\n")\nsys.exit(%s)\n' "$SB" "$1" > "$LRM_HUB_REPORT"; }
row() { printf '{"at":"t","hook":"h","reason":"%s","cwd":"/w/%s"}\n' "$1" "$2" >> "$LRM_NIGHT_INBOX"; }

fake 0
python3 "$R"; chk "빈 결재함: 보고 없음·종료 0" '[ $? = 0 ] && [ ! -e "$SB/calls" ]'
row "git merge 확인 필요 (작업 브랜치로 origin/dev 받아오기만 확인 없이 허용됩니다)" a
row "git merge 확인 필요 (작업 브랜치로 origin/dev 받아오기만 확인 없이 허용됩니다)" b
row "새 PR 생성은 사용자 승인 뒤에만" a
echo 'broken' >> "$LRM_NIGHT_INBOX"
OUT=$(python3 "$R" --dry-run); chk "dry-run: 본문만, 결재함 그대로" '[ -s "$LRM_NIGHT_INBOX" ] && [ ! -e "$SB/calls" ] && printf "%s" "$OUT" | grep -q "3건"' "$OUT"
chk "본문 3줄·400자 이내, 많은 순 묶음" '[ "$(printf "%s\n" "$OUT" | wc -l | tr -d " ")" = 3 ] && [ ${#OUT} -le 400 ] && printf "%s" "$OUT" | grep -q "가장 많은 것: git merge.*2건"' "$OUT"
fake 1
python3 "$R" 2>/dev/null; RC=$?
chk "보고 실패: 종료 1·결재함 복구(다음 회차 재시도)" '[ "$RC" = 1 ] && [ "$(grep -c . "$LRM_NIGHT_INBOX")" = 4 ] && [ -z "$(ls "$SB/night-inbox" 2>/dev/null)" ]'
fake 0; rm -f "$SB/calls"
python3 "$R" >/dev/null; RC=$?
chk "보고 성공: info·main·첨부, 결재함 비움·보관 파일 남김" '[ "$RC" = 0 ] && [ ! -e "$LRM_NIGHT_INBOX" ] && [ "$(ls "$SB/night-inbox" | wc -l | tr -d " ")" = 1 ] && grep -q "\"info\"" "$SB/calls" && grep -q "\"--file\"" "$SB/calls" && grep -q "\"--auto\"" "$SB/calls"'
python3 "$R"; chk "다시 돌려도 중복 보고 없음" '[ "$(grep -c . "$SB/calls")" = 1 ]'
echo "----"; echo "통과 $PASS · 실패 $FAIL"; [ "$FAIL" = 0 ]
