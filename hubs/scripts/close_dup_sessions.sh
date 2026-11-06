#!/bin/bash
# 12:27 Orca 재시작이 --resume 으로 되살린 옛 코디네이터 중복 세션 7개를 닫음.
# 현재 일하는 허브 터미널(24450f78 UI·c40b78b3 문서·2482e660 아키)은 목록에 없음.
set -u
DUPS=(
  845db708-b1ec-4d32-a6b2-c38d89e2404e   # UI 중복
  90854987-140f-494b-b055-3ddcf5eea6ea   # UI 중복
  3ba47227-5f38-4abc-908f-d59368a3b4e5   # UI 중복
  b2d549b6-a7bc-4432-beea-f5dc653ee1c0   # 문서 중복
  2ca84089-2047-4ebc-bcb7-18a9e629fddb   # 문서 중복
  48f7d4cd-b741-4dbc-96ed-f88b3efd3a5b   # 문서 중복
  7b09aed8-55be-4007-b30a-4ac3d14fc45b   # 아키 중복
)
for id in "${DUPS[@]}"; do
  t="term_$id"
  title=$(orca terminal show --terminal "$t" 2>/dev/null | sed -n 's/^title: //p')
  if [ -z "$title" ]; then
    echo "건너뜀(이미 없음): $id"
    continue
  fi
  if orca terminal close --terminal "$t" >/dev/null 2>&1; then
    echo "닫음: $title ($id)"
  else
    echo "실패: $title ($id)"
  fi
done
