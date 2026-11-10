#!/usr/bin/env bash
# 배포용 zip 생성. git 에 추적된 파일만(git archive) 담고, 만들기 전에 시크릿·개인 값 형태를 검사해 걸리면 중단함.
set -euo pipefail

usage() {
  cat <<'E'
사용: make_dist_zip.sh [ref] [--out-dir <폴더>] [--strict-internal]

  ref          담을 커밋(기본 HEAD). 병합 뒤에는 main
  --out-dir    저장 폴더(기본 ~/Documents/90_정리기록/배포, 없으면 만듦)
  --strict-internal  내부 호스트·회사 도메인 경고도 중단으로 처리

결과 파일: <폴더>/orca-main-install-<YYYYMMDD>.zip. 같은 이름이 있으면 덮어쓰지 않고 중단한다.
zip 안 최상위 폴더: orca-main-install/

만들기 전 검사(하나라도 걸리면 zip 을 만들지 않음. 출력은 파일:줄만, 값은 출력하지 않음):
  토큰       xox?-·xapp-숫자-·sk-·ghp_·AKIA·PEM 개인 키·인증서 머리줄(바이너리 파일도 검사)
  이메일     example 도메인·github.com·anthropic.com 외 주소
  홈 경로    macOS 사용자 홈 절대 경로, 이 맥 사용자명
  Slack ID   U0·D0·C0·W0·G0 로 시작하는 9~11자 대문자·숫자
  파일 이름  .env·.env.*(.example 제외)·키·인증서·.bak·__pycache__·.pyc·.DS_Store·허브 실행 상태·임시 로그
  바이너리   허용 확장자(이미지·글꼴·오디오·pdf) 밖의 바이너리 파일. 허용 확장자도 토큰은 검사
추가 금지어: DIST_DENY_PATTERN=<정규식> (예: 실명. 값은 출력하지 않음)

경고(zip 은 만듦, 종류와 파일 수만 출력하고 값은 출력하지 않음):
  내부 호스트   Tailscale 대역(100.64/10) IP
  회사 도메인   Jira 사이트 도메인
  개인 웹 주소  github.io 주소
  같은 사용자의 다른 맥으로 가는 zip 이면 시크릿은 아니지만 공유되면 노출될 수 있으니 공유 전 확인.

예시: scripts/make_dist_zip.sh
      scripts/make_dist_zip.sh main
      scripts/make_dist_zip.sh HEAD --out-dir "$(mktemp -d)"
종료 코드: 0 생성, 1 검사 걸림(--strict-internal 이면 경고 포함)·이미 있음·git 오류, 2 인자 오류
E
}

die() { printf 'ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }

REF=HEAD OUT_DIR="$HOME/Documents/90_정리기록/배포" n_ref=0 STRICT=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --strict-internal) STRICT=1; shift ;;
    --out-dir) [ -n "${2:-}" ] || die "--out-dir 에 폴더 경로가 필요함" 2; OUT_DIR="$2"; shift 2 ;;
    -*) usage >&2; die "알 수 없는 옵션: $1" 2 ;;
    *) n_ref=$((n_ref + 1)); [ "$n_ref" = 1 ] || die "ref 는 하나만" 2; REF="$1"; shift ;;
  esac
done

REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null) || die "git 저장소 안에서 실행해야 함"
COMMIT=$(git -C "$REPO" rev-parse --verify -q "$REF^{commit}") || die "ref 를 찾을 수 없음: $REF. git branch 로 이름 확인"
NAME="orca-main-install-$(date +%Y%m%d).zip"
DEST="$OUT_DIR/$NAME"
[ ! -e "$DEST" ] || die "이미 있음: $DEST. 기존 파일을 옮기거나 --out-dir 로 다른 폴더 지정"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/dist-zip.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
mkdir "$TMP/tree"
git -C "$REPO" archive --format=tar "$COMMIT" | tar -x -C "$TMP/tree"

