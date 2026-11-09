#!/usr/bin/env bash
# README «반영 순서» 2번 백업 블록 시험. 블록을 README 에서 그대로 꺼내 임시 HOME 에서 실행하고,
# 7번(저장소 폴더 삭제) 뒤에도 백업에서 옛 스킬이 복원되는지 확인한다. 실제 ~/.claude 는 건드리지 않음.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

W=$(mktemp -d "${TMPDIR:-/tmp}/cc-dockit-backup.XXXXXX")
trap 'rm -rf "${W:?}"' EXIT
H="$W/home"; SRC="$W/repo/skills"
mkdir -p "$H/.claude/skills" "$SRC" "$H/.claude/projects/-Users-$(id -un)-orca-LoveRevolutionMaker/memory"

# 라이브: 4개는 저장소 폴더를 가리키는 링크, structure-change-doc 만 실제 폴더
for n in explain-diff tech-record work-report doc-coauthoring; do
  mkdir -p "$SRC/$n/references"; printf '%s 본문\n' "$n" > "$SRC/$n/SKILL.md"; printf '%s 참고\n' "$n" > "$SRC/$n/references/r.md"
  ln -s "$SRC/$n" "$H/.claude/skills/$n"
done
mkdir -p "$H/.claude/skills/structure-change-doc"; printf 'structure 본문\n' > "$H/.claude/skills/structure-change-doc/SKILL.md"
printf '메모\n' > "$H/.claude/projects/-Users-$(id -un)-orca-LoveRevolutionMaker/memory/MEMORY.md"

# README 2번 항목의 sh 블록만 추출
BLOCK="$W/step2.sh"
awk '/^2\. \*\*백업\.\*\*/ { in2 = 1; next } in2 && /^   ```sh/ { grab = 1; next } grab && /^   ```/ { exit } grab { sub(/^   /, ""); print }' "$REPO/README.md" > "$BLOCK"
[ -s "$BLOCK" ] && grep -q 'cp -' "$BLOCK" && ok "README 2번 블록을 추출함" || ko "README 2번 블록 추출" "비어 있음"

out=$(HOME="$H" bash "$BLOCK" 2>&1); rc=$?
B=$(cat "$H/.claude-config-backup/LAST-PRE-DOC-KIT" 2>/dev/null || true)
[ "$rc" = 0 ] && [ -d "$B/skills" ] && ok "백업 블록 실행 성공, 백업 폴더 기록" || ko "백업 실행" "rc=$rc $out"
[ -z "$(find "$B/skills" -type l 2>/dev/null)" ] && ok "백업에 링크가 없음(실제 내용으로 복사)" || ko "링크째 복사됨" ""

# 7번: 저장소 폴더와 라이브 링크를 지움
rm -rf "${SRC:?}"
for n in explain-diff tech-record work-report doc-coauthoring; do rm "$H/.claude/skills/$n"; done

bad=0
for n in explain-diff tech-record work-report doc-coauthoring; do
  [ "$(cat "$B/skills/$n/SKILL.md" 2>/dev/null)" = "$n 본문" ] && [ "$(cat "$B/skills/$n/references/r.md" 2>/dev/null)" = "$n 참고" ] || bad=$((bad + 1))
done
[ "$bad" = 0 ] && ok "저장소 폴더를 지운 뒤에도 링크였던 스킬 4개의 내용이 백업에 남음" || ko "백업 내용 유실" "$bad개"

# 되돌리기 표의 명령
for n in explain-diff tech-record structure-change-doc work-report doc-coauthoring; do
  [ -e "$H/.claude/skills/$n" ] && rm -rf "${H:?}/.claude/skills/$n"
  cp -Rp "$B/skills/$n" "$H/.claude/skills/"
done
bad=0
for n in explain-diff tech-record work-report doc-coauthoring; do
  [ ! -L "$H/.claude/skills/$n" ] && [ "$(cat "$H/.claude/skills/$n/SKILL.md")" = "$n 본문" ] || bad=$((bad + 1))
done
[ "$bad" = 0 ] && [ "$(cat "$H/.claude/skills/structure-change-doc/SKILL.md")" = "structure 본문" ] && ok "README 되돌리기 명령으로 옛 스킬 5개가 복원됨" || ko "복원" "$bad개"
[ "$(cat "$B/memory/MEMORY.md")" = "메모" ] && ok "프로젝트 메모리도 백업됨" || ko "메모리 백업" ""

# 회귀: 링크째 복사(-a)면 이 시험이 실패해야 함
W2="$W/regress"; mkdir -p "$W2/real" "$W2/out"; printf 'x\n' > "$W2/real/f"; ln -s "$W2/real" "$W2/link"
cp -a "$W2/link" "$W2/out/"; rm -rf "${W2:?}/real"
[ ! -e "$W2/out/link/f" ] && ok "대조: cp -a 는 링크째 복사돼 원본 삭제 뒤 내용이 없음(이 시험이 잡는 결함)" || ko "대조군" "cp -a 가 내용을 복사함"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
