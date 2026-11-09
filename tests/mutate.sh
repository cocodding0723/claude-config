#!/usr/bin/env bash
# 변이 검증: 저장소 사본에 결함을 하나씩 심고 검증 스크립트가 실패하는지 확인한다.
# 설치기 변이(M)는 tests/install/run.sh, doc-kit 변이(D)는 tests/doc-kit/{run,sample,rename,backup}.sh 가 맡음.
# ONLY="M3 M6" 처럼 일부만 실행 가능. 검출 못 한 변이가 있으면 종료 코드 1. 원본 작업 트리는 바꾸지 않고 임시 사본에서만 실행함.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd -P)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/cc-mutate.XXXXXX")
trap 'rm -rf "${WORK:?}"' EXIT
MISSED=0 RAN_M=0 RAN_D=0

fresh_copy() {  # 로컬 하네스 설정(.claude)은 사본에서 제외: 미추적이라 설치기 시험의 경로 검사에 걸림
  rm -rf "${WORK:?}/repo"
  mkdir -p "$WORK/repo"
  (cd "$REPO" && tar -cf - --exclude='./.git/objects/pack' --exclude='./.claude' . 2>/dev/null) | (cd "$WORK/repo" && tar -xf -)
}

# 사본에 변이 적용(file, perl 치환식). 실제로 파일이 바뀌지 않으면 변이 정의 오류
mutate() {
  local file="$1" expr="$2"
  cp "$WORK/repo/$file" "$WORK/before"
  perl -0pi -e "$expr" "$WORK/repo/$file"
  if cmp -s "$WORK/before" "$WORK/repo/$file"; then echo "변이 정의 오류: $file 가 바뀌지 않음" >&2; return 1; fi
}

exec_case() {
  local runner="$1" id="$2" desc="$3" file="$4" expr="$5" out failed
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $id "* ]]; then return; fi
  case "$id" in M*) RAN_M=$((RAN_M + 1)) ;; *) RAN_D=$((RAN_D + 1)) ;; esac
  fresh_copy
  if ! mutate "$file" "$expr"; then printf '| %s | %s | 정의 오류 | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); return; fi
  out=$("$runner" 2>&1); local rc=$?
  failed=$(echo "$out" | grep '^FAIL' | head -2 | sed 's/^FAIL  //' | tr '\n' ';' | cut -c1-90)
  if [ "$rc" != 0 ]; then printf '| %s | %s | 검출 | %s |\n' "$id" "$desc" "$failed"
  else printf '| %s | %s | **미검출** | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); fi
}
install_suite() { "$WORK/repo/tests/install/run.sh"; }
# doc-kit 검증 3종. 하나라도 실패하면 실패
dk_suite() {
  local rc=0 s
  for s in run.sh sample.sh rename.sh backup.sh; do "$WORK/repo/tests/doc-kit/$s" || rc=1; done
  return $rc
}
# ONLY 에 해당 접두(M·D)가 있을 때만 그 묶음의 기준 실행을 함
wants() { local w; [ -z "${ONLY:-}" ] && return 0; for w in $ONLY; do [[ "$w" == $1* ]] && return 0; done; return 1; }
run_case() { exec_case install_suite "$@"; }
run_dk() { exec_case dk_suite "$@"; }

fresh_copy
if wants M; then
  base=$(install_suite 2>&1); brc=$?
  if [ "$brc" != 0 ]; then echo "기준 실행(설치기)이 실패함. 변이 검증 불가:"; echo "$base" | grep '^FAIL'; exit 2; fi
fi
if wants D; then
  base=$(dk_suite 2>&1); brc=$?
  if [ "$brc" != 0 ]; then echo "기준 실행(doc-kit)이 실패함. 변이 검증 불가:"; echo "$base" | grep '^FAIL'; exit 2; fi
fi
echo "기준(무변이): 설치기·doc-kit 통과"
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