# 검사 결과는 파일:줄만 남김(일치한 값은 출력하지 않음)
hits="$TMP/hits"; : > "$hits"
TOKEN_RE='xox[a-z]-[A-Za-z0-9]|xapp-[0-9]-[A-Za-z0-9]|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*(PRIVATE KEY|CERTIFICATE)-----'
BIN_OK='png|jpe?g|gif|webp|ico|ttf|otf|woff2?|mp3|m4a|wav|pdf'  # 내용 검사가 토큰뿐인 바이너리 확장자
scan() {
  local label="$1" re="$2" flags="${3:-E}"
  (cd "$TMP/tree" && grep -rnI"$flags" -e "$re" . 2>/dev/null | cut -d: -f1,2 | sed "s#^\./#[$label] #") >> "$hits" || true
}
# grep -I 는 바이너리를 건너뛰므로 NUL 포함 파일은 따로 처리: 허용 확장자 밖이면 중단, 안이면 토큰만 검사
scan_binary() {
  local f
  (cd "$TMP/tree" && find . -type f -print0) | while IFS= read -r -d '' f; do
    [ -s "$TMP/tree/$f" ] && ! LC_ALL=C tr -d '\0' < "$TMP/tree/$f" | cmp -s - "$TMP/tree/$f" || continue
    if printf '%s' "$f" | grep -qiE "\.($BIN_OK)$"; then
      LC_ALL=C grep -anE -e "$TOKEN_RE" "$TMP/tree/$f" 2>/dev/null | cut -d: -f1 | sed "s#^#[토큰] ${f#./}:#" || true
    else
      printf '[바이너리] %s\n' "${f#./}"
    fi
  done >> "$hits"
}
scan 토큰 "$TOKEN_RE"
scan 홈경로 '/Use''rs/[A-Za-z0-9._-]+'  # 이 파일이 저장소 경로 검사에 걸리지 않게 쪼갬
scan 사용자명 "$(id -un)" F
scan SlackID '\b[UDCWG]0[A-Z0-9]{8,10}\b'
[ -z "${DIST_DENY_PATTERN:-}" ] || scan 금지어 "$DIST_DENY_PATTERN"
scan_binary
(cd "$TMP/tree" && grep -rnIoE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' . 2>/dev/null \
  | grep -vE '@([A-Za-z0-9-]+\.)*(example(\.com|\.org)?|github\.com|anthropic\.com)$' \
  | cut -d: -f1,2 | sed 's#^\./#[이메일] #') >> "$hits" || true
(cd "$TMP/tree" && find . -type f | sed 's#^\./##' | grep -E '(\.env(\.|$)|\.pem$|\.p8$|\.p12$|\.key$|\.keystore$|\.jks$|id_rsa|id_ed25519|\.bak|__pycache__|\.pyc$|\.DS_Store$|slack-(inbox|sent|poll-state|pending-history)|/state/|HANDOFF\.md$|decisions\.tsv$|\.(log|tmp|swp)$)' \
  | grep -vE '\.env\.example$' | sed 's#^#[파일이름] #') >> "$hits" || true

# 경고: 시크릿은 아니지만 공유되면 노출되는 값. 종류와 파일 수만 출력
warn_scan() {
  local label="$1" re="$2" n
  n=$(cd "$TMP/tree" && grep -rlIE -e "$re" . 2>/dev/null | wc -l | tr -d ' ') || true
  [ "$n" = 0 ] || { printf '경고 %s: %s개 파일(값은 출력하지 않음)\n' "$label" "$n" >&2; WARNED=$((WARNED + 1)); }
}
WARNED=0
warn_scan '내부 호스트(Tailscale 대역 IP)' '\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}\b'
warn_scan '회사 도메인(Jira 사이트)' 'atlassian\.net'
warn_scan '개인 웹 주소(github.io)' '[A-Za-z0-9-]+\.github\.io'
[ "$STRICT" = 0 ] || [ "$WARNED" = 0 ] || [ -s "$hits" ] || { printf '[경고] --strict-internal: 경고 %s종류\n' "$WARNED" >&2; die "경고도 중단하는 모드라 zip 을 만들지 않음. 위 파일을 자리표시로 바꾸거나 --strict-internal 없이 실행"; }

if [ -s "$hits" ]; then
  sort -u "$hits" >&2
  die "검사 걸림 $(sort -u "$hits" | wc -l | tr -d ' ')건. 위 위치를 자리표시로 바꾸거나 추적에서 빼고 다시 실행"
fi

git -C "$REPO" archive --format=zip --prefix=orca-main-install/ -o "$TMP/$NAME" "$COMMIT"
mkdir -p "$OUT_DIR"
cp "$TMP/$NAME" "$DEST"

files=$(zipinfo -1 "$DEST" | grep -vc '/$')
# 사용자 폴더 이름이 화면·기록에 남지 않도록 HOME 을 ~ 로 표시
printf '만듦: %s\n' "${DEST/#"$HOME"/~}"
printf 'ref: %s (%s)\n' "$REF" "${COMMIT:0:7}"
printf '크기: %s 바이트\n' "$(stat -f '%z' "$DEST" 2>/dev/null || stat -c '%s' "$DEST")"
printf '파일 수: %s\n' "$files"
printf 'sha256: %s\n' "$(shasum -a 256 "$DEST" | cut -d' ' -f1)"
