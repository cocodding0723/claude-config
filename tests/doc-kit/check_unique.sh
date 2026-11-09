#!/usr/bin/env bash
# doc-kit 본문의 프로젝트 고유값 점검. 값은 출력하지 않고 파일:줄만 낸다. 있으면 종료 코드 1.
# 사용: check_unique.sh [검사 폴더·파일, 기본 skills/doc-kit]
# 패턴 원문이 저장소 이름 스캔에 걸리지 않도록 조각으로 이어 붙임
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
TARGET="${1:-$REPO/skills/doc-kit}"
[ -e "$TARGET" ] || { echo "ERROR: 검사 대상 없음: $TARGET" >&2; exit 2; }

parts=(
  '정''오' '강찬''형' 'D0C14' 'coord-docs''-hub' '연애''혁명' 'LoveRevo''lution' 'LRM''-'
  'mach''amy' 'Job''Maker' 'origin/''dev' '90_정리''기록' 'Slack'' DM'
)
pattern=$(IFS='|'; echo "${parts[*]}")
# render 자리표시(__KEY__)가 doc-kit 에 있으면 설치기 값 파일에 묶임
pattern="$pattern|__[A-Z][A-Z_]*__"

# references/sources.md 는 선례 저작자·저장소 이름을 적는 것이 목적인 목록이라 이 점검에서 제외(다른 파일은 그대로 검사)
hits=$(grep -rnE "$pattern" "$TARGET" --exclude='*.pyc' --exclude='sources.md' --exclude-dir=__pycache__ | cut -d: -f1,2 | sed "s#^$TARGET/##")
if [ -n "$hits" ]; then
  echo "고유값 $(echo "$hits" | wc -l | tr -d ' ')건(파일:줄):"
  echo "$hits"
  exit 1
fi
echo "고유값 0건: $TARGET"
