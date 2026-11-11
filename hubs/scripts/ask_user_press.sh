#!/bin/bash
# 허브가 사용자에게 "워커 확인 창을 눌러 달라"고 요청할 때 쓰는 유일한 경로.
# 워커 화면을 지금 다시 읽어 확인 창 문구가 실제로 보일 때만 메인에 요청을 보냄(낡은 보고로 인한 헛걸음 방지, 11-11 사고).
# 사용: ask_user_press.sh --terminal <워커 핸들> --expect '<확인 창 문구 일부>' "요청 한 줄"
#       --dry-run  메인에 보내지 않고 보낼 글만 출력
# 종료 코드: 0 전송, 1 메인 전송 실패, 2 인자 오류·터미널 없음, 3 확인 창 문구가 화면에 없음(요청 거부)
# 환경: LRM_ORCA(orca 경로) · LRM_MAIN_ENV(main.env, MAIN_TERMINAL 줄만 읽음)
set -u
ORCA="${LRM_ORCA:-$(command -v orca || echo /usr/local/bin/orca)}"
MAIN_ENV="${LRM_MAIN_ENV:-$HOME/.config/lrm-hubs/main.env}"
TERM_H="" EXPECT="" DRY=0
die() { echo "$1" >&2; exit "${2:-2}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --terminal) [ $# -ge 2 ] || die "--terminal 값 필요 — 워커 터미널 핸들"; TERM_H="$2"; shift 2 ;;
    --expect) [ $# -ge 2 ] || die "--expect 값 필요 — 확인 창에 보이는 문구 일부(예 'Do you want')"; EXPECT="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n 2,7p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --*) die "알 수 없는 옵션: $1 — --help 참고" ;;
    *) break ;;
  esac
done
[ -n "$TERM_H" ] && [ -n "$EXPECT" ] && [ $# -eq 1 ] && [ -n "$1" ] || die "사용: ask_user_press.sh --terminal 핸들 --expect '문구' \"요청 한 줄\""
REQ="$1"
RAW=$("$ORCA" terminal read --terminal "$TERM_H" --screen --json 2>/dev/null)
TEXT=$(printf '%s' "$RAW" | EXPECT="$EXPECT" REQ="$REQ" H="$TERM_H" python3 -c '
import json,os,sys,time
try:
    d=json.load(sys.stdin)
except ValueError:
    sys.exit(2)
if not d.get("ok"):
    sys.exit(2)
tail=[l.rstrip() for l in (d["result"]["terminal"].get("tail") or [])]
if not any(os.environ["EXPECT"] in l for l in tail):
    sys.exit(3)
shown=[l for l in tail if l.strip() and set(l.strip())-set("─━-")][-8:]
print("[허브→메인] 창 확인 요청: %s\n워커 %s · 화면 읽은 시각 %s\n```\n%s\n```" % (os.environ["REQ"], os.environ["H"][:13], time.strftime("%H:%M:%S"), "\n".join(shown)))
')
case $? in
  0) ;;
  3) die "확인 창 안 보임('$EXPECT' 없음) — 이미 처리됐을 수 있음. 화면을 다시 확인하고 요청하지 않음" 3 ;;
  *) die "워커 터미널 없음·읽기 실패 — 세션이 끝났는지 orca terminal list 로 확인" 2 ;;
esac
if [ "$DRY" = 1 ]; then printf '%s\n' "$TEXT"; exit 0; fi
MAIN=$(sed -n 's/^MAIN_TERMINAL=//p' "$MAIN_ENV" 2>/dev/null | head -1)
[ -n "$MAIN" ] || die "메인 터미널 없음 — $MAIN_ENV 의 MAIN_TERMINAL 확인" 1
"$ORCA" terminal send --terminal "$MAIN" --text "$TEXT" --enter --wait-submit 20 --json >/dev/null 2>&1 || die "메인 전송 실패 — orca 상태 확인" 1
echo "전송: 메인에 창 확인 요청"
