#!/usr/bin/env bash
# 승인 경계 문구 검사: 공통 규칙·역할 파일이 새 경계(11-13 D-CFGAPPLY)를 담고 낡은 상충 문장이 없는지 확인.
set -uo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
COMMON="$REPO/skills/orca-hub-create/references/common-rules.txt.tmpl"
ROLES=$(ls "$REPO"/hubs/roles/role-*.txt "$REPO"/hubs/roles/role-*.tmpl)

DETAIL="$REPO/skills/orca-hub-create/references/rules-detail.md"
grep -q '^1\. 승인 경계: 사용자 직접 = dev/main 병합' "$COMMON" && grep -q '계획서 컨펌 1회' "$COMMON" && ok "짧은 공통 규칙에 승인 경계·계획 승인" || ko "공통 규칙" "1번 승인 경계·계획 승인 없음"
head -3 "$COMMON" | grep -q '사용자 손을 덜 탄다' && head -3 "$COMMON" | grep -q '속도보다 완성도' && ok "공통 규칙 맨 위 3줄에 접점 최소·완성도 원칙" || ko "공통 규칙 원칙" "맨 위 3줄에 없음"
grep -q '^## 승인 경계' "$DETAIL" && grep -q 'hub_report.py decide` Slack 카드' "$DETAIL" && ok "세부 규칙에 승인 경계 절·decide 카드 한 줄" || ko "세부 규칙" "절 없음"
grep -qE '선발급\(중복|draft 로 게시\(11-13 D-PRDRAFT2\)' "$COMMON" "$REPO/skills/orca-hub-create/SKILL.md" && ko "낡은 선발급·draft 무승인 문장 남음" || ok "낡은 선발급·draft 무승인 문장 없음"
for f in $ROLES; do
  n=$(basename "$f")
  [ "$n" = role-doc-kit.txt ] && continue
  if [ "$n" = role-feature.txt ]; then
    grep -q '계획 컨펌 1회' "$f" && grep -q 'dev/main 병합' "$f" && grep -q 'board-merge-check' "$f" && ok "$n: 접점·계획·머지 검사" || ko "$n" "접점·계획·머지 검사 문구 없음"
    continue
  fi
  grep -q '무승인 = .*사용자 검수 뒤 = 새 PR.*사용자 직접 = dev/main 병합, 라이브 Firebase' "$f" && grep -q 'decide` Slack 카드' "$f" && ok "$n: 새 경계·decide 카드" || ko "$n" "경계 문구 없음"
done
stale=$(grep -lE '여전히 금지:|권한 확대\(사용자 11-07|권한\(사용자 11-07' $ROLES "$COMMON" 2>/dev/null)
[ -z "$stale" ] && ok "낡은 상충 문장(11-07 권한·여전히 금지) 없음" || ko "낡은 문장 남음" "$stale"
grep -q 'push·PR (명시 승인 뒤)' "$REPO/skills/orca-main/references/workflow.md" && ko "orca-main workflow 낡은 push·PR 승인 표" "" || ok "orca-main workflow 낡은 승인 표 없음"
echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
