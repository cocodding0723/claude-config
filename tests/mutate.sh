#!/usr/bin/env bash
# 변이 검증: 저장소 사본에 결함을 하나씩 심고 tests/install/run.sh 가 실패하는지 확인한다.
# ONLY="M3 M6" 처럼 일부만 실행 가능. 검출 못 한 변이가 있으면 종료 코드 1. 원본 작업 트리는 바꾸지 않고 임시 사본에서만 실행함.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd -P)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/cc-mutate.XXXXXX")
trap 'rm -rf "${WORK:?}"' EXIT
MISSED=0

fresh_copy() {
  rm -rf "${WORK:?}/repo"
  mkdir -p "$WORK/repo"
  (cd "$REPO" && tar -cf - --exclude='./.git/objects/pack' . 2>/dev/null) | (cd "$WORK/repo" && tar -xf -)
}

# 사본에 변이 적용(file, perl 치환식). 실제로 파일이 바뀌지 않으면 변이 정의 오류
mutate() {
  local file="$1" expr="$2"
  cp "$WORK/repo/$file" "$WORK/before"
  perl -0pi -e "$expr" "$WORK/repo/$file"
  if cmp -s "$WORK/before" "$WORK/repo/$file"; then echo "변이 정의 오류: $file 가 바뀌지 않음" >&2; return 1; fi
}

run_case() {
  local id="$1" desc="$2" file="$3" expr="$4" out failed
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $id "* ]]; then return; fi
  fresh_copy
  if ! mutate "$file" "$expr"; then printf '| %s | %s | 정의 오류 | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); return; fi
  out=$("$WORK/repo/tests/install/run.sh" 2>&1); local rc=$?
  failed=$(echo "$out" | grep '^FAIL' | head -2 | sed 's/^FAIL  //' | tr '\n' ';' | cut -c1-90)
  if [ "$rc" != 0 ]; then printf '| %s | %s | 검출 | %s |\n' "$id" "$desc" "$failed"
  else printf '| %s | %s | **미검출** | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); fi
}

fresh_copy
base=$("$WORK/repo/tests/install/run.sh" 2>&1); brc=$?
if [ "$brc" != 0 ]; then echo "기준 실행이 실패함. 변이 검증 불가:"; echo "$base" | grep '^FAIL'; exit 2; fi
echo "기준(무변이): 통과"
echo
echo '| 변이 | 내용 | 결과 | 먼저 실패한 검사 |'
echo '|---|---|---|---|'

run_case M1 "훅 병합을 배열 덮어쓰기(.[0] * .[1])로" merge/merge.jq 's/\A.*\z/.[0] * .[1]\n/s'
run_case M2 "Orca 상태줄을 추출하지 않음" merge/orca-keep.jq 's/then \{ statusLine \}/then {}/'
run_case M3 "이벤트 안 훅 순서 뒤집기(Orca 가 내 훅 앞)" merge/merge.jq 's/\(\(\$x\[\$k\] \/\/ \[\]\) \+ \(\$y\[\$k\] \/\/ \[\]\)\)/((\$y[\$k] \/\/ []) + (\$x[\$k] \/\/ []))/'
run_case M4 "중복 제거 제거" merge/merge.jq 's/ \| dd\)\)\n/))\n/'
run_case M5 "STATE_DIR 을 저장소 쪽 위치로 되돌림" claude/hooks/lib/common.sh 's/STATE_DIR="\$\{CLAUDE_HOOKS_STATE:-\$HOME\/\.claude\/hooks\/state\}"/STATE_DIR="\$HOOKS_DIR\/state"/'
run_case M6 "백업 이동 실패를 무시" install.sh 's/mv "\$t" "\$stored" \|\| die "[^"]*"/mv "\$t" "\$stored" || true/'
run_case M7 "복원이 백업을 mv 로 소모" install.sh 's/cp -a "\$stored" "\$tmp" \|\| die/mv "\$stored" "\$tmp" || die/'
run_case M8 "복원 전 백업 존재 검사 제거" install.sh 's/\|\| die "복원 중단: 백업 원본 없음[^"]*"/|| true/'
run_case M9 "render 자리표시 치환 생략" install.sh 's/content=\$\{content\/\/"__\$\{k\}__"\/\$v\}/:/'
run_case M10 "복원이 새로 만든 폴더를 남김" install.sh 's/rmdir "\$HOME\/\$dst" 2>\/dev\/null/false/'
run_case M11 "복원 대상 백업 manifest 에서 new 항목 누락" install.sh 's/mark_new\(\) \{ ensure_backup; record new "\$1"; \}/mark_new() { :; }/'
run_case M12 "템플릿에 실명 패턴 혼입" hubs/roles/role-vfx.txt.tmpl 's/\A/정''오 /'
run_case M13 "값 파일의 공백뿐인 값을 통과시킴" install.sh "s/ \\| tr -d '\\[:space:\\]'//"
run_case M14 "patsub_replacement 방어 제거" install.sh 's/^shopt -u patsub_replacement.*\n//m'
run_case M15 "라이브 우선 중단 제거" install.sh 's/\[ "\$ALLOW_LIVE_DIFF" = 1 \] && return 0/return 0/'
run_case M16 "라이브 비교에서 의도 치환 규칙을 모든 파일에 면제" install.sh 's/! cmp -s "\$r" "\$l"; fi/false; fi/'
run_case M17 "links.tsv 에서 스킬 파일 1줄 누락" links.tsv 's/^skills\/orca-hub-create\/scripts\/create_worker_ws.sh\t[^\n]*\n//m'

echo
if [ "$MISSED" = 0 ]; then echo "변이 17개 모두 검출"; else echo "미검출·오류 $MISSED 개"; exit 1; fi
