#!/usr/bin/env bash
# README «반영 순서» 2번(백업)·7번(옛 스킬 삭제)·되돌리기를 README 에서 그대로 꺼내 임시 HOME 에서 실행해 확인한다.
# structure-change-doc 처럼 링크가 아닌 실제 폴더 안에 파일 단위 링크가 있는 사례도 포함. 실제 ~/.claude 는 건드리지 않음.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

W=$(mktemp -d "${TMPDIR:-/tmp}/cc-dockit-backup.XXXXXX")
trap 'rm -rf "${W:?}"' EXIT

LINKED="explain-diff tech-record work-report doc-coauthoring"   # 저장소 폴더를 가리키는 링크 하나
FOLDER="structure-change-doc"                                   # 실제 폴더 + 안의 파일 단위 링크
SCD_LINKS="scripts/check.py scripts/image_diff.py scripts/puml_diff.py scripts/screen_flow.sh templates/after.puml templates/before.puml templates/doc.html"

# 라이브 모양 만들기: mk_live <HOME> <저장소 skills 폴더>
mk_live() {
  local h="$1" src="$2" n f
  mkdir -p "$h/.claude/skills" "$src" "$h/.claude/projects/-Users-$(id -un)-orca-LoveRevolutionMaker/memory"
  for n in $LINKED; do
    mkdir -p "$src/$n/references"; printf '%s 본문\n' "$n" > "$src/$n/SKILL.md"; printf '%s 참고\n' "$n" > "$src/$n/references/r.md"
    ln -s "$src/$n" "$h/.claude/skills/$n"
  done
  mkdir -p "$src/$FOLDER/scripts" "$src/$FOLDER/templates" "$h/.claude/skills/$FOLDER/scripts" "$h/.claude/skills/$FOLDER/templates"
  for f in $SCD_LINKS; do
    printf 'real %s\n' "$f" > "$src/$FOLDER/$f"
    ln -s "$src/$FOLDER/$f" "$h/.claude/skills/$FOLDER/$f"
  done
  printf 'structure 본문\n' > "$h/.claude/skills/$FOLDER/SKILL.md"                        # 설치기가 렌더한 실제 파일
  mkdir -p "$h/.claude/skills/$FOLDER/scripts/__pycache__"                                 # 라이브 전용 파일
  printf 'pyc\n' > "$h/.claude/skills/$FOLDER/scripts/__pycache__/x.pyc"
  printf 'bak\n' > "$h/.claude/skills/$FOLDER/scripts/check.py.bak-1"
  printf '메모\n' > "$h/.claude/projects/-Users-$(id -un)-orca-LoveRevolutionMaker/memory/MEMORY.md"
}

# README 의 «N번» 항목 첫 sh 블록 추출: extract <번호> <제목 앞부분> <출력 파일>
extract() {
  awk -v key="^$1\\\\. \\\\*\\\\*$2" '$0 ~ key { in2 = 1; next } in2 && /^   ```sh/ { grab = 1; next } grab && /^   ```/ { exit } grab { sub(/^   /, ""); print }' "$REPO/README.md" > "$3"
}

H="$W/home"; SRC="$W/repo/skills"
mk_live "$H" "$SRC"

BLOCK="$W/step2.sh"; DEL="$W/step7.sh"
extract 2 백업 "$BLOCK"
[ -s "$BLOCK" ] && grep -q 'cp -' "$BLOCK" && ok "README 2번 블록을 추출함" || ko "README 2번 블록 추출" "비어 있음"
extract 7 '옛 스킬 5개 삭제' "$DEL"
[ -s "$DEL" ] && grep -q '^rm ' "$DEL" && ok "README 7번 블록을 추출함" || ko "README 7번 블록 추출" "비어 있음"

