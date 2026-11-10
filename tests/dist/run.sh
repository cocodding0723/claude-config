#!/usr/bin/env bash
# make_dist_zip.sh 검증. 임시 폴더에만 zip 을 만들고 실제 ~/Documents 에는 쓰지 않음.
# 심는 시크릿·개인 값은 이 파일이 저장소 검사에 걸리지 않도록 문자열을 쪼개 만든다.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
MK="$REPO/scripts/make_dist_zip.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

T=$(mktemp -d "${TMPDIR:-/tmp}/dist-test.XXXXXX")
trap 'rm -rf "${T:?}"' EXIT
export HOME="$T/home"; mkdir -p "$HOME"

# ---- 1. 이 저장소 HEAD 로 생성 ------------------------------------------------
out=$(bash "$MK" HEAD --out-dir "$HOME/out" 2>&1); rc=$?
Z="$HOME/out/orca-main-install-$(date +%Y%m%d).zip"
[ "$rc" = 0 ] && [ -f "$Z" ] && ok "HEAD 로 zip 생성" || ko "생성" "rc=$rc $(echo "$out" | tail -3 | tr '\n' ' ')"
names=$(zipinfo -1 "$Z" 2>/dev/null | grep -v '/$')
want=$(git -C "$REPO" ls-tree -r --name-only HEAD | wc -l | tr -d ' ')
[ "$(echo "$names" | wc -l | tr -d ' ')" = "$want" ] && echo "$out" | grep -q "^파일 수: $want$" && ok "zip 파일 수 = git 추적 파일 수($want)" || ko "파일 수" "zip $(echo "$names" | wc -l | tr -d ' '), git $want"
[ -z "$(echo "$names" | grep -v '^orca-main-install/')" ] && ok "zip 최상위 폴더 orca-main-install/" || ko "최상위 폴더" ""
bad=$(echo "$names" | grep -vE '\.env\.example$' | grep -E '(\.env(\.|$)|\.pem$|\.p8$|\.key$|\.keystore$|\.jks$|id_rsa|\.bak|__pycache__|\.pyc$|\.DS_Store$|slack-(inbox|sent|poll-state)|/state/|HANDOFF\.md$|decisions\.tsv$|\.(log|tmp|swp)$)' || true)
[ -z "$bad" ] && ok "zip 안 시크릿·.env·키·.bak·__pycache__·임시물 0개" || ko "금지 파일" "$(echo "$bad" | head -3 | tr '\n' ' ')"
echo "$names" | grep -qx 'orca-main-install/install.sh' && echo "$names" | grep -qx 'orca-main-install/skills/orca-main/SKILL.md' && echo "$names" | grep -qx 'orca-main-install/hubs/mods/lrm-board/hooks/register.tsx' && echo "$names" | grep -qx 'orca-main-install/hubs/scripts/slack_socket.py' && ok "zip 에 설치기·orca-main·mod·소켓 수신기 포함" || ko "필수 파일" "누락"
[ "$(echo "$out" | sed -n 's/^sha256: //p')" = "$(shasum -a 256 "$Z" | cut -d' ' -f1)" ] && ok "출력 sha256 = 실제 파일 해시" || ko "sha256" "불일치"
echo "$out" | grep -q '^만듦: ~/out/' && ! echo "$out" | grep -qF "$HOME" && ok "출력 경로는 HOME 을 ~ 로 표시" || ko "출력 경로" "$(echo "$out" | head -1)"

# ---- 2. 같은 이름은 덮어쓰지 않음 · 인자 오류 -----------------------------------
h1=$(shasum -a 256 "$Z" | cut -d' ' -f1)
bash "$MK" HEAD --out-dir "$HOME/out" >/dev/null 2>&1; rc=$?
[ "$rc" = 1 ] && [ "$h1" = "$(shasum -a 256 "$Z" | cut -d' ' -f1)" ] && ok "이미 있으면 종료 코드 1·파일 그대로" || ko "덮어쓰기 방지" "rc=$rc"
bash "$MK" 없는-ref-zz --out-dir "$HOME/o2" >/dev/null 2>&1; rc=$?
[ "$rc" = 1 ] && [ ! -e "$HOME/o2" ] && ok "없는 ref 는 종료 코드 1·폴더 미생성" || ko "없는 ref" "rc=$rc"
bash "$MK" --bogus >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "알 수 없는 옵션은 종료 코드 2" || ko "옵션 오류" "rc=$rc"

# ---- 3. 심은 값마다 중단·zip 미생성·값 미출력 ------------------------------------
# 임시 저장소에 스크립트를 넣고 파일 하나를 커밋해 검사가 잡는지 확인. 전역 git 훅은 끔
plant() {
  local label="$1" path="$2" body="$3" id; id="$label-${2//\//_}"; local r="$T/r-$id" o rc
  mkdir -p "$r/scripts" "$(dirname "$r/$path")"
  cp "$MK" "$r/scripts/"; printf '%s\n' "$body" > "$r/$path"
  git -C "$r" init -q -b feat/t && git -C "$r" add -f . && git -C "$r" -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@t.example commit -qm t
  o=$(DIST_DENY_PATTERN="${4:-}" bash "$r/scripts/make_dist_zip.sh" --out-dir "$T/out-$id" 2>&1); rc=$?
  if [ "$rc" = 1 ] && [ ! -e "$T/out-$id" ] && echo "$o" | grep -q "\[$label\] $path" && { [ -z "$body" ] || ! echo "$o" | grep -qF -e "$body"; }; then
    ok "검사: $label 심으면 중단·zip 미생성·값 미출력"
  else
    ko "검사 $label" "rc=$rc $(echo "$o" | head -2 | tr '\n' ' ')"
  fi
}
plant 토큰 docs/a.md "SLACK_BOT_TOKEN=xox""b-1234567890-abcdef"
plant 토큰 docs/k.txt "-----BEGIN ""OPENSSH PRIVATE KEY-----"
plant 이메일 docs/b.md "연락: someone""@""corp-mail.co"
plant 홈경로 docs/c.md "경로 /Use""rs/somebody/work"
plant 사용자명 docs/d.md "프로젝트 -Users-$(id -un)-orca"
plant SlackID docs/e.md "채널 D0""ABCDEFGH1"
plant 파일이름 hubs/slack-bot.env ""
plant 금지어 docs/f.md "담당 홍""길동" "홍길""동"
plant 파일이름 hubs/slack-bot.env.local "SLACK_BOT_TOKEN="
# 양식 파일(.env.example)은 통과
r="$T/r-envex"; mkdir -p "$r/scripts" "$r/hubs"; cp "$MK" "$r/scripts/"; printf '# SLACK_APP_TOKEN=\n' > "$r/hubs/slack-bot.env.example"
git -C "$r" init -q -b feat/t && git -C "$r" add . && git -C "$r" -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@t.example commit -qm t
bash "$r/scripts/make_dist_zip.sh" --out-dir "$T/out-envex" >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && ok ".env.example 양식은 통과" || ko ".env.example" "rc=$rc"

