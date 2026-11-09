#!/usr/bin/env bash
# 옛 문서 스킬 이름 치환 도구(migrate/rename_skills.sh) 시험. 임시 사본·가짜 메모리 폴더에서만 실행하며 실제 파일은 바꾸지 않음.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

W=$(mktemp -d "${TMPDIR:-/tmp}/cc-dockit-rename.XXXXXX")
trap 'rm -rf "${W:?}"' EXIT
export HOME="$W/home"; mkdir -p "$HOME"

# 도구를 임시 저장소 사본에 놓고(REPO 가 스크립트 위치 기준) doc-kit 참고 파일만 흉내 냄
SB="$W/repo"; MEM="$W/mem"; BK="$W/backup"
mkdir -p "$SB/migrate" "$SB/skills/doc-kit/references" "$SB/claude" "$MEM"
cp "$REPO"/migrate/rename_skills.sh "$REPO"/migrate/rename_apply.pl "$REPO"/migrate/rename-rules.tsv "$SB/migrate/"
for f in style config explain report; do : > "$SB/skills/doc-kit/references/$f.md"; done
TOOL="$SB/migrate/rename_skills.sh"

printf '규칙은 tech-record/references/house-style.md 를 따른다.\n무관한 줄\n`explain-diff`·`tech-record`·`create-pr`·`work-report` 를 쓴다.\n' > "$SB/claude/a.md"
printf 'structure-change-doc 와 doc-coauthoring 사용\n' > "$MEM/m.md"
printf '이미 doc-kit 만 있다\n' > "$MEM/clean.md"
printf 'repo\tclaude/a.md\nmem\tm.md\nmem\tclean.md\n' > "$W/targets.tsv"
run() { "$TOOL" "$@" --targets "$W/targets.tsv" --memory-dir "$MEM" --backup-root "$BK"; }
sum() { shasum "$SB/claude/a.md" "$MEM/m.md" "$MEM/clean.md" | cut -d' ' -f1 | tr '\n' ' '; }