run_dk D1 "doc-kit 본문에 프로젝트 고유 이름 혼입" skills/doc-kit/SKILL.md 's/\A/정''오님 /'
run_dk D2 "doc-kit 참고 파일에 render 자리표시 혼입" skills/doc-kit/references/config.md 's/\A/__PERSON_FULL__ /'
run_dk D3 "새 허브 역할 파일에 티켓 접두 혼입" hubs/roles/role-doc-kit.txt 's/\A/LRM''-1 /'
run_dk D4 "프로젝트 지정(작업 지시)을 무시" skills/doc-kit/scripts/doc_config.py 's/    if arg:\n/    if False:\n/'
run_dk D5 "설정 문법 오류 종료 코드를 1 로" skills/doc-kit/scripts/doc_config.py 's/        return 2\n/        return 1\n/'
run_dk D6 "설정 없을 때 파일 이름 규칙 기본값 변경" skills/doc-kit/scripts/doc_config.py 's/"name_rule": "\{날짜\}-\{주제\}"/"name_rule": "{주제}"/'
run_dk D7 "check.py 고유명사 검사를 독자와 무관하게 상시 적용" skills/doc-kit/scripts/check.py 's/if audience == "기획자":/if True:/'
run_dk D8 "check.py 가 설정의 티켓 패턴을 무시" skills/doc-kit/scripts/check.py 's/    if ticket:\n/    if False:\n/'
run_dk D9 "전역 규칙에 전달 규칙 누출" claude/CLAUDE.md 's/\z/- 결과 문서는 DM 으로 직접 전달한다\n/'
run_dk D10 "역할 파일에서 직접 전달 금지 문구 변경" hubs/roles/role-doc-kit.txt 's/직접 보내지 않는다/직접 보낸다/'
run_dk D11 "SKILL.md 제목 형식을 역할 파일과 다르게" skills/doc-kit/SKILL.md 's/\[허브명→메인\] 📄/[허브명→메인] 📌/'
run_dk D12 "restart_hub 가 ROLE_FILE 을 무시" hubs/scripts/restart_hub.sh 's/\$\{ROLE_FILE:-[^}]*\}/\$HOME\/.config\/lrm-hubs\/roles\/role-\$3.txt/'
run_dk D13 "치환 스크립트가 백업 복사를 생략" migrate/rename_skills.sh 's/cp -p "\$path" "\$dir\/\$n\.orig" \|\| die "[^"]*"/:/'
run_dk D14 "치환 스크립트가 대상 존재 사전 확인을 생략" migrate/rename_skills.sh 's/\|\| die "대상 없음: /|| true # /'
run_dk D15 "치환 되돌리기가 치환 뒤 수정 파일도 덮어씀" migrate/rename_skills.sh 's/if \[ "\$cur" != "\$after" \]; then/if false; then/'
run_dk D16 "치환 --dry-run 이 실제로 치환" migrate/rename_skills.sh 's/dry\) do_dry ;;/dry) do_apply ;;/'
run_dk D17 "치환 규칙에서 house-style 참고 파일 규칙 제거" migrate/rename-rules.tsv 's/^house-style[^\n]*\n//m'
run_dk D18 "README 백업이 링크째 복사(cp -a)" README.md 's/cp -RpL ~\/\.claude\/skills/cp -a ~\/.claude\/skills/'
run_dk D19 "show 가 채널·받는 사람 값을 출력" skills/doc-kit/scripts/doc_config.py 's/PRIVATE_KEYS = \("recipients", "channel"\)/PRIVATE_KEYS = ()/'
run_dk D20 "check.py 가 숫자만의 7자리를 해시로 오검출" skills/doc-kit/scripts/check.py 's/\(\?=\[0-9\]\*\[a-f\]\)//'
run_dk D21 "치환 검사가 이름만 남은 없는 참고 파일을 놓침" migrate/rename_skills.sh 's/house-style|sources|md-spec/zz-none/g'
run_dk D22 "README 7번에서 폴더 안 링크 rm 줄 1개 누락" README.md 's/rm ~\/\.claude\/skills\/structure-change-doc\/templates\/doc\.html\n//'
run_dk D23 "README 7번이 실제 폴더를 -r 없이 rm" README.md 's/rm -r ~\/\.claude\/skills\/structure-change-doc\n/rm ~\/.claude\/skills\/structure-change-doc\n/'
run_dk D24 "create-pr 선례 경로를 config.md 로" skills/create-pr/SKILL.md 's/references\/sources\.md`\)/references\/config.md`)/'
run_dk D25 "치환 규칙에서 띄어 쓴 로→으로 제거" migrate/rename-rules.tsv 's/^\(\?<=doc-kit\) 로[^\n]*\n//m'
run_dk D26 "치환 규칙이 .md 없는 house-style 을 처리하지 않음" migrate/rename-rules.tsv 's/house-style\(\?:\\\.md\)\?\(\?!\[\\w-\]\)/house-style\\.md/'
run_dk D27 "role-docs 가 없는 house-style 이름을 가리킴" hubs/roles/role-docs.txt.tmpl 's/doc-kit style\.md/doc-kit house-style/'

echo
if [ $((RAN_M + RAN_D)) = 0 ]; then echo "실행한 변이가 없음: ONLY 가 어느 변이 번호와도 맞지 않음"; exit 1; fi
if [ "$MISSED" = 0 ]; then echo "실행한 변이 모두 검출(설치기 ${RAN_M}개·doc-kit ${RAN_D}개)"; else echo "미검출·오류 $MISSED 개(실행: 설치기 ${RAN_M}개·doc-kit ${RAN_D}개)"; exit 1; fi
