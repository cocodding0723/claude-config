#!/usr/bin/env bash
# README «반영 순서» 4번의 라이브 우선 점검 설명 시험. 치환 대상 13개 중 render 2개는 링크가 아니라
# 설치기가 값을 채워 만드는 파일이라, 나머지가 모두 링크여도 옛 내용이면 LIVE-NEWER 가 나오는지 확인한다.
# 임시 HOME 에서만 실행하며 실제 ~/.claude 등은 읽지도 바꾸지도 않음.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
INSTALL="$REPO/install.sh"
unset GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM CLAUDE_CONFIG_ALLOW_LIVE_DIFF CLAUDE_CONFIG_VALUES
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

W=$(mktemp -d "${TMPDIR:-/tmp}/cc-dockit-livediff.XXXXXX")
trap 'rm -rf "${W:?}"' EXIT
export HOME="$W/home"
mkdir -p "$HOME/.config/claude-config"
printf 'OWNER_NAME=VAL_OWNER\nPERSON_FULL=VAL_PERSON_FULL\nPERSON_SHORT=VAL_PERSON_SHORT\nDM_CHANNEL=VAL&DM_CHANNEL\nREMOTE_ENV=VAL_REMOTE\n' > "$HOME/.config/claude-config/values.env"
printf '[user]\n\tname = sandbox\n' > "$HOME/.gitconfig"

