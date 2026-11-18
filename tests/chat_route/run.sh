#!/usr/bin/env bash
# 채팅 택1 분기(lib/chat.sh) 시험. 진입점 3개가 CHAT 값에 따라 슬랙 그대로·디스코드 구현으로 넘김·중단 중 하나로 가는지 확인.
# 진입점 사본을 임시 폴더에 두고 디스코드 구현은 인자를 기록하는 가짜로 대체. 실제 Slack·Discord 호출 없음.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
SCRIPTS=${CHAT_ROUTE_SCRIPTS:-$REPO/hubs/scripts}
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-chatroute.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
mkdir -p "$SB/s/lib" "$SB/home/.config/claude-config"
cp "$SCRIPTS/lib/py.sh" "$SCRIPTS/lib/chat.sh" "$SB/s/lib/"
export HOME="$SB/home" LRM_HUB_DIR="$SB/hub"
VALS="$HOME/.config/claude-config/values.env"

for pair in slack_notify.sh:discord_notify.py slack_poll.sh:discord_poll.py slack_pending.sh:discord_pending.py; do
  ep=${pair%%:*} impl=${pair#*:}
  cp "$SCRIPTS/$ep" "$SB/s/$ep"
  rm -f "$SB/s/$impl"

  # 디스코드인데 구현 없음 → 원인·조치, 종료 코드 2
  printf 'CHAT=discord\n' > "$VALS"
  out=$(bash "$SB/s/$ep" --help 2>&1); rc=$?
  [ "$rc" = 2 ] && echo "$out" | grep -q "$impl 없음" && ok "$ep: 디스코드 구현 없으면 중단" || ko "$ep 구현 없음" "rc=$rc $out"

  # 디스코드 → 같은 인자로 구현에 넘김
  printf 'import sys\nprint("IMPL", *sys.argv[1:])\n' > "$SB/s/$impl"
  out=$(bash "$SB/s/$ep" --once "가 나" 2>&1); rc=$?
  [ "$rc" = 0 ] && [ "$out" = "IMPL --once 가 나" ] && ok "$ep: CHAT=discord 면 인자 그대로 넘김" || ko "$ep 넘김" "rc=$rc $out"

  # 허용값 밖 → 중단
  out=$(LRM_CHAT=teams bash "$SB/s/$ep" --help 2>&1); rc=$?
  [ "$rc" = 2 ] && echo "$out" | grep -q 'CHAT=teams 은 허용값' && ok "$ep: 허용값 밖이면 중단" || ko "$ep 허용값" "rc=$rc"

  # 값 없음(기본 slack) → 슬랙 본문 실행(도움말에 원래 사용법이 나옴)
  : > "$VALS"
  out=$(bash "$SB/s/$ep" --help 2>&1)
  echo "$out" | grep -q 'IMPL' && ko "$ep 기본 slack" "디스코드로 넘어감" || ok "$ep: CHAT 없으면 슬랙 그대로"
done

printf '결과: PASS %d, FAIL %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
