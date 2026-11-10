#!/usr/bin/env bash
# 설치 전·후 의존성 점검(읽기 전용). 명령·앱·값 파일의 존재와 권한만 보고 파일 내용은 읽지 않음.
set -uo pipefail

usage() {
  cat <<'E'
사용: doctor.sh [-h|--help]
      install.sh --doctor   (같은 점검)

필수·권장 의존성을 한 줄씩 점검하고 빠진 것은 원인과 조치를 함께 출력한다. 아무것도 설치·변경하지 않는다.

출력 줄 형식(맨 앞 단어로 구분):
  OK    항목 (버전)
  MISS  항목 — 원인: … / 조치: …   필수. 하나라도 있으면 종료 코드 1
  WARN  항목 — 원인: … / 조치: …   권장·선택. 종료 코드에 영향 없음
마지막 줄: 요약: 필수 누락 N, 경고 M

값·시크릿 파일(values.env, slack-bot.env, main.env)은 있는지와 권한(600 권장)만 본다.
환경: HOME 기준. CLAUDE_CONFIG_VALUES 로 값 파일 위치 변경(install.sh 와 같음).
예시: ./install.sh --doctor && ./install.sh --dry-run
종료 코드: 0 필수 전부 있음, 1 필수 누락, 2 인자 오류
E
}

case "${1:-}" in
  "") ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; printf 'ERROR: 알 수 없는 인자: %s\n' "$1" >&2; exit 2 ;;
esac