# ---- 3b. 바이너리: NUL 뒤 토큰·허용 밖 확장자 ------------------------------------
# 파일 하나(NUL 포함)를 커밋한 임시 저장소에서 실행. 인자: 이름 파일경로 기대종료코드 기대문구
plant_bin() {
  local id="$1" path="$2" want_rc="$3" want="$4" tok="$5" r="$T/r-bin-$1" o rc
  mkdir -p "$r/scripts" "$(dirname "$r/$path")"; cp "$MK" "$r/scripts/"
  printf 'head\000%s\ntail\n' "$tok" > "$r/$path"
  git -C "$r" init -q -b feat/t && git -C "$r" add -f . && git -C "$r" -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@t.example commit -qm t
  o=$(bash "$r/scripts/make_dist_zip.sh" --out-dir "$T/out-bin-$id" 2>&1); rc=$?
  if [ "$rc" = "$want_rc" ] && { [ -z "$want" ] || echo "$o" | grep -q "$want"; } && { [ -z "$tok" ] || ! echo "$o" | grep -qF -e "$tok"; }; then
    ok "바이너리: $id"
  else
    ko "바이너리 $id" "rc=$rc $(echo "$o" | head -2 | tr '\n' ' ')"
  fi
}
plant_bin NUL뒤토큰-md docs/n.md 1 '\[바이너리\] docs/n.md' "xox""b-1234567890-abcdef"
plant_bin NUL뒤토큰-png assets/n.png 1 '\[토큰\] assets/n.png' "xox""b-1234567890-abcdef"
plant_bin 정상-png assets/ok.png 0 '' "plain-data"

# ---- 3c. 내부 호스트·회사 도메인 경고: zip 은 만들고, --strict-internal 이면 중단 --------
r="$T/r-warn"; mkdir -p "$r/scripts" "$r/docs"; cp "$MK" "$r/scripts/"
printf 'host 100''.101.2.3\nsite acme''.atlassian''.net\nblog someone''.github''.io\n' > "$r/docs/w.md"
printf 'host 100''.102.3.4\n' > "$r/docs/w2.md"
git -C "$r" init -q -b feat/t && git -C "$r" add . && git -C "$r" -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@t.example commit -qm t
o=$(bash "$r/scripts/make_dist_zip.sh" --out-dir "$T/out-warn" 2>&1); rc=$?
[ "$rc" = 0 ] && ls "$T"/out-warn/*.zip >/dev/null 2>&1 && echo "$o" | grep -q '경고 내부 호스트(Tailscale 대역 IP): 2개 파일' && echo "$o" | grep -q '경고 회사 도메인(Jira 사이트): 1개 파일' && echo "$o" | grep -q '경고 개인 웹 주소(github.io): 1개 파일' && ok "내부 호스트·도메인은 경고만(종료 코드 0·zip 생성·종류와 파일 수 출력)" || ko "경고 모드" "rc=$rc $(echo "$o" | head -3 | tr '\n' ' ')"
! echo "$o" | grep -qE '100\.10[12]|atlassian|someone|acme|w\.md|w2\.md' && ok "경고 출력에 값·파일 이름 없음" || ko "경고 값 출력" "값이 보임"
o=$(bash "$r/scripts/make_dist_zip.sh" --strict-internal --out-dir "$T/out-strict" 2>&1); rc=$?
[ "$rc" = 1 ] && [ ! -e "$T/out-strict" ] && echo "$o" | grep -q '경고 내부 호스트' && ! echo "$o" | grep -qE '100\.10[12]|atlassian|someone|acme' && ok "--strict-internal: 경고도 중단(종료 코드 1·zip 미생성·값 미출력)" || ko "--strict-internal" "rc=$rc $(echo "$o" | head -2 | tr '\n' ' ')"
# 허용 목록 이메일(예시 도메인·git 주소)은 통과
r="$T/r-allow"; mkdir -p "$r/scripts" "$r/docs"; cp "$MK" "$r/scripts/"
printf 'git@github.com:x/y.git\nnoreply@anthropic.com\na@example.com\nb@test.example\n' > "$r/docs/ok.md"
git -C "$r" init -q -b feat/t && git -C "$r" add . && git -C "$r" -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@t.example commit -qm t
bash "$r/scripts/make_dist_zip.sh" --out-dir "$T/out-allow" >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && ok "허용 이메일(example·github·anthropic)은 통과" || ko "허용 이메일" "rc=$rc"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
