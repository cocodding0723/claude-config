#!/usr/bin/env bash
# 승인 경계 문구 검사: 공통 규칙·역할 파일이 새 경계(11-13 D-CFGAPPLY)를 담고 낡은 상충 문장이 없는지 확인.
set -uo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
COMMON="$REPO/skills/orca-hub-create/references/common-rules.txt.tmpl"
ROLES=$(ls "$REPO"/hubs/roles/role-*.txt "$REPO"/hubs/roles/role-*.tmpl)

grep -q '^## 승인 경계' "$COMMON" && grep -q 'hub_report.py decide` Slack 카드' "$COMMON" && ok "공통 규칙에 승인 경계 절·decide 카드 한 줄" || ko "공통 규칙" "절 없음"
for f in $ROLES; do
  n=$(basename "$f")
  [ "$n" = role-doc-kit.txt ] && continue
  grep -q '무승인 = .*사용자 검수 뒤 = 새 PR.*사용자 직접 = dev/main 병합, 라이브 Firebase' "$f" && grep -q 'decide` Slack 카드' "$f" && ok "$n: 새 경계·decide 카드" || ko "$n" "경계 문구 없음"
done
stale=$(grep -lE '여전히 금지:|권한 확대\(사용자 11-07|권한\(사용자 11-07' $ROLES "$COMMON" 2>/dev/null)
[ -z "$stale" ] && ok "낡은 상충 문장(11-07 권한·여전히 금지) 없음" || ko "낡은 문장 남음" "$stale"
grep -q 'push·PR (명시 승인 뒤)' "$REPO/skills/orca-main/references/workflow.md" && ko "orca-main workflow 낡은 push·PR 승인 표" "" || ok "orca-main workflow 낡은 승인 표 없음"
echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