N_MISS=0 N_WARN=0
ok() { printf 'OK    %s\n' "$*"; }
miss() { N_MISS=$((N_MISS + 1)); printf 'MISS  %s — 원인: %s / 조치: %s\n' "$1" "$2" "$3"; }
warn() { N_WARN=$((N_WARN + 1)); printf 'WARN  %s — 원인: %s / 조치: %s\n' "$1" "$2" "$3"; }
have() { command -v "$1" >/dev/null 2>&1; }
# 출력에 사용자 폴더 이름이 남지 않도록 HOME 을 ~ 로 바꿈
tilde() { case "$1" in "$HOME"/*) printf '~/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac; }
first_line() { "$@" 2>/dev/null | head -1 | tr -d '\r'; }

# 이름 버전명령 필수여부 쓰임 조치
tool() {
  local name="$1" ver="$2" need="$3" why="$4" fix="$5" v
  if have "$name"; then
    v=$(first_line sh -c "$ver")
    ok "$name${v:+ ($v)}"
  elif [ "$need" = req ]; then
    miss "$name" "명령 없음($why)" "$fix"
  else
    warn "$name" "명령 없음($why)" "$fix"
  fi
}

# ---- 운영체제·셸 ------------------------------------------------------------
if [ "$(uname -s)" = Darwin ]; then
  ok "macOS ($(first_line sw_vers -productVersion))"
else
  miss "macOS" "운영체제가 $(uname -s) 임(launchd·plist 설치가 macOS 전용)" "macOS 에서 실행"
fi
if [ -x /bin/bash ]; then ok "bash (/bin/bash $(first_line /bin/bash -c 'echo $BASH_VERSION'))"
else miss "bash" "/bin/bash 없음(허브 스크립트 첫 줄이 /bin/bash)" "macOS 기본 셸 확인"; fi

# ---- 필수 명령 --------------------------------------------------------------
tool git 'git --version' req "저장소 받기·전역 git 훅" "xcode-select --install 또는 brew install git"
if have python3; then
  pyv=$(first_line python3 -I -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
  if python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    ok "python3 ($pyv, $(tilde "$(command -v python3)"))"
  else
    miss "python3" "버전 $pyv(3.9 이상 필요: slack_push.py·doc-kit)" "brew install python 또는 python.org 설치본"
  fi
else
  miss "python3" "명령 없음(slack_push.py·doc-kit·소켓 수신기)" "xcode-select --install 또는 brew install python"
fi
tool jq 'jq --version' req "설정 병합(install.sh)·슬랙 스크립트" "brew install jq"
tool curl 'curl --version' req "Slack API 호출" "macOS 기본 포함. 없으면 brew install curl"
tool perl 'perl -e "print \$^V"' req "install.sh --diff-live 경로 치환" "macOS 기본 포함. 없으면 brew install perl"
tool launchctl 'echo macOS' req "Slack 폴러 launchd 등록" "macOS 에서 실행"
tool plutil 'echo macOS' opt "plist 형식 확인" "macOS 기본 포함"
tool claude 'claude --version' req "Claude Code 세션·스킬 실행" "https://claude.com/claude-code 안내대로 설치"
tool orca 'orca --version' req "허브·메인 코디네이터 터미널 제어" "Orca 앱 설치 뒤 앱 메뉴에서 CLI 설치"

# ---- Orca 앱 실행 상태 -------------------------------------------------------
if have orca; then
  st=$(orca status --json 2>/dev/null || true)
  running=$(printf '%s' "$st" | python3 -I -c 'import json,sys
try: r=json.load(sys.stdin)["result"]; print("yes" if r["app"]["running"] and r["runtime"]["reachable"] else "no")
except Exception: print("unknown")' 2>/dev/null || echo unknown)
  case "$running" in
    yes) ok "Orca 앱 실행 중" ;;
    no) warn "Orca 앱" "앱이 꺼졌거나 런타임 연결 안 됨(허브 순찰·메시지 전달 불가)" "orca open 으로 앱 실행 뒤 orca status 확인" ;;
    *) warn "Orca 앱" "orca status 응답을 읽지 못함" "orca status 를 직접 실행해 오류 확인" ;;
  esac
fi

# ---- 선택 도구 --------------------------------------------------------------
tool gh 'gh --version' opt "PR 생성(create-pr)" "brew install gh && gh auth login"
tool shellcheck 'shellcheck --version | sed -n 2p' opt "tests/install/run.sh 정적 검사" "brew install shellcheck"
if [ -d "/Applications/Unity Hub.app" ] || have unity; then ok "Unity (Hub 또는 unity CLI)"
else warn "Unity" "Unity Hub·unity CLI 없음(Unity 작업 허브·unity_slot.sh 만 영향)" "Unity 작업을 할 때만 Unity Hub 설치"; fi
if have python3 && python3 -I -c 'import slack_sdk' 2>/dev/null; then ok "python3 slack_sdk (Socket Mode 수신기용)"
else warn "python3 slack_sdk" "모듈 없음(Socket Mode 수신기를 켤 때만 필요, 폴러는 무관)" "켤 때 python3 -m pip install --user slack_sdk"; fi

# ---- 값·시크릿 파일: 존재·권한만 ---------------------------------------------
perm_of() { stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1" 2>/dev/null; }
secret_file() {
  local path="$1" need="$2" why="$3" fix="$4" p
  if [ -f "$path" ]; then
    p=$(perm_of "$path")
    if [ "$p" = 600 ]; then ok "$(tilde "$path") (권한 600)"
    else warn "$(tilde "$path")" "권한 $p(다른 계정이 읽을 수 있음)" "chmod 600 $(tilde "$path")"; fi
  elif [ "$need" = req ]; then
    miss "$(tilde "$path")" "파일 없음($why)" "$fix"
  else
    warn "$(tilde "$path")" "파일 없음($why)" "$fix"
  fi
}
secret_file "${CLAUDE_CONFIG_VALUES:-$HOME/.config/claude-config/values.env}" req \
  "install.sh 가 실명·채널 자리표시를 채울 값" "values.env.example 을 복사해 값을 채운 뒤 chmod 600"
secret_file "$HOME/.config/lrm-hubs/slack-bot.env" opt \
  "Slack 봇 토큰. 없으면 슬랙 알림·수신 꺼짐" "만들고 SLACK_BOT_TOKEN=·SLACK_USER_ID= 를 직접 채운 뒤 chmod 600"
secret_file "$HOME/.config/lrm-hubs/main.env" opt \
  "메인 코디네이터 설정" "skills/orca-main/references/main.env.example 을 복사해 채운 뒤 chmod 600"

printf '요약: 필수 누락 %d, 경고 %d\n' "$N_MISS" "$N_WARN"
[ "$N_MISS" = 0 ]
