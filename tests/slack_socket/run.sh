#!/usr/bin/env bash
# slack_socket.py 연결 감시 검증. slack_sdk 는 가짜 모듈(driver.py)로 대체해 실제 Slack·토큰·네트워크 없이 시험함.
# 감시 간격·임계는 환경 변수로 줄여 시험함(코드에 시험 전용 분기 없음). 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함.
# SOCK_SCRIPT=<경로> 를 주면 그 수신기로 시험함(변이 검증용).
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
SOCK=${SOCK_SCRIPT:-$HERE/../../hubs/scripts/slack_socket.py}
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-slacksock.XXXXXX")
cleanup() {
  local f
  for f in "$SB"/*/pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; done
  wait 2>/dev/null
  rm -rf "${SB:?}"
}
trap cleanup EXIT

# 토큰·ID 는 소스에 통째로 두지 않고 실행 때 조립함
APPTOK="xapp""-1-FAKEAPPTOKEN0123456789"
BOTTOK="fake-bot-token-9876543210"
ME=Uuser1
LEAK=LEAK_DETAIL_SENTINEL
LOGRE='^[0-9]{2}-[0-9]{2} [0-9:]{8} \[socket\] '

# start 이름 시나리오JSON [VAR=값 ...]: 수신기를 백그라운드로 띄움. 기본 감시 간격 0.1초·끊김 0.6초·정기 재연결 끔·생존 기록 사실상 끔
start() {
  local name=$1 scen=$2; shift 2
  local d="$SB/$name"
  mkdir -p "$d/hub"
  printf 'SLACK_APP_TOKEN=%s\nSLACK_BOT_TOKEN=%s\nSLACK_USER_ID=%s\n' "${APP_OVERRIDE-$APPTOK}" "$BOTTOK" "$ME" > "$d/hub/slack-bot.env"
  cp "$HERE/fake_poll.sh" "$d/hub/slack_poll.sh"
  printf '%s' "$scen" > "$d/scen.json"
  : > "$d/calls"
  env LRM_HUB_DIR="$d/hub" FAKE_SCENARIO="$d/scen.json" FAKE_CALLS="$d/calls" \
    LRM_SOCKET_CHECK_S=0.1 LRM_SOCKET_DOWN_S=0.6 LRM_SOCKET_REFRESH_S=0 LRM_SOCKET_ALIVE_S=600 LRM_SOCKET_DEBOUNCE_S=0.1 \
    "$@" python3 -I "$HERE/driver.py" "$SOCK" > "$d/out" 2>&1 &
  echo $! > "$d/pid"
}
stop() { local d="$SB/$1"; kill "$(cat "$d/pid")" 2>/dev/null; wait "$(cat "$d/pid")" 2>/dev/null; }
# waitfor 이름 파일 정규식 [초]: 파일에 정규식이 나올 때까지 대기. 나오면 0
waitfor() {
  local f="$SB/$1/$2" i max=$(( ${4:-6} * 20 ))
  for ((i = 0; i < max; i++)); do grep -Eq "$3" "$f" && return 0; sleep 0.05; done
  return 1
}
# waitexit 이름 [초]: 프로세스 종료를 기다려 종료 코드를 RC 에 담음. 시간 초과면 1
waitexit() {
  local pid i max=$(( ${2:-8} * 20 )); pid=$(cat "$SB/$1/pid")
  for ((i = 0; i < max; i++)); do
    kill -0 "$pid" 2>/dev/null || { wait "$pid"; RC=$?; return 0; }
    sleep 0.05
  done
  RC=timeout; return 1
}
count() { grep -Ec "$3" "$SB/$1/$2" || true; }  # 이름 파일 정규식 → 줄 수
# retired_order 이름 연결번호: 그 클라이언트의 정리 호출을 기록 순서대로 한 줄에 담음
retired_order() { awk -v n="$2" '$3 == n && $2 ~ /^(close|disconnect|terminate|runner_shutdown)$/ { printf "%s ", $2 }' "$SB/$1/calls"; }
at() { awk -v n="$2" -v w="$3" '$2 == w && $3 == n { print $1; exit }' "$SB/$1/calls"; }  # 이름 연결번호 동작 → 시각

# ---- 1. 시작: 연결됨·시작 수집 -------------------------------------------------------------
start s1 '{}'
waitfor s1 out '시작 → 수집 rc=0 \{"ok":1\}' && grep -Eq "${LOGRE}연결됨\$" "$SB/s1/out" && ok "시작: '연결됨' 뒤 '시작 → 수집' 로그(기존 문구)" || ko "시작 로그" "$(head -c 200 "$SB/s1/out")"
stop s1

# ---- 2. 끊김이 임계(0.6초) 이상 → 재연결 ----------------------------------------------------
# 연결 직후 0.2초에 끊기고 복구되지 않음. 임계 전에는 재연결하지 않고, 임계 뒤 새 클라이언트로 한 번 재연결해야 함
start s2 '{"lifetimes":[[0.2,null]]}' LRM_SOCKET_ALIVE_S=0.25
waitfor s2 calls ' connect 2 ok' && waitfor s2 out '재연결 → 수집 rc=0' && ok "끊김 임계 뒤 재연결 호출·'재연결 → 수집'" || ko "끊김 재연결" "$(tr '\n' ' ' < "$SB/s2/calls" | cut -c1-160)"
t1=$(at s2 1 connect); t2=$(at s2 2 connect)
awk -v a="${t1:-0}" -v b="${t2:-0}" 'BEGIN { exit !(b > 0 && b - a >= 0.75) }' && ok "임계 이전(끊김 0.2초+임계 0.6초 미만)에는 재연결하지 않음" || ko "임계 이전 재연결" "간격 $(awk -v a="${t1:-0}" -v b="${t2:-0}" 'BEGIN { print b - a }')초"
grep -Eq "${LOGRE}재연결됨\$" "$SB/s2/out" && [ "$(count s2 out '재연결됨$')" = 1 ] && ok "'재연결됨' 로그 한 번" || ko "재연결됨 로그" "$(count s2 out '재연결됨$')건"
[ "$(count s2 calls ' new ')" = 2 ] && [ "$(count s2 calls ' close 1$')" = 1 ] && [ "$(count s2 calls ' close 2$')" = 0 ] && ok "새 클라이언트를 만들고 옛 클라이언트만 닫음(close 재사용 안 함)" || ko "클라이언트 교체" "$(tr '\n' ' ' < "$SB/s2/calls" | cut -c1-200)"
[ "$(retired_order s2 1)" = "close disconnect terminate runner_shutdown " ] && [ -z "$(retired_order s2 2)" ] && ok "교체 성공: 옛 클라이언트만 close → disconnect → runner 종료 순서로 정리(새 클라이언트는 그대로)" || ko "교체 정리 순서" "$(retired_order s2 1)"
waitfor s2 out '생존 연결=아니오' && waitfor s2 out '생존 연결=예 마지막이벤트=없음 재연결=1회' && ok "생존 줄: 끊긴 동안 '아니오', 재연결 뒤 '예·재연결=1회'" || ko "생존 줄 상태" "$(grep '생존' "$SB/s2/out" | head -3 | tr '\n' '|')"
! grep -q '시간 초과' "$SB/s2/out" && ok "정상 경로: runner 정리가 상한 안에 끝나 '시간 초과' 로그 없음" || ko "정상 경로 시간 초과" "$(grep '시간 초과' "$SB/s2/out" | head -1)"
stop s2

# ---- 3. 임계보다 짧은 끊김은 SDK 자체 복구에 맡기고 재연결됨·수집만 ---------------------------------
start s3 '{"lifetimes":[[0.2,0.5]]}' LRM_SOCKET_DOWN_S=1.5
waitfor s3 out '재연결됨$' && waitfor s3 out '재연결 → 수집 rc=0' || ko "자체 복구 감지" "$(head -c 200 "$SB/s3/out")"
sleep 0.4
[ "$(count s3 calls ' connect ')" = 1 ] && ok "임계 안에 복구되면 재연결 호출 없이 '재연결됨'·'재연결 → 수집'만" || ko "불필요한 재연결" "$(tr '\n' ' ' < "$SB/s3/calls" | cut -c1-200)"
stop s3

# ---- 4. 재연결 연속 5회 실패 → 종료 코드 3 ----------------------------------------------------
start s4 '{"lifetimes":[[0.2,null]],"connect":["ok"],"default":"fail"}'
waitexit s4 8
[ "${RC:-}" = 3 ] && ok "재연결 5연속 실패 → 종료 코드 3" || ko "5연속 실패 종료" "rc=${RC:-}"
[ "$(count s4 calls ' connect [0-9]+ fail')" = 5 ] && grep -q '재연결 실패 FakeNetError 1/5' "$SB/s4/out" && grep -q '재연결 실패 FakeNetError 5/5' "$SB/s4/out" && ok "실패 5회까지만 시도·로그에 종류 이름과 횟수만" || ko "실패 횟수 로그" "$(grep -c '재연결 실패' "$SB/s4/out")건"
! grep -q "$LEAK" "$SB/s4/out" && ok "실패 로그에 예외 본문 없음(종류 이름만)" || ko "예외 본문 유출" ""
# 4회 실패 뒤 성공하면 연속이 끊겨, 두 번째 끊김에서 다시 4회 실패해도 종료하지 않음
start s4b '{"lifetimes":[[0.2,null],[0.2,null]],"connect":["ok","fail","fail","fail","fail","ok","fail","fail","fail","fail","ok"]}'
waitfor s4b calls ' connect 11 ok' 10 && sleep 0.4 && kill -0 "$(cat "$SB/s4b/pid")" 2>/dev/null && [ "$(count s4b out '재연결됨$')" = 2 ] && ok "실패 4회→성공을 두 번 반복: 연속 횟수가 초기화돼 종료하지 않음" || ko "연속 실패 초기화" "$(tail -3 "$SB/s4b/out" | tr '\n' '|')"
stop s4b
# 예외 없이 연결 안 되는 실패(실제 SDK 의 웹소켓 단계 실패)도 실패로 집계해 5연속에서 종료함
start s11 '{"lifetimes":[[0.2,null]],"connect":["ok"],"default":"silent"}'
waitexit s11 8
[ "${RC:-}" = 3 ] && [ "$(count s11 out '재연결됨$')" = 0 ] && ok "연결 안 됨(예외 없음) 5연속 → 종료 코드 3·'재연결됨' 로그 0건" || ko "silent 5연속 종료" "rc=${RC:-} 재연결됨 $(count s11 out '재연결됨$')건"
[ "$(count s11 calls ' connect [0-9]+ silent')" = 5 ] && grep -q '재연결 실패 ConnectionError 1/5' "$SB/s11/out" && grep -q '재연결 실패 ConnectionError 5/5' "$SB/s11/out" && ok "silent 연결도 실패 횟수·종류 이름(ConnectionError)으로 기록" || ko "silent 실패 로그" "$(grep -c '재연결 실패' "$SB/s11/out")건"
[ "$(count s11 calls ' runner_shutdown ')" = "$(count s11 calls ' new ')" ] && ok "실패한 새 클라이언트와 종료 직전 클라이언트까지 모두 runner 정리(생성 $(count s11 calls ' new ')개)" || ko "실패 경로 정리" "생성 $(count s11 calls ' new ') runner 종료 $(count s11 calls ' runner_shutdown ')"
# SDK 자체 복구 때 실패 횟수 초기화: 4회 실패 → 자체 복구 → 다시 끊겨 1회 실패해도 합산 5가 되지 않아 종료하지 않음
start s16 '{"lifetimes":[[0.2,2.6,3.0,null]],"connect":["ok","fail","fail","fail","fail","fail","ok"]}' LRM_SOCKET_CHECK_S=0.4 LRM_SOCKET_DOWN_S=0.5
waitfor s16 calls ' connect 7 ok' 10 && sleep 0.5 && kill -0 "$(cat "$SB/s16/pid")" 2>/dev/null && [ "$(count s16 out '재연결됨$')" = 2 ] && ok "SDK 자체 복구 때 연속 실패 횟수 초기화(4회 실패→복구→1회 실패는 종료 아님)" || ko "자체 복구 초기화" "$(tail -3 "$SB/s16/out" | tr '\n' '|')"
stop s16

# ---- 5. 정기 재연결, 0 이면 끔 ---------------------------------------------------------------
start s5 '{}' LRM_SOCKET_REFRESH_S=0.6
waitfor s5 out '정기 재연결 → 수집 rc=0' && grep -Eq "${LOGRE}정기 재연결\$" "$SB/s5/out" && ok "정기 재연결: 로그 '정기 재연결'·수집" || ko "정기 재연결" "$(head -c 200 "$SB/s5/out")"
[ "$(count s5 calls ' connect 2 ok')" -ge 1 ] && [ "$(count s5 calls ' close 1$')" = 1 ] && ok "정기 재연결: 새 클라이언트 연결 뒤 옛 클라이언트 닫음" || ko "정기 재연결 호출" "$(tr '\n' ' ' < "$SB/s5/calls" | cut -c1-200)"
sleep 1.5
n=$(count s5 calls ' connect ')
[ "$n" -ge 2 ] && [ "$n" -le 8 ] && ok "정기 재연결 주기 유지: 주기(0.6초)마다 한 번씩, 점검(0.1초)마다 재연결하지 않음 (연결 ${n}회)" || ko "정기 재연결 폭주" "연결 ${n}회"
! grep -Eq "${LOGRE}재연결됨\$" "$SB/s5/out" && ok "정기 재연결은 '재연결됨'(끊김 복구) 로그를 섞지 않음" || ko "로그 혼동" ""
stop s5
start s6 '{}' LRM_SOCKET_REFRESH_S=0
sleep 1.6
[ "$(count s6 calls ' connect ')" = 1 ] && ! grep -q '정기 재연결' "$SB/s6/out" && ok "LRM_SOCKET_REFRESH_S=0: 정기 재연결 끔" || ko "정기 재연결 끔" "$(tr '\n' ' ' < "$SB/s6/calls" | cut -c1-200)"
stop s6
start s6b '{"connect":["ok","fail","ok"]}' LRM_SOCKET_REFRESH_S=0.5
waitfor s6b out '정기 재연결 실패 FakeNetError$' && sleep 0.3 && ! grep -q '재연결 실패 FakeNetError [0-9]/' "$SB/s6b/out" && kill -0 "$(cat "$SB/s6b/pid")" 2>/dev/null && ok "정기 재연결 실패: 옛 연결 유지·종료하지 않음" || ko "정기 재연결 실패" "$(tail -3 "$SB/s6b/out" | tr '\n' '|')"
stop s6b

# ---- 6. 생존 로그 형식·주기 ------------------------------------------------------------------
# 1.3초에 이벤트 1건(봇 글이라 수집은 안 함, 수신 시각만 갱신). 그 전에는 '없음', 뒤에는 '0분 전'
EV_BOT='{"at":1.3,"type":"events_api","envelope_id":"eb","payload":{"event":{"type":"message","channel_type":"im","bot_id":"Bbot1"}}}'
start s7 "{\"events\":[$EV_BOT]}" LRM_SOCKET_ALIVE_S=0.5
sleep 2.6
n=$(count s7 out '생존 ')
[ "$n" -ge 2 ] && [ "$n" -le 5 ] && ok "생존 줄은 주기(0.5초)마다 한 줄씩, 매 점검마다 찍지 않음 (2.6초 동안 ${n}줄)" || ko "생존 줄 주기" "${n}줄"
bad=$(grep '생존' "$SB/s7/out" | grep -Evc "${LOGRE}생존 연결=(예|아니오) 마지막이벤트=(없음|[0-9]+분 전) 재연결=[0-9]+회\$" || true)
[ "$n" -ge 1 ] && [ "$bad" = 0 ] && ok "생존 줄 형식 '생존 연결=예|아니오 마지막이벤트=N분 전 재연결=n회' 고정(다른 값 없음)" || ko "생존 줄 형식" "$(grep '생존' "$SB/s7/out" | head -2 | tr '\n' '|')"
grep -q '생존 연결=예 마지막이벤트=없음 재연결=0회' "$SB/s7/out" && grep -q '생존 연결=예 마지막이벤트=0분 전 재연결=0회' "$SB/s7/out" && ok "마지막 이벤트: 수신 전 '없음', 수신 뒤 '0분 전'(봇 이벤트도 수신으로 침)" || ko "마지막 이벤트 표시" "$(grep '생존' "$SB/s7/out" | tr '\n' '|' | cut -c1-240)"
stop s7

# ---- 7. 기존 이벤트 처리 불변 ----------------------------------------------------------------
ev() { # 시각 id 종류(message|reaction_added) 사용자 채널유형 [bot_id]
  local extra=""; [ -n "${6:-}" ] && extra=",\"bot_id\":\"$6\""
  printf '{"at":%s,"type":"events_api","envelope_id":"%s","payload":{"event":{"type":"%s","user":"%s","channel_type":"%s"%s}}}' "$1" "$2" "$3" "$4" "$5" "$extra"
}
EVS="$(ev 0.4 e1 message $ME im),$(ev 1.0 e2 reaction_added $ME im),$(ev 1.6 e3 message $ME im Bbot1),$(ev 2.2 e4 message Uother9 im),$(ev 2.8 e5 message $ME channel)"
EVS="$EVS,{\"at\":3.4,\"type\":\"hello\",\"envelope_id\":\"e6\"}"
start s8 "{\"events\":[$EVS]}"
sleep 4.0
[ "$(count s8 out 'DM → 수집 rc=0')" = 1 ] && [ "$(count s8 out '반응 → 수집 rc=0')" = 1 ] && ok "본인 DM → 'DM → 수집', 본인 반응 → '반응 → 수집' (기존 문구)" || ko "DM·반응 수집" "$(grep -c '수집' "$SB/s8/out")건"
[ "$(wc -l < "$SB/s8/hub/poll.calls" | tr -d ' ')" = 3 ] && ok "봇 자신·타인·채널 글·비 이벤트는 수집을 일으키지 않음(수집 3회=시작+DM+반응)" || ko "무시해야 할 이벤트" "수집 $(wc -l < "$SB/s8/hub/poll.calls" | tr -d ' ')회"
[ "$(count s8 calls ' ack ')" = 6 ] && ok "모든 요청에 응답(ack) 전송" || ko "ack" "$(count s8 calls ' ack ')건"
stop s8
# 재연결로 만든 새 클라이언트(2번)가 받은 이벤트도 처리·응답해야 함(리스너 누락 검출)
start s15 "{\"lifetimes\":[[0.2,null]],\"events_client\":2,\"events\":[$(ev 0.3 en message $ME im)]}"
waitfor s15 out 'DM → 수집 rc=0' 8 && waitfor s15 calls ' ack 2 en' && ok "새 클라이언트에 리스너가 붙어 이벤트 처리·ack(DM → 수집)" || ko "새 클라이언트 리스너" "$(tr '\n' ' ' < "$SB/s15/calls" | cut -c1-200)"
stop s15
# runner 정리가 예외를 내도 재연결은 계속되고 로그에는 종류 이름만 남음
start s14 '{"lifetimes":[[0.2,null]],"runner_error":true}'
waitfor s14 out '재연결 → 수집 rc=0' && grep -q '정리 runner 실패 FakeNetError' "$SB/s14/out" && kill -0 "$(cat "$SB/s14/pid")" 2>/dev/null && ok "runner 정리 예외는 삼키고 종류 이름만 로그" || ko "정리 예외 처리" "$(tail -3 "$SB/s14/out" | tr '\n' '|')"
stop s14

# runner 종료가 영구 대기해도(SDK join 상한 없음) 정리는 상한(0.5초) 안에 돌아와 감시를 이어감. 상한이 없으면 재연결 로그가 안 나와 대기 시간 초과로 실패
start s17 '{"lifetimes":[[0.2,null]],"runner_hang":true}' LRM_SOCKET_RETIRE_TIMEOUT_S=0.5 LRM_SOCKET_ALIVE_S=0.25
waitfor s17 out '재연결 → 수집 rc=0' 3 && waitfor s17 out '생존 연결=예 마지막이벤트=없음 재연결=1회' 3 && kill -0 "$(cat "$SB/s17/pid")" 2>/dev/null && ok "runner 종료가 영구 대기해도 상한 안에 정리가 돌아와 재연결·생존 기록이 이어짐" || ko "runner 정리 상한" "$(tail -3 "$SB/s17/out" | tr '\n' '|')"
[ "$(count s17 out '정리 runner 시간 초과 0\.5초$')" = 1 ] && grep -Eq "${LOGRE}정리 runner 시간 초과 0\.5초\$" "$SB/s17/out" && ok "시간 초과 로그 한 줄('정리 runner 시간 초과 0.5초')" || ko "시간 초과 로그" "$(grep -c '시간 초과' "$SB/s17/out")건 $(grep '시간 초과' "$SB/s17/out" | head -1)"
stop s17

# ---- 8. 설정 오류·첫 연결 실패(기존 종료 코드 2) ---------------------------------------------------
APP_OVERRIDE="notoken" start s9 '{}'
waitexit s9 4
[ "${RC:-}" = 2 ] && grep -q 'SLACK_APP_TOKEN(xapp-) 또는 SLACK_BOT_TOKEN 없음' "$SB/s9/out" && ok "xapp- 토큰 없음 → 종료 코드 2·안내 한 줄" || ko "토큰 없음" "rc=${RC:-}"
start s10 '{"connect":["fail"]}'
waitexit s10 4
[ "${RC:-}" = 2 ] && grep -q '연결 실패 FakeNetError — SLACK_APP_TOKEN·네트워크 확인' "$SB/s10/out" && ! grep -q "$LEAK" "$SB/s10/out" && ok "첫 연결 실패 → 종료 코드 2·종류 이름만" || ko "첫 연결 실패" "rc=${RC:-}"
start s12 '{"connect":["silent"]}'
waitexit s12 4
[ "${RC:-}" = 2 ] && grep -q '연결 실패 ConnectionError — SLACK_APP_TOKEN·네트워크 확인' "$SB/s12/out" && ! grep -Eq "${LOGRE}연결됨\$" "$SB/s12/out" && [ ! -e "$SB/s12/hub/poll.calls" ] && ok "첫 연결이 예외 없이 안 붙음 → 종료 코드 2·'연결됨'·수집 없음" || ko "첫 연결 silent" "rc=${RC:-} $(head -c 160 "$SB/s12/out")"
[ "$(count s12 calls ' runner_shutdown 1$')" = 1 ] && ok "첫 연결 실패 때도 클라이언트 정리" || ko "첫 연결 실패 정리" "$(tr '\n' ' ' < "$SB/s12/calls" | cut -c1-200)"

# 기본 주기(환경 변수 없음): 감시 30초·끊김 60초·정기 재연결 6시간·생존 10분·묶음 2초·runner 정리 상한 5초
want='Timing(check_s=30.0, down_s=60.0, refresh_s=21600.0, alive_s=600.0, debounce_s=2.0) retire_timeout_s=5.0'
got=$(env -i PATH="$PATH" python3 -I "$HERE/driver.py" --defaults "$SOCK" 2>&1)
[ "$got" = "$want" ] && ok "Timing.from_env 기본값 30·60·21600·600·2초·정리 상한 5초" || ko "기본값" "$got"

# ---- 10. 결정 카드 버튼(interactive) -----------------------------------------------------------
# 가짜 orca: 받은 인자를 orca.calls 에 남김. 메인 터미널 핸들은 main.env 에서 읽음
cat > "$SB/fake_orca.sh" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_ORCA_LOG"
SH
chmod +x "$SB/fake_orca.sh"
MSG='{"ts":"1794383274.541929","text":"주제","attachments":[{"color":"#F2994A","blocks":[{"type":"section","text":{"type":"mrkdwn","text":"*시험 결정*\n배경"}},{"type":"actions","block_id":"lrm_decide:D-T1","elements":[]}]}]}'
act() { # 시각 id 사용자 action_id value 글자
  printf '{"at":%s,"type":"interactive","envelope_id":"%s","payload":{"type":"block_actions","user":{"id":"%s"},"trigger_id":"trg1","channel":{"id":"Dch1"},"message":%s,"actions":[{"block_id":"lrm_decide:D-T1","action_id":"%s","value":"%s","text":{"type":"plain_text","text":"%s"}}]}}' "$1" "$2" "$3" "$MSG" "$4" "$5" "$6"
}
SUB='{"at":0.9,"type":"interactive","envelope_id":"i3","payload":{"type":"view_submission","user":{"id":"'$ME'"},"view":{"callback_id":"lrm_custom","private_metadata":"{\"did\":\"D-T1\",\"channel\":\"Dch1\",\"ts\":\"1794383274.541929\"}","state":{"values":{"answer":{"text":{"value":"둘 다  섞어서"}}}}}}}'
EVB="$(act 0.4 i1 $ME pick_2 'D-T1|2' '2. B 안'),$(act 0.6 i2 $ME custom 'D-T1|custom' '직접 입력'),$SUB,$(act 1.2 i4 Uother9 pick_1 'D-T1|1' '1. A')"
mkdir -p "$SB/s20/hub"; printf 'MAIN_TERMINAL=term-main\n' > "$SB/s20/hub/main.env"
start s20 "{\"events\":[$EVB],\"history\":[$MSG]}" LRM_ORCA="$SB/fake_orca.sh" FAKE_ORCA_LOG="$SB/s20/orca.calls"
waitfor s20 out '결정 D-T1 버튼 답 기록 · 메인 전달 성공' 4 && sleep 0.8
[ "$(count s20 calls ' ack 1 i[1-4]')" = 4 ] && ok "버튼·모달 요청 모두 ack" || ko "버튼 ack" "$(count s20 calls ' ack 1 i')"
grep -q 'web chat_update .*✅ 2. B 안 선택됨' "$SB/s20/calls" && ! grep -q 'web chat_update .*"type": "actions"' "$SB/s20/calls" && ok "선택 → 카드를 '✅ 선택됨'으로 갱신·버튼 제거" || ko "카드 갱신" "$(grep 'web chat_update' "$SB/s20/calls" | head -1 | cut -c1-200)"
grep -q 'web views_open .*"callback_id": "lrm_custom"' "$SB/s20/calls" && ok "'직접 입력' → 모달 열기" || ko "모달" ""
grep -q '결정: 시험 결정 = 2. B 안 (1794383274.541929)' "$SB/s20/orca.calls" && grep -q '결정: 시험 결정 = 직접 입력: 둘 다 섞어서 (1794383274.541929)' "$SB/s20/orca.calls" && grep -q -- '--terminal term-main' "$SB/s20/orca.calls" && ok "선택·직접 입력 모두 메인 터미널로 전달" || ko "메인 전달" "$(cat "$SB/s20/orca.calls" 2>/dev/null | cut -c1-200)"
[ "$(grep -c '"type": "button"' "$SB/s20/hub/slack-inbox.jsonl")" = 2 ] && grep -q '"pick": 2' "$SB/s20/hub/slack-inbox.jsonl" && [ "$(grep -c 1794383274.541929 "$SB/s20/hub/slack-done.txt")" = 2 ] && ok "inbox 기록 2건·done 처리" || ko "기록" "$(wc -l < "$SB/s20/hub/slack-inbox.jsonl" 2>/dev/null)"
! grep -q '= 1. A' "$SB/s20/orca.calls" && ok "다른 사용자 클릭은 무시" || ko "남의 클릭" ""
stop s20

# 메인 전달 실패(orca 실패) → 카드 스레드에 warn 경고
printf '#!/bin/sh\nexit 1\n' > "$SB/bad_orca.sh"; chmod +x "$SB/bad_orca.sh"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> "$FAKE_NOTIFY_LOG"\n' > "$SB/fake_notify.sh"; chmod +x "$SB/fake_notify.sh"
mkdir -p "$SB/s21/hub"; printf 'MAIN_TERMINAL=term-main\n' > "$SB/s21/hub/main.env"
start s21 "{\"events\":[$(act 0.4 i1 $ME pick_2 'D-T1|2' '2. B 안')]}" LRM_ORCA="$SB/bad_orca.sh" LRM_NOTIFY="$SB/fake_notify.sh" FAKE_NOTIFY_LOG="$SB/s21/notify.calls"
waitfor s21 out '메인 전달 실패' 4 && waitfor s21 notify.calls 'thread 1794383274.541929 메인 전달 실패' 4 && grep -q -- '--kind warn' "$SB/s21/notify.calls" && ok "메인 전달 실패 → 카드 스레드에 warn" || ko "전달 실패 경고" "$(cat "$SB/s21/notify.calls" 2>/dev/null)"
stop s21

# ---- 9. 토큰·ID 는 어떤 출력에도 없음 ----------------------------------------------------------
leaks=$(cat "$SB"/*/out | grep -Ec "$APPTOK|$BOTTOK|$ME|$LEAK|Uother9|Bbot1" || true)
[ "$leaks" = 0 ] && ok "모든 시나리오 로그·표준출력에 토큰·사용자 ID·예외 본문 0건" || ko "유출" "${leaks}줄"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