# 치환 대상 13개(repo 줄)의 설치 방식: links.tsv 의 모드로 link/render 를 가름
RENDER_DST=() LINK_DST=()
while IFS=$'\t' read -r base rel; do
  [ "$base" = repo ] || continue
  row=$(awk -F'\t' -v s="$rel" '$1 == s { print $2 "\t" $3; exit }' "$REPO/links.tsv")
  if [ -z "$row" ]; then
    # 폴더 항목(skills/<이름>)의 하위 파일: 상위 폴더 줄을 찾음
    d=$rel; while [ "$d" != "." ] && [ -z "$row" ]; do d=$(dirname "$d"); row=$(awk -F'\t' -v s="$d" '$1 == s { print $2 "\t" $3; exit }' "$REPO/links.tsv"); done
    [ -n "$row" ] && row="${row%%$'\t'*}/${rel#"$d"/}"$'\t'"${row##*$'\t'}"
  fi
  dst=${row%%$'\t'*}; mode=${row##*$'\t'}
  if [ "$mode" = render ]; then RENDER_DST+=("$dst"); else LINK_DST+=("$dst"); fi
done < <(grep -v '^#' "$REPO/migrate/rename-targets.tsv")
[ "${#RENDER_DST[@]}" = 2 ] && [ "${#LINK_DST[@]}" = 11 ] && ok "치환 대상 13개 = 링크 11개 + render 2개" || ko "치환 대상 구성" "render ${#RENDER_DST[@]}, link ${#LINK_DST[@]}"

# README 가 render 2개의 LIVE-NEWER 줄과 중단 메시지, 허용 줄 목록을 그대로 적고 있는지
miss=0; for d in "${RENDER_DST[@]}"; do grep -qxF "   LIVE-NEWER $d" "$REPO/README.md" || miss=$((miss + 1)); done
grep -qF '«라이브가 저장소보다 앞선 항목 2건»' "$REPO/README.md" && grep -qF '**허용 줄 목록.**' "$REPO/README.md" && [ "$miss" = 0 ] && ok "README 가 render 2줄·중단 메시지·허용 줄 목록을 적고 있음" || ko "README 설명" "render 줄 ${miss}개 누락"

# ---- 첫 설치 후: 링크 11개와 render 2개가 모두 저장소와 같음 --------------------------
out=$("$INSTALL" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "빈 HOME 에 설치" || ko "설치" "rc=$rc $(echo "$out" | tail -2 | tr '\n' ' ')"
out=$("$INSTALL" --diff-live 2>&1); rc=$?
[ "$rc" = 0 ] && ! echo "$out" | grep -q '^LIVE-' && ok "설치 직후 --diff-live: 출력 없음" || ko "설치 직후 diff-live" "rc=$rc"

# ---- 모두 링크 상태에서 render 2개만 옛 이름 내용(= 설치 전 라이브) ------------------------
bad=0
for d in "${RENDER_DST[@]}"; do
  [ -f "$HOME/$d" ] && [ ! -L "$HOME/$d" ] || { bad=$((bad + 1)); continue; }
  sed 's/doc-kit/structure-change-doc/g' "$HOME/$d" > "$W/old" && cp "$W/old" "$HOME/$d"
done
for d in "${LINK_DST[@]}"; do [ -L "$HOME/$d" ] || [ -L "$(dirname "$HOME/$d")" ] || bad=$((bad + 1)); done
[ "$bad" = 0 ] && ok "준비: 링크 11개는 링크, render 2개는 링크가 아닌 실제 파일(옛 이름 내용으로 바꿈)" || ko "준비" "${bad}건 예상과 다름"

out=$("$INSTALL" --diff-live 2>&1); rc=$?
want=$(for d in "${RENDER_DST[@]}"; do echo "LIVE-NEWER $d"; done | LC_ALL=C sort)
[ "$rc" = 1 ] && [ "$(echo "$out" | LC_ALL=C sort)" = "$want" ] && ok "링크 상태여도 --diff-live 는 render 2개만 LIVE-NEWER 로 알림(rc 1)" || ko "render 2줄" "rc=$rc $(echo "$out" | tr '\n' '/')"

before=$(cd "$HOME" && find . -type f | LC_ALL=C sort | xargs shasum | shasum)
out=$("$INSTALL" --dry-run 2>&1); rc=$?
[ "$rc" != 0 ] && echo "$out" | grep -q '라이브가 저장소보다 앞선 항목 2건' && ok "플래그 없는 --dry-run 은 이 2건으로 중단" || ko "dry-run 중단" "rc=$rc"
out=$("$INSTALL" 2>&1); rc=$?
after=$(cd "$HOME" && find . -type f | LC_ALL=C sort | xargs shasum | shasum)
[ "$rc" != 0 ] && [ "$before" = "$after" ] && ok "플래그 없는 설치도 중단하고 아무것도 바꾸지 않음(백업 폴더 포함 파일 지문 동일)" || ko "설치 중단" "rc=$rc"

# 허용 줄 목록(= render 2줄)과 같을 때만 플래그로 진행
extra=$("$INSTALL" --diff-live 2>&1 | grep -vxF "$want" || true)
[ -z "$extra" ] && ok "허용 줄 목록 밖의 줄이 없음" || ko "허용 줄 밖" "$extra"
"$INSTALL" --dry-run --allow-live-diff >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && ok "--dry-run --allow-live-diff 는 중단 없이 끝남" || ko "플래그 dry-run" "rc=$rc"
out=$("$INSTALL" --allow-live-diff 2>&1); rc=$?
[ "$rc" = 0 ] && ok "--allow-live-diff 설치 성공" || ko "플래그 설치" "rc=$rc"
bad=0; for d in "${RENDER_DST[@]}"; do grep -q 'structure-change-doc' "$HOME/$d" && bad=$((bad + 1)); done
[ "$bad" = 0 ] && ok "설치 뒤 render 2개가 저장소 내용(doc-kit 이름)으로 바뀜" || ko "render 갱신" "${bad}개 옛 이름 남음"
out=$("$INSTALL" --diff-live 2>&1); rc=$?
[ "$rc" = 0 ] && "$INSTALL" --check >/dev/null 2>&1 && ok "설치 뒤 --diff-live 출력 없음, --check 통과" || ko "설치 뒤 점검" "rc=$rc"

# ---- 링크가 아닌 사본이 남은 경우: 그 파일도 줄로 나옴 ------------------------------------
t="$HOME/${LINK_DST[0]}"
if [ -L "$t" ]; then rm "$t"; cp "$REPO/$(awk -F'\t' -v d="${LINK_DST[0]}" '$2 == d { print $1; exit }' "$REPO/links.tsv")" "$t"; printf '라이브 수정\n' >> "$t"; fi
out=$("$INSTALL" --diff-live 2>&1); rc=$?
[ "$rc" = 1 ] && [ "$out" = "LIVE-NEWER ${LINK_DST[0]}" ] && ok "링크가 아닌 사본이 저장소와 다르면 그 파일만 LIVE-NEWER" || ko "링크 아닌 사본" "rc=$rc $(echo "$out" | tr '\n' '/')"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
