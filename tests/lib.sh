#!/bin/bash
# 셸 테스트 공통 도구. 가짜 토큰·로컬 스텁만 사용하고 실제 Slack·운영 폴더는 건드리지 않음.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 검사 대상 스크립트 폴더. 변이 검증이 변형 사본을 가리킬 때 덮어씀
SCRIPTS="${LRM_SCRIPT_DIR:-$ROOT/lrm-hubs}"
PASS=0 FAIL=0
STUB_PID=""

# 임시 허브 폴더 준비. 가짜 인증값 파일과 호출 기록용 즉답·대기 스텁을 넣음
new_hub() {
  HUB="$(mktemp -d)"; export LRM_HUB_DIR="$HUB"
  printf 'SLACK_BOT_TOKEN=xoxb-TEST-FAKE\nSLACK_USER_ID=UTEST\n' > "$HUB/slack-bot.env"
  printf '#!/bin/bash\necho x >> "%s/cmd-calls"\n' "$HUB" > "$HUB/slack_cmd.sh"
  printf '#!/bin/bash\necho x >> "%s/pending-calls"\n' "$HUB" > "$HUB/slack_pending.sh"
  chmod +x "$HUB/slack_cmd.sh" "$HUB/slack_pending.sh"
  date +%s > "$HUB/.pending-last"  # 기본은 2시간 주기 미도래
  LOG="$HUB/requests.jsonl"; : > "$LOG"
}

# 스텁 시작: start_stub <스크립트 JSON 문자열>
start_stub() {
  printf '%s' "$1" > "$HUB/stub-script.json"
  python3 "$ROOT/tests/stub_slack.py" "$HUB/stub-script.json" "$LOG" "$HUB/port" &
  STUB_PID=$!
  for _ in $(seq 1 50); do [ -s "$HUB/port" ] && break; sleep 0.1; done
  export SLACK_API_BASE="http://127.0.0.1:$(cat "$HUB/port")"
}

stop_stub() { [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null; wait "$STUB_PID" 2>/dev/null; STUB_PID=""; rm -rf "$HUB"; }

# 요청 로그에서 메서드 호출 수
count() { grep -c "\"method\": \"$1\"" "$LOG" || true; }

# 밀리초 시각. Retry-After 대기 시간 검증용
now_ms() { python3 -c 'import time;print(int(time.time()*1000))'; }

# check <설명> <기대값> <실제값>
check() {
  if [ "$2" = "$3" ]; then PASS=$((PASS+1)); echo "  ok   $1"
  else FAIL=$((FAIL+1)); echo "  FAIL $1 — 기대 [$2] 실제 [$3]"; fi
}

summary() { echo "통과 $PASS, 실패 $FAIL"; [ "$FAIL" = 0 ]; }
