#!/usr/bin/env bash
# 자동 알림 하루 1회 판정(alert_dedup.py)과 slack_notify.sh 연결 시험.
# 가짜 curl(PATH 앞)·로컬 주소(SLACK_API_BASE=127.0.0.1)·임시 HOME 만 씀. 실제 Slack·토큰·라이브 파일은 건드리지 않음.
# 시각은 LRM_ALERT_NOW(epoch 초)로 고정해 날짜 바뀜을 흉내 냄.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS="$HERE/../../hubs/scripts"
NOTIFY="$SCRIPTS/slack_notify.sh"
AD="$SCRIPTS/alert_dedup.py"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-alertdedup.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
LOG="$SB/log" BIN="$SB/bin" HOME_DIR="$SB/home"; CFG="$HOME_DIR/.config/lrm-hubs"
mkdir -p "$LOG" "$BIN" "$CFG"
cp "$HERE/../slack_notify/fake_curl.sh" "$BIN/curl"; chmod 755 "$BIN/curl"
printf 'SLACK_BOT_TOKEN=fake-token-0123456789-abcdefghij\nSLACK_USER_ID=Uuser1\n' > "$CFG/slack-bot.env"
D1=1794700000            # 임의의 하루 안 시각
D1B=$((D1 + 3600))       # 같은 날, 1시간 뒤
D2=$((D1 + 2 * 86400))   # 이틀 뒤

# send: 인자를 slack_notify.sh 에 전달. 결과 RC, $SB/out, 보낸 횟수 N(slack-sent.jsonl 줄 수)
send() {
  local now="$1"; shift
  LRM_ALERT_NOW="$now" HOME="$HOME_DIR" PATH="$BIN:$PATH" SLACK_API_BASE=http://127.0.0.1:9 FAKE_CURL_LOG="$LOG" LRM_BTN_WARN=off \
    python3 "$HERE/../slack_notify/augment.py" "$NOTIFY" "$@" > "$SB/out" 2> "$SB/err"
  RC=$?
  N=$( [ -f "$CFG/slack-sent.jsonl" ] && wc -l < "$CFG/slack-sent.jsonl" | tr -d ' ' || echo 0 )
}
sent_n() { [ -f "$CFG/slack-sent.jsonl" ] && wc -l < "$CFG/slack-sent.jsonl" | tr -d ' ' || echo 0; }
last_action() { tail -n 1 "$CFG/alert-dedup.jsonl" 2>/dev/null | jq -r '.action + ":" + (.reason // "")'; }
reset() { rm -f "$CFG"/slack-sent.jsonl "$CFG"/alert-dedup.* ; : > "$LOG/fail.api"; }

# ---- A. 같은 제목·같은 상태 하루 2회 → 1회 ------------------------------------------------------
reset
send $D1 --kind warn --tag hubx --from watcher "연결 끊김
- 원인: x"
B1=$(sent_n)
send $D1B --kind warn --tag hubx --from watcher "연결 끊김
- 원인: y(본문이 달라도)"
B2=$(sent_n)
[ "$B1" = 1 ] && [ "$B2" = 1 ] && [ "$RC" = 0 ] && grep -q '^suppressed$' "$SB/out" && ok "A 같은 제목·같은 상태 하루 2회 → 1회 전송, 둘째는 suppressed(종료 0)" || ko "A 하루 2회" "B1=$B1 B2=$B2 rc=$RC out=$(cat "$SB/out")"
[ "$(last_action)" = "suppressed:same_day_same_state" ] && ok "A 막은 알림은 alert-dedup.jsonl 에 suppressed 로 기록" || ko "A suppressed 기록" "$(last_action)"
send $D1B --kind warn --tag hubx --from watcher "연결 끊김 14:05 (3분 전)
- 원인: z"
[ "$(sent_n)" = 1 ] && ok "A 제목의 시각·'N분 전' 만 달라도 같은 제목으로 봄" || ko "A 시각 정규화" "$(sent_n)"

# ---- B. 상태 변화 → 재전송 ---------------------------------------------------------------------
send $D1B --kind warn --tag hubx --from watcher --state fixed "연결 끊김
- 원인: y"
[ "$(sent_n)" = 2 ] && [ "$(last_action)" = "sent:state_changed" ] && ok "B 상태가 바뀌면(--state) 같은 날에도 재전송" || ko "B 상태 변화" "n=$(sent_n) $(last_action)"
# 실패 → 통과 → 실패: 같은 키가 다시 와도 사이에 상태가 바뀌었으면 보냄
reset
send $D1 --kind warn --tag ci-watch --from ci --subject pr-1 --state fail "PR #1 CI 실패 원인"
send $D1 --kind done --tag ci-watch --from ci --subject pr-1 --state pass "CI 통과"
send $D1 --kind warn --tag ci-watch --from ci --subject pr-1 --state fail "PR #1 CI 실패 원인"
send $D1 --kind warn --tag ci-watch --from ci --subject pr-1 --state fail "PR #1 CI 실패 원인"
[ "$(sent_n)" = 3 ] && ok "B 실패→통과→실패는 3회 전송, 이어 같은 실패 반복은 막음" || ko "B 실패→통과→실패" "n=$(sent_n)"
# 대상(subject)이 다르면 별개: 같은 제목 'CI 통과' 가 PR 마다 1회
reset
send $D1 --kind done --tag ci-watch --from ci --subject pr-1 --state pass "CI 통과"
send $D1 --kind done --tag ci-watch --from ci --subject pr-2 --state pass "CI 통과"
send $D1 --kind done --tag ci-watch --from ci --subject pr-1 --state pass "CI 통과"
[ "$(sent_n)" = 2 ] && ok "B 대상이 다른 같은 제목은 각각 1회" || ko "B 대상 분리" "n=$(sent_n)"
# 보낸 곳·허브·종류가 다르면 별개 키
reset
send $D1 --kind warn --tag h1 --from a "같은 제목"
send $D1 --kind warn --tag h2 --from a "같은 제목"
send $D1 --kind warn --tag h1 --from b "같은 제목"
send $D1 --kind progress --tag h1 --from a "같은 제목"
[ "$(sent_n)" = 4 ] && ok "B 키 = 보낸 곳·허브·종류·제목, 하나라도 다르면 별개" || ko "B 키 구성" "n=$(sent_n)"