# ---- 7번 블록 자체 점검 --------------------------------------------------------
# 삭제는 리터럴 경로만(변수·와일드카드·find -delete 금지)
bad=$(grep -E '^[[:space:]]*rm ' "$DEL" | grep -E '[$*?{}]|-rf|-fr' || true)
[ -z "$bad" ] && ! grep -q -- '-delete' "$DEL" && ok "7번 삭제 줄은 리터럴 경로뿐(변수·와일드카드·-rf·-delete 없음)" || ko "삭제 줄이 리터럴이 아님" "$bad"
# links.tsv 가 안의 파일을 링크로 설치하는 옛 스킬 폴더는 모두 7번에 그 링크의 rm 줄이 있어야 함
miss=0 seen=0
while IFS=$'\t' read -r src dst mode; do
  [ "$mode" = link ] || continue
  case "$dst" in .claude/skills/*/*)
    n=${dst#.claude/skills/}; n=${n%%/*}
    case " $LINKED $FOLDER " in *" $n "*) seen=$((seen + 1)); grep -qxF "rm ~/$dst" "$DEL" || { miss=$((miss + 1)); echo "  rm 줄 없음: $dst"; } ;; esac ;;
  esac
done < <(grep -v '^#' "$REPO/links.tsv")
[ "$seen" = 7 ] && [ "$miss" = 0 ] && ok "links.tsv 의 폴더 안 링크 7개(structure-change-doc)가 7번에 모두 rm 줄로 있음" || ko "폴더 안 링크 목록 불일치" "seen=$seen miss=$miss"
# 링크 하나짜리 4개도 7번에 있어야 함
miss=0; for n in $LINKED; do grep -qxF "rm ~/.claude/skills/$n" "$DEL" || miss=$((miss + 1)); done
[ "$miss" = 0 ] && ok "7번에 폴더째 링크 4개의 rm 줄이 있음" || ko "폴더째 링크 rm 줄" "${miss}개 누락"
# README 되돌리기 표에 복원 명령과 중간 정지 안내가 있음
grep -qF 'cp -Rp "$B"/skills/<이름> ~/.claude/skills/' "$REPO/README.md" && grep -q '중간에 멈춰' "$REPO/README.md" && ok "되돌리기 표에 복원 명령과 7번 중간 정지 안내가 있음" || ko "되돌리기 표 내용" ""

# ---- 2번: 백업 ----------------------------------------------------------------
out=$(HOME="$H" bash "$BLOCK" 2>&1); rc=$?
B=$(cat "$H/.claude-config-backup/LAST-PRE-DOC-KIT" 2>/dev/null || true)
[ "$rc" = 0 ] && [ -d "$B/skills" ] && ok "백업 블록 실행 성공, 백업 폴더 기록" || ko "백업 실행" "rc=$rc $out"
[ -z "$(find "$B/skills" -type l 2>/dev/null)" ] && ok "백업에 링크가 없음(폴더 안 링크 포함 실제 내용으로 복사)" || ko "링크째 복사됨" ""
[ "$(cat "$B/skills/$FOLDER/scripts/check.py" 2>/dev/null)" = "real scripts/check.py" ] && [ -f "$B/skills/$FOLDER/scripts/__pycache__/x.pyc" ] && [ "$(cat "$B/skills/$FOLDER/SKILL.md")" = "structure 본문" ] && ok "폴더 안 링크가 가리키던 내용·라이브 전용 파일·SKILL.md 가 백업에 있음" || ko "structure-change-doc 백업 내용" ""

# ---- 7번 안전성: 저장소 원본이 살아 있어도 rm -r 이 원본을 건드리지 않음 ---------------
H2="$W/home2"; SRC2="$W/repo2/skills"
mk_live "$H2" "$SRC2"
out=$(HOME="$H2" bash -e "$DEL" 2>&1); rc=$?
keep=0; for f in $SCD_LINKS; do [ "$(cat "$SRC2/$FOLDER/$f" 2>/dev/null)" = "real $f" ] && keep=$((keep + 1)); done
[ "$rc" = 0 ] && [ "$keep" = 7 ] && [ ! -e "$H2/.claude/skills/$FOLDER" ] && ok "7번 실행: 라이브 폴더는 사라지고 저장소 원본 7개는 그대로" || ko "7번이 원본을 건드림" "rc=$rc keep=$keep $out"
[ -z "$(find "$H2/.claude/skills" -mindepth 1 2>/dev/null)" ] && ok "7번 실행 뒤 라이브 스킬 폴더에 남는 것이 없음" || ko "남은 항목" "$(ls -A "$H2/.claude/skills" | tr '\n' ' ')"

# ---- 7번: 저장소 폴더를 먼저 지운 순서(README 순서) -----------------------------------
rm -rf "${SRC:?}"
# 폴더가 남아 있으면(7번 전에 저장소만 지운 상태) 끊긴 링크 때문에 복원 cp 가 실패한다: 이 시험이 막으려는 결함
cp -Rp "$B/skills/$FOLDER" "$H/.claude/skills/" >/dev/null 2>&1; crc=$?
[ "$crc" != 0 ] && ok "대조: 폴더·끊긴 링크가 남은 채 복원하면 cp 가 실패함(7번을 먼저 끝내야 하는 이유)" || ko "대조군" "cp 가 성공함"
out=$(HOME="$H" bash -e "$DEL" 2>&1); rc=$?
[ "$rc" = 0 ] && [ -z "$(find "$H/.claude/skills" -mindepth 1 2>/dev/null)" ] && ok "저장소 폴더 삭제 뒤 7번 실행: 링크 4개·폴더 1개가 모두 지워짐" || ko "7번 실행" "rc=$rc $out"

bad=0
for n in $LINKED; do
  [ "$(cat "$B/skills/$n/SKILL.md" 2>/dev/null)" = "$n 본문" ] && [ "$(cat "$B/skills/$n/references/r.md" 2>/dev/null)" = "$n 참고" ] || bad=$((bad + 1))
done
[ "$bad" = 0 ] && ok "저장소 폴더를 지운 뒤에도 링크였던 스킬 4개의 내용이 백업에 남음" || ko "백업 내용 유실" "${bad}개"

# ---- 되돌리기 표의 명령 ---------------------------------------------------------------
fail=0
for n in $LINKED $FOLDER; do cp -Rp "$B/skills/$n" "$H/.claude/skills/" || fail=$((fail + 1)); done
bad=0
for n in $LINKED; do
  [ ! -L "$H/.claude/skills/$n" ] && [ "$(cat "$H/.claude/skills/$n/SKILL.md")" = "$n 본문" ] || bad=$((bad + 1))
done
scd_ok=1
for f in $SCD_LINKS; do [ ! -L "$H/.claude/skills/$FOLDER/$f" ] && [ "$(cat "$H/.claude/skills/$FOLDER/$f" 2>/dev/null)" = "real $f" ] || scd_ok=0; done
[ "$fail" = 0 ] && [ "$bad" = 0 ] && [ "$scd_ok" = 1 ] && [ "$(cat "$H/.claude/skills/$FOLDER/SKILL.md")" = "structure 본문" ] && [ -f "$H/.claude/skills/$FOLDER/scripts/__pycache__/x.pyc" ] && ok "README 되돌리기 명령으로 옛 스킬 5개(폴더 안 링크 7개 포함)가 실제 파일로 복원됨" || ko "복원" "cp 실패 $fail, 링크 스킬 $bad, scd $scd_ok"
[ "$(cat "$B/memory/MEMORY.md")" = "메모" ] && ok "프로젝트 메모리도 백업됨" || ko "메모리 백업" ""

# 회귀: 링크째 복사(-a)면 이 시험이 실패해야 함
W2="$W/regress"; mkdir -p "$W2/real" "$W2/out"; printf 'x\n' > "$W2/real/f"; ln -s "$W2/real" "$W2/link"
cp -a "$W2/link" "$W2/out/"; rm -rf "${W2:?}/real"
[ ! -e "$W2/out/link/f" ] && ok "대조: cp -a 는 링크째 복사돼 원본 삭제 뒤 내용이 없음(이 시험이 잡는 결함)" || ko "대조군" "cp -a 가 내용을 복사함"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