# ---- dry-run ----------------------------------------------------------------
before=$(sum)
out=$(run --dry-run 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(sum)" = "$before" ] && [ ! -e "$BK" ] && ok "dry-run: 파일·백업 무변경, 성공" || ko "dry-run 무변경" "rc=$rc"
echo "$out" | grep -qx 'CHANGE claude/a.md:1' && echo "$out" | grep -qx 'CHANGE claude/a.md:3' && ! echo "$out" | grep -q 'a.md:2' && echo "$out" | grep -qx 'CHANGE mem/m.md:1' && ok "dry-run: 바뀔 줄만 파일:줄로 출력(mem 표기 포함)" || ko "dry-run 줄 표시" "$(echo "$out" | tr '\n' '/')"
! echo "$out" | grep -q 'house-style\|structure-change-doc\|무관한' && ok "dry-run: 줄 내용은 출력하지 않음" || ko "내용 미출력" ""
echo "$out" | grep -q '요약: 바뀔 파일 2, 확인 필요 0, 없음 0' && ok "dry-run: 이미 치환된 파일(clean.md)은 제외·요약 일치" || ko "dry-run 요약" "$(echo "$out" | tail -1)"

# REVIEW: 치환 뒤 doc-kit 경로가 실재하지 않는 줄
printf 'tech-record/references/nope.md 참조\n' > "$MEM/m.md"
out=$(run --dry-run 2>&1); rc=$?
[ "$rc" = 1 ] && echo "$out" | grep -qx 'REVIEW mem/m.md:1' && ok "dry-run: 없는 doc-kit 경로는 REVIEW·종료 코드 1" || ko "REVIEW" "rc=$rc"
# REVIEW: 경로 접두 없이 옛 참고 파일 이름만 남은 줄도 doc-kit/references 에 있는지 확인(없는 파일을 가리키는 치환)
printf 'tech-record 의 같은 폴더 `md-spec.md` 를 본다\n' > "$MEM/m.md"
out=$(run --dry-run 2>&1); rc=$?
[ "$rc" = 1 ] && echo "$out" | grep -qx 'REVIEW mem/m.md:1' && ok "dry-run: 이름만 남은 없는 참고 파일(md-spec.md)은 REVIEW·종료 코드 1" || ko "접두 없는 참고 파일 REVIEW" "rc=$rc"
printf '같은 폴더 `house-style.md` 를 본다\n' > "$MEM/m.md"
out=$(run --dry-run 2>&1); rc=$?
[ "$rc" = 0 ] && echo "$out" | grep -qx 'CHANGE mem/m.md:1' && ok "dry-run: 이름만 적은 house-style.md 는 style.md 로 치환돼 REVIEW 없음" || ko "house-style 이름 치환" "rc=$rc"
printf 'structure-change-doc 와 doc-coauthoring 사용\n' > "$MEM/m.md"

# perl 실패는 빈 출력으로 묻지 않고 종료 코드 2 와 메시지로 알림
printf '(\t대치\n' > "$W/bad-rules.tsv"
out=$("$TOOL" --dry-run --rules "$W/bad-rules.tsv" --targets "$W/targets.tsv" --memory-dir "$MEM" --backup-root "$BK" 2>&1); rc=$?
[ "$rc" = 2 ] && echo "$out" | grep -q '치환 규칙 적용 실패' && ! echo "$out" | grep -q '^CHANGE' && ok "dry-run: 규칙 적용(perl) 실패는 CHANGE 없이 종료 코드 2·메시지" || ko "perl 실패 노출" "rc=$rc"

# ---- 대상 없음 ---------------------------------------------------------------
printf 'repo\tclaude/a.md\nmem\t없는파일.md\n' > "$W/targets-missing.tsv"
before=$(sum)
out=$("$TOOL" --apply --targets "$W/targets-missing.tsv" --memory-dir "$MEM" --backup-root "$BK" 2>&1); rc=$?
[ "$rc" = 2 ] && [ "$(sum)" = "$before" ] && [ ! -e "$BK" ] && ok "apply: 대상이 하나라도 없으면 아무것도 바꾸지 않고 종료 코드 2" || ko "대상 없음 중단" "rc=$rc"
out=$("$TOOL" --dry-run --targets "$W/targets-missing.tsv" --memory-dir "$MEM" --backup-root "$BK" 2>&1)
echo "$out" | grep -qx 'MISSING mem/없는파일.md' && ok "dry-run: 없는 대상은 MISSING" || ko "MISSING" ""

# ---- apply: 백업 후 치환 ------------------------------------------------------
cp -p "$SB/claude/a.md" "$W/a.orig"; cp -p "$MEM/m.md" "$W/m.orig"
out=$(run --apply 2>&1); rc=$?
dir=$(echo "$out" | sed -n 's/^백업: \([^ ]*\) .*/\1/p')
[ "$rc" = 0 ] && [ -d "$dir" ] && [ -s "$dir/manifest.tsv" ] && ok "apply: 백업 폴더·manifest 생성" || ko "apply 백업" "rc=$rc dir=$dir"
[ -f "$dir/1.orig" ] && [ -f "$dir/2.orig" ] && cmp -s "$dir/1.orig" "$W/a.orig" && cmp -s "$dir/2.orig" "$W/m.orig" && ok "apply: 백업 원본이 치환 전과 바이트 동일" || ko "백업 내용" ""
! grep -q 'tech-record\|structure-change-doc\|doc-coauthoring' "$SB/claude/a.md" "$MEM/m.md" && grep -q 'doc-kit/references/style.md' "$SB/claude/a.md" && ok "apply: 옛 이름이 doc-kit 으로 바뀜(경로 규칙 포함)" || ko "치환 결과" ""
[ "$(sed -n 2p "$SB/claude/a.md")" = '무관한 줄' ] && ok "apply: 이름이 없는 줄은 그대로" || ko "무관한 줄" ""
grep -qx '`doc-kit`·`create-pr` 를 쓴다.' "$SB/claude/a.md" && ok "apply: 나열 구절이 하나로 합쳐지고 중복 없음" || ko "나열 구절" "$(sed -n 3p "$SB/claude/a.md")"
# 조사: doc-kit 은 받침으로 끝나므로 로→으로, 가→이(이름 치환으로 틀어진 조사 교정)
printf '`explain-diff`로 쓴다\nwork-report 가 맡는다\n`tech-record` 가 맡는다\nwork-report 가능\n' > "$W/josa.md"
perl "$SB/migrate/rename_apply.pl" "$SB/migrate/rename-rules.tsv" < "$W/josa.md" > "$W/josa.out"
[ "$(cat "$W/josa.out")" = "$(printf '`doc-kit`으로 쓴다\ndoc-kit 이 맡는다\n`doc-kit` 이 맡는다\ndoc-kit 가능')" ] && ok "치환: 이름 뒤 조사 로→으로·가→이 교정(가능 같은 단어는 그대로)" || ko "조사 교정" "$(tr '\n' '/' < "$W/josa.out")"
# 띄어 쓴 조사, .md 없는 house-style, 선례 경로가 재적용에서도 같은 결과로 나옴(옛 문구 그대로 넣어 규칙만 시험)
printf '`explain-diff` 로 작성\nwork-report 로 작성\ndoc-kit 로그\n문서 스킬 규칙(explain-diff·tech-record house-style, 평서 ~다) 준수\n선례는 `~/.claude/skills/tech-record/references/sources.md` 다\n' > "$W/old.md"
perl "$SB/migrate/rename_apply.pl" "$SB/migrate/rename-rules.tsv" < "$W/old.md" > "$W/old.out"
[ "$(cat "$W/old.out")" = "$(printf '`doc-kit` 으로 작성\ndoc-kit 으로 작성\ndoc-kit 로그\n문서 스킬 규칙(doc-kit style.md, 평서 ~다) 준수\n선례는 `~/.claude/skills/doc-kit/references/sources.md` 다')" ] && ok "치환: 띄어 쓴 로→으로, .md 없는 house-style→style.md, 선례 경로→sources.md(로그 같은 단어는 그대로)" || ko "옛 문구 재적용" "$(tr '\n' '/' < "$W/old.out")"
cmp -s "$MEM/clean.md" <(printf '이미 doc-kit 만 있다\n') && ok "apply: 바뀔 것 없는 파일은 건드리지 않음" || ko "clean 무변경" ""
run --apply >/dev/null 2>&1; run --dry-run 2>&1 | grep -q '요약: 바뀔 파일 0' && ok "apply 반복: 이미 치환돼 바뀔 파일 0(멱등)" || ko "멱등" ""

# ---- revert --------------------------------------------------------------------
printf '치환 뒤 직접 고친 줄\n' >> "$MEM/m.md"
out=$("$TOOL" --revert "$dir" 2>&1); rc=$?
echo "$out" | grep -qx 'KEEP mem/m.md (치환 뒤 다시 바뀌어 건너뜀, 직접 확인)' && [ "$rc" = 1 ] && grep -q '치환 뒤 직접 고친 줄' "$MEM/m.md" && ok "revert: 치환 뒤 다시 바뀐 파일은 건너뛰고 알림" || ko "revert KEEP" "rc=$rc"
cmp -s "$SB/claude/a.md" "$W/a.orig" && ok "revert: 안 바뀐 파일은 원본으로 복원" || ko "revert 복원" ""
out=$("$TOOL" --revert "$dir" 2>&1); echo "$out" | grep -qx 'SKIP claude/a.md (이미 원본)' && ok "revert 반복: 이미 원본이면 건너뜀" || ko "revert 멱등" ""
rm -f "${dir:?}/1.orig"
"$TOOL" --revert "$dir" >/dev/null 2>&1; [ "$?" = 2 ] && ok "revert: 백업 원본이 없으면 아무것도 바꾸지 않고 종료 코드 2" || ko "revert 원본 없음" ""
"$TOOL" --revert "$W/없는폴더" >/dev/null 2>&1; [ "$?" = 2 ] && ok "revert: 백업 폴더가 아니면 종료 코드 2" || ko "revert 폴더 아님" ""
"$TOOL" --bad >/dev/null 2>&1; [ "$?" = 2 ] && ok "알 수 없는 인자는 종료 코드 2" || ko "인자 오류" ""

# ---- 실제 저장소 대상: 존재하지 않는 참고 경로·없는 대상이 없어야 함 ---------------------------
grep -E '^repo' "$REPO/migrate/rename-targets.tsv" > "$W/repo-targets.tsv"
out=$("$REPO/migrate/rename_skills.sh" --dry-run --targets "$W/repo-targets.tsv" --memory-dir "$MEM" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "저장소 대상 13곳: 없는 대상·없는 doc-kit 경로 0건" || ko "저장소 대상" "$(echo "$out" | grep -E 'REVIEW|MISSING' | head -3 | tr '\n' '/')"
# 저장소 13곳은 이미 치환된 상태: 규칙을 다시 적용해도 바뀌는 파일이 없고, 선례는 sources.md 를 가리킴
echo "$out" | grep -q '요약: 바뀔 파일 0, 확인 필요 0, 없음 0' && ok "저장소 대상 13곳: 규칙 재적용 시 바뀔 파일 0" || ko "저장소 대상 멱등" "$(echo "$out" | tail -1)"
grep -qF '선례는 `~/.claude/skills/doc-kit/references/sources.md`' "$REPO/skills/create-pr/SKILL.md" && ok "create-pr 의 선례 경로는 doc-kit/references/sources.md" || ko "create-pr 선례 경로" ""
! grep -rnE 'doc-kit`? 로([^가-힣]|$)|doc-kit house-style' "$REPO/claude" "$REPO/hubs" "$REPO/skills/create-pr" "$REPO/skills/work-brief" "$REPO/skills/catch-up" "$REPO/skills/pr-review-game" "$REPO/skills/pretty-code" "$REPO/skills/orca-hub-create" >/dev/null && ok "저장소 13곳에 'doc-kit 로'·'doc-kit house-style' 문구가 남지 않음" || ko "옛 조사·옛 파일명 문구" ""
[ "$(wc -l < "$W/repo-targets.tsv" | tr -d ' ')" = 13 ] && ok "저장소 대상 목록 13개" || ko "대상 수" ""

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