# ---- C. 날짜 바뀜 → 재전송 ---------------------------------------------------------------------
reset
send $D1 --kind warn --from watcher "하루 한 번"
send $D2 --kind warn --from watcher "하루 한 번"
[ "$(sent_n)" = 2 ] && [ "$(last_action)" = "sent:new_day" ] && ok "C 날짜가 바뀌면 재전송" || ko "C 날짜 바뀜" "n=$(sent_n) $(last_action)"

# ---- D. 제한 없는 전송 -------------------------------------------------------------------------
reset
send $D1 --kind decide --no-doc --options '1=예 => 진행,2=아니오 => 중단' --recommend 1 --decision-id D-x1 --from watcher $'카드\n배경: 시험 배경 설명입니다\n용어: 없음'
send $D1 --kind decide --no-doc --options '1=예 => 진행,2=아니오 => 중단' --recommend 1 --decision-id D-x2 --from watcher $'카드\n배경: 시험 배경 설명입니다\n용어: 없음'
[ "$(sent_n)" = 1 ] && grep -q suppressed "$SB/out" && ok "D --from 을 준 시스템 결정 카드는 제한함(같은 제목·같은 상태)" || ko "D 시스템 카드" "n=$(sent_n)"
reset
send $D1 --kind decide --no-doc --options '1=예 => 진행,2=아니오 => 중단' --recommend 1 --decision-id D-y1 $'카드\n배경: 시험 배경 설명입니다\n용어: 없음'
send $D1 --kind decide --no-doc --options '1=예 => 진행,2=아니오 => 중단' --recommend 1 --decision-id D-y2 $'카드\n배경: 시험 배경 설명입니다\n용어: 없음'
[ "$(sent_n)" = 2 ] && ok "D --from 없는 결정 카드(사용자 요청)는 제한 없음" || ko "D 사용자 카드" "n=$(sent_n)"
reset
send $D1 --kind warn --from watcher --thread 1700000000.000100 "스레드 답글"
send $D1 --kind warn --from watcher --thread 1700000000.000100 "스레드 답글"
[ "$(sent_n)" = 2 ] && [ ! -f "$CFG/alert-dedup.json" ] && ok "D --thread 답글은 --from 이 있어도 제한 없음(판정 안 거침)" || ko "D 스레드" "n=$(sent_n)"
reset
printf 'doc' > "$SB/doc.txt"
send $D1 --kind doc --from watcher "문서 전달" "$SB/doc.txt"
send $D1 --kind doc --from watcher "문서 전달" "$SB/doc.txt"
[ "$(sent_n)" = 2 ] && ok "D 파일 첨부(info --file)는 제한 없음" || ko "D 파일" "n=$(sent_n)"
reset
send $D1 --kind warn "옛 호출"
send $D1 --kind warn "옛 호출"
[ "$(sent_n)" = 2 ] && [ ! -f "$CFG/alert-dedup.json" ] && ok "D --from 이 없는 기존 호출은 불변(제한 없음)" || ko "D 옛 호출" "n=$(sent_n)"

# ---- E. 전송 실패 시 예약 되돌림 ---------------------------------------------------------------
reset
printf 'ratelimited' > "$LOG/fail.api"
send $D1 --kind warn --from watcher "실패 뒤 재시도"
FAILRC=$RC
: > "$LOG/fail.api"
send $D1B --kind warn --from watcher "실패 뒤 재시도"
[ "$FAILRC" = 1 ] && [ "$(sent_n)" = 1 ] && ok "E API 실패는 보낸 것으로 치지 않아 다음 회차에 다시 보냄" || ko "E 실패 되돌림" "failrc=$FAILRC n=$(sent_n)"

# ---- F. 동시 실행 잠금 -------------------------------------------------------------------------
reset
mkdir -p "$SB/c"
for i in 1 2 3 4 5 6 7 8; do
  ( python3 "$AD" check --dir "$CFG" --from w --kind warn --title "동시" > "$SB/c/$i" 2>&1 ) &
done
wait
S=$(cat "$SB"/c/* | grep -c '^send ' || true)
U=$(cat "$SB"/c/* | grep -c '^suppress$' || true)
[ "$S" = 1 ] && [ "$U" = 7 ] && ok "F 동시 8건 → 1건만 보냄, 7건 막음(잠금)" || ko "F 동시 실행" "send=$S suppress=$U"

# ---- G. 판정 도구 고장은 전송 쪽으로 ------------------------------------------------------------
reset
printf '{broken' > "$CFG/alert-dedup.json"
send $D1 --kind warn --from watcher "손상된 상태 파일"
[ "$(sent_n)" = 1 ] && ok "G 상태 파일이 깨져 있어도 보냄(알림을 잃지 않음)" || ko "G 손상" "n=$(sent_n)"
reset
LRM_ALERT_DEDUP="$SB/없는파일.py" send $D1 --kind warn --from watcher "도구 없음"
[ "$(sent_n)" = 1 ] && ok "G 판정 도구가 없어도 보냄" || ko "G 도구 없음" "n=$(sent_n)"

echo "합계: PASS $PASS · FAIL $FAIL"
[ "$FAIL" = 0 ]
