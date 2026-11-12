#!/usr/bin/env bash
# restart_watch.py·card_actions.py·slack_socket.py 연동 검증. orca·ps·slack_notify.sh·resume_all.sh 는 모두 가짜이며
# 실제 Orca·Slack·허브 기동은 쓰지 않음. 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함.
# RW_SCRIPTS=<폴더> 를 주면 그 폴더의 스크립트로 시험함(변이 검증용).
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS=${RW_SCRIPTS:-$HERE/../../hubs/scripts}
RW="$SCRIPTS/restart_watch.py" CA="$SCRIPTS/card_actions.py" SOCK="$SCRIPTS/slack_socket.py"
DRIVER="$HERE/../slack_socket/driver.py"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-restartwatch.XXXXXX")
trap 'for f in "$SB"/*/pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; done; wait 2>/dev/null; rm -rf "${SB:?}"' EXIT
BIN="$SB/bin"; mkdir -p "$BIN"
# 가짜 orca: status --json 은 $FAKE_STATUS 파일 내용, FAKE_ORCA_FAIL=1 이면 실패. 그 밖의 호출은 로그에 남김
cat > "$BIN/orca" <<'SH'
#!/bin/sh
[ "${FAKE_ORCA_FAIL:-}" = 1 ] && { echo "boom" >&2; exit 1; }
case "$1" in status) cat "$FAKE_STATUS" ;; *) printf '%s\n' "$*" >> "$FAKE_ORCA_LOG" ;; esac
SH
# 가짜 ps: 시작 시각은 $FAKE_LSTART
printf '#!/bin/sh\necho "$FAKE_LSTART"\n' > "$BIN/ps"
cat > "$BIN/notify" <<'SH'
#!/bin/bash
[ "${FAKE_NOTIFY_FAIL:-}" = 1 ] && exit 1
{ printf '%s\n' "$@"; echo '---'; } >> "$FAKE_NOTIFY_LOG"
SH
cat > "$BIN/resume_all.sh" <<'SH'
#!/bin/bash
printf '%s\n' "$*" >> "$FAKE_RESUME_LOG"
SH
chmod +x "$BIN"/*

export LRM_HUB_DIR="$SB/hub" LRM_HUB_STATUS_DIR="$SB/status" LRM_ORCA="$BIN/orca" LRM_NOTIFY="$BIN/notify" LRM_RESUME_ALL="$BIN/resume_all.sh"
export HUBS_TSV="$SB/hubs.tsv" FAKE_STATUS="$SB/status.json" FAKE_NOTIFY_LOG="$SB/notify.log" FAKE_RESUME_LOG="$SB/resume.log" FAKE_ORCA_LOG="$SB/orca.log"
export PATH="$BIN:$PATH"
mkdir -p "$LRM_HUB_DIR" "$LRM_HUB_STATUS_DIR"; : > "$FAKE_NOTIFY_LOG"; : > "$FAKE_RESUME_LOG"
printf '# slug\tname\trole\tws\trepo\nalpha\t알파 허브\t-\t-\t-\nbeta\t베타 허브\t-\t-\t-\ngamma\t감마 허브\t-\t-\t-\ndelta\t델타 허브\t-\t-\t-\n' > "$HUBS_TSV"

NOW=$(python3 -c "from datetime import datetime,timezone;print(datetime.now(timezone.utc).isoformat(timespec='seconds'))")
OLD=$(python3 -c "from datetime import datetime,timedelta,timezone;print((datetime.now(timezone.utc)-timedelta(hours=30)).isoformat(timespec='seconds'))")
mkst() { printf '{"slug":"%s","updatedAt":"%s","runId":"","tasks":%s,"branches":[],"workers":[],"next":"","blockers":[]}\n' "$1" "$2" "$3" > "$LRM_HUB_STATUS_DIR/$1.json"; }
mkst alpha "$NOW" '["기능 A 구현 LRM-266","기능 B"]'
mkst beta "$NOW" '["<!channel> 베타 작업"]'
mkst gamma "$NOW" '[]'           # 진행 작업 없음 → 제외
mkst delta "$OLD" '["낡은 작업"]'  # 30시간 전 → 제외
status() { printf '{"ok":true,"result":{"app":{"running":true,"pid":%s},"runtime":{"state":"ready","runtimeId":"%s"}}}' "$1" "$2" > "$FAKE_STATUS"; }
rw() { python3 -I "$RW" check "$@"; }
cards() { grep -c '^---$' "$FAKE_NOTIFY_LOG" || true; }
stfield() { python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" "$LRM_HUB_DIR/restart-watch.json" "$1" 2>/dev/null; }
export FAKE_LSTART="Thu Nov 12 13:16:37 2026"

# ---- 1. 첫 실행: 기록만 ------------------------------------------------------------------------
status 100 rt-A
out=$(rw --json); chk "첫 실행: 카드 없음·상태 first_run" '[ "$(cards)" = 0 ] && echo "$out" | grep -q "\"first_run\""'
chk "첫 실행: 신원 저장" '[ -f "$LRM_HUB_DIR/restart-watch.json" ] && grep -q rt-A "$LRM_HUB_DIR/restart-watch.json"'
out=$(rw --json); chk "변화 없음: 카드 없음·상태 same" '[ "$(cards)" = 0 ] && echo "$out" | grep -q "\"same\""'

# ---- 2. 조회 실패: 판정 불가, 재시작 아님 -----------------------------------------------------
FAKE_ORCA_FAIL=1 rw --json > "$SB/o"; rc=$?
chk "조회 실패: unavailable·카드 없음·종료 0" '[ $rc = 0 ] && grep -q unavailable "$SB/o" && [ "$(cards)" = 0 ]'
chk "조회 실패: 저장 신원 유지·판정 불가 기록" 'grep -q rt-A "$LRM_HUB_DIR/restart-watch.json" && [ "$(stfield lastProbe)" = unavailable ]'
printf '{"ok":true,"result":{"app":{"running":false},"runtime":{}}}' > "$FAKE_STATUS"; rw --json > "$SB/o"
chk "앱 미실행 응답도 판정 불가" 'grep -q unavailable "$SB/o" && [ "$(cards)" = 0 ]'
printf 'not json' > "$FAKE_STATUS"; rw --json > "$SB/o"
chk "응답 형식 이상도 판정 불가" 'grep -q unavailable "$SB/o" && [ "$(cards)" = 0 ]'
status 100 rt-A; rw --json > "$SB/o"
chk "실패 뒤 같은 신원이면 재시작 아님" 'grep -q "\"same\"" "$SB/o" && [ "$(cards)" = 0 ]'

# ---- 3. dry-run: 문구만 -----------------------------------------------------------------------
status 200 rt-B
before=$(cat "$LRM_HUB_DIR/restart-watch.json")
rw --dry-run > "$SB/o"
chk "dry-run: 카드 문구 출력, 전송·저장 없음" 'grep -q "Orca 재시작 감지" "$SB/o" && [ "$(cards)" = 0 ] && [ "$before" = "$(cat "$LRM_HUB_DIR/restart-watch.json")" ]'

# ---- 4. 재시작 1회: 카드 1개 -----------------------------------------------------------------
rw --json > "$SB/o"
chk "재시작: 카드 1개" '[ "$(cards)" = 1 ] && grep -q "\"carded\"" "$SB/o"'
chk "카드: 결정 카드·선택지 이어받기/보류·D-RS 번호" 'grep -qx -- "--kind" "$FAKE_NOTIFY_LOG" && grep -qx "decide" "$FAKE_NOTIFY_LOG" && grep -qx "1=이어받기,2=보류" "$FAKE_NOTIFY_LOG" && grep -Eqx "D-RS[0-9a-f]{10}" "$FAKE_NOTIFY_LOG"'
chk "카드: 제목 첫 줄 'Orca 재시작 감지'" 'grep -qx "Orca 재시작 감지" <(sed -n "1,/^---\$/p" "$FAKE_NOTIFY_LOG" | tr "\n" "\n" | awk "BEGIN{RS=\"\"} {print}" ) || grep -q "^Orca 재시작 감지" "$FAKE_NOTIFY_LOG"'
chk "카드: 진행 작업 있는 허브(alpha·beta)만, 작업 없음(gamma)·낡음(delta) 제외" 'grep -q "alpha · 알파 허브 — 기능 A 구현 LRM-266 외 1건" "$FAKE_NOTIFY_LOG" && grep -q "beta · 베타 허브" "$FAKE_NOTIFY_LOG" && ! grep -q "gamma" "$FAKE_NOTIFY_LOG" && ! grep -q "delta" "$FAKE_NOTIFY_LOG"'
chk "카드: Slack 멘션·링크 문자 제거" '! grep -q "<!channel>" "$FAKE_NOTIFY_LOG" && grep -q "!channel 베타 작업" "$FAKE_NOTIFY_LOG"'
chk "카드: 허브 줄은 '참고:' 아래(티켓 번호 검사 제외 구간)" 'awk "/^참고:/{f=1} f&&/LRM-266/{ok=1} END{exit !ok}" "$FAKE_NOTIFY_LOG"'
DID=$(grep -Eo "D-RS[0-9a-f]{10}" "$FAKE_NOTIFY_LOG" | head -1)
chk "후속 동작 등록: resume·대상 alpha,beta" 'python3 -I "$CA" list | grep -q "^$DID	resume	.*alpha.*beta"'

# ---- 5. 같은 재시작: 중복 없음 ---------------------------------------------------------------
rw --json > "$SB/o"; rw --json > "$SB/o2"
chk "같은 재시작 재확인: 카드 추가 없음(총 1개)" '[ "$(cards)" = 1 ] && grep -q "\"same\"" "$SB/o2"'
# 신원 저장이 어긋나 같은 재시작이 다시 변화로 보이는 경우에도 중복 카드 없음
python3 - "$LRM_HUB_DIR/restart-watch.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["identity"]={"runtimeId":"rt-A","pid":100,"startedAt":d["identity"]["startedAt"]}; json.dump(d,open(p,"w"))
PY
rw --json > "$SB/o"
chk "같은 재시작 키 재감지: dup·카드 추가 없음" 'grep -q "\"dup\"" "$SB/o" && [ "$(cards)" = 1 ]'

# ---- 6. 다음 재시작(pid 만 바뀜): 새 카드 -------------------------------------------------------
status 300 rt-B; rw --json > "$SB/o"
chk "pid 만 바뀐 재시작: 새 카드(총 2개)" '[ "$(cards)" = 2 ]'

# ---- 7. 전송 실패: 저장값 유지·다음 주기 재시도 ---------------------------------------------------
status 400 rt-C
FAKE_NOTIFY_FAIL=1 rw --json > "$SB/o" 2>"$SB/e"; rc=$?
chk "전송 실패: 종료 1·원인 안내·등록 철회" '[ $rc = 1 ] && grep -q "카드 전송 실패" "$SB/e" && ! python3 -I "$CA" list | grep -q "rt-C"'
rw --json > "$SB/o"
chk "전송 실패 뒤 재시도: 카드 1개 추가(총 3개)" '[ "$(cards)" = 3 ] && grep -q "\"carded\"" "$SB/o"'

# ---- 8. 대상 허브 없음: 카드 없음 -----------------------------------------------------------------
rm -f "$LRM_HUB_STATUS_DIR"/*.json
status 500 rt-D; rw --json > "$SB/o"
chk "대상 허브 없음: 카드 없음(총 3개 그대로)" '[ "$(cards)" = 3 ] && grep -q no_targets "$SB/o"'
rw --json > "$SB/o"; chk "대상 없음 재시작도 다시 처리하지 않음" '[ "$(cards)" = 3 ]'

# ---- 9. 버튼 답 처리 -----------------------------------------------------------------------------
: > "$FAKE_RESUME_LOG"
python3 -I "$CA" handle --did "$DID" --pick 2 > "$SB/o"
chk "보류(2번): resume 호출 없음" '[ ! -s "$FAKE_RESUME_LOG" ] && grep -q "동작 없음" "$SB/o"'
python3 -I "$CA" handle --did "$DID" --pick 1 > "$SB/o"
chk "보류로 지워진 카드를 다시 눌러도 동작 없음" '[ ! -s "$FAKE_RESUME_LOG" ] && grep -q "등록 없음" "$SB/o"'
python3 - <<PY
import sys; sys.path.insert(0, "$SCRIPTS")
import card_actions as c
c.register("D-RSTEST000001", "resume", {"slugs": ["alpha", "beta"]})
PY
python3 -I "$CA" handle --did D-RSTEST000001 --pick 1 > "$SB/o"
chk "이어받기(1번): resume_all.sh --only alpha --only beta 만 호출" '[ "$(cat "$FAKE_RESUME_LOG")" = "--only alpha --only beta" ]' "$(cat "$FAKE_RESUME_LOG")"
python3 -I "$CA" handle --did D-RSTEST000001 --pick 1 > /dev/null
chk "같은 카드 두 번 눌러도 resume 은 한 번" '[ "$(wc -l < "$FAKE_RESUME_LOG" | tr -d " ")" = 1 ]'

# ---- 10. 소켓 수신기 연동: 버튼 → 후속 동작, 주기 감시 ---------------------------------------------
ME=Uuser1
MSG='{"ts":"1794383274.541929","text":"Orca 재시작 감지","attachments":[{"color":"#F2994A","blocks":[{"type":"section","text":{"type":"mrkdwn","text":"*Orca 재시작 감지*\n본문"}},{"type":"actions","block_id":"lrm_decide:D-RSBTN0000001","elements":[]}]}]}'
act() { printf '{"at":%s,"type":"interactive","envelope_id":"%s","payload":{"type":"block_actions","user":{"id":"%s"},"trigger_id":"t1","channel":{"id":"Dch1"},"message":%s,"actions":[{"block_id":"lrm_decide:%s","action_id":"pick_%s","value":"%s|%s","text":{"type":"plain_text","text":"%s"}}]}}' "$1" "$2" "$ME" "$MSG" "$3" "$4" "$3" "$4" "$5"; }
# sockrun 이름 이벤트JSON [환경...]: 가짜 SDK 로 수신기를 띄움. 허브 폴더에 등록 항목을 미리 만들어 둠
sockrun() {
  local n=$1 ev=$2; shift 2; local d="$SB/$n"; mkdir -p "$d/hub"
  printf 'SLACK_APP_TOKEN=%s\nSLACK_BOT_TOKEN=fake-bot\nSLACK_USER_ID=%s\n' "xapp""-1-FAKE0123456789" "$ME" > "$d/hub/slack-bot.env"
  printf '#!/bin/sh\necho "{\\"ok\\":1}"\n' > "$d/hub/slack_poll.sh"
  printf '{"events":[%s]}' "$ev" > "$d/scen.json"; : > "$d/calls"
  LRM_HUB_DIR="$d/hub" python3 - <<PY
import sys; sys.path.insert(0, "$SCRIPTS")
import card_actions as c
c.register("D-RSBTN0000001", "resume", {"slugs": ["alpha", "beta"]})
PY
  env LRM_HUB_DIR="$d/hub" FAKE_SCENARIO="$d/scen.json" FAKE_CALLS="$d/calls" LRM_PUSH_MAIN=/fake/main \
    LRM_SOCKET_CHECK_S=0.1 LRM_SOCKET_DOWN_S=0.6 LRM_SOCKET_REFRESH_S=0 LRM_SOCKET_ALIVE_S=600 LRM_SOCKET_DEBOUNCE_S=0.1 "$@" \
    python3 -I "$DRIVER" "$SOCK" > "$d/out" 2>&1 &
  echo $! > "$d/pid"
}
wf() { local f="$SB/$1/$2" i; for ((i = 0; i < 100; i++)); do grep -Eq "$3" "$f" 2>/dev/null && return 0; sleep 0.05; done; return 1; }
: > "$FAKE_RESUME_LOG"; : > "$FAKE_ORCA_LOG"; : > "$FAKE_NOTIFY_LOG"
sockrun k1 "$(act 0.4 b1 D-RSBTN0000001 1 '1. 이어받기')"
if wf k1 out '후속 동작 rc=0'; then
  chk "소켓 ✅ 버튼: resume_all.sh --only alpha --only beta 호출" '[ "$(cat "$FAKE_RESUME_LOG")" = "--only alpha --only beta" ]' "$(cat "$FAKE_RESUME_LOG")"
else ko "소켓 ✅ 버튼" "$(tail -3 "$SB/k1/out")"; fi
chk "소켓 ✅ 버튼: 메인 터미널로 전달하지 않음" '[ ! -s "$FAKE_ORCA_LOG" ]'
chk "소켓 ✅ 버튼: 카드 닫힘(chat_update 선택됨)" 'grep -q "web chat_update .*선택됨" "$SB/k1/calls"'
sleep 0.4
chk "소켓 ✅ 버튼: 스레드에 완료 알림(--kind done)" 'grep -qx done "$FAKE_NOTIFY_LOG"'
kill "$(cat "$SB/k1/pid")" 2>/dev/null
: > "$FAKE_RESUME_LOG"; : > "$FAKE_ORCA_LOG"
sockrun k2 "$(act 0.4 b2 D-RSBTN0000001 2 '2. 보류')"
wf k2 out '후속 동작 rc=0 보류'; sleep 0.3
chk "소켓 ❌ 보류: 아무것도 실행하지 않음" '[ ! -s "$FAKE_RESUME_LOG" ] && [ ! -s "$FAKE_ORCA_LOG" ]'
chk "소켓 ❌ 보류: 등록 항목만 지워짐" '! grep -q D-RSBTN0000001 "$SB/k2/hub/card-actions.json"'
kill "$(cat "$SB/k2/pid")" 2>/dev/null
# 주기 감시: 소켓 감시 루프가 restart_watch.py check 를 부름(첫 실행이라 카드 없음). 주기 0 이면 끔
status 900 rt-Z
sockrun k3 '' LRM_SOCKET_WATCH_S=0.3
wf k3 out '감시 restart_watch.py rc=0 restart_watch: first_run' && ok "소켓 감시 루프가 restart_watch.py check 를 주기 실행" || ko "주기 감시" "$(tail -3 "$SB/k3/out")"
kill "$(cat "$SB/k3/pid")" 2>/dev/null
sockrun k4 '' LRM_SOCKET_WATCH_S=0; sleep 1.2
chk "WATCH_S=0 이면 감시기를 부르지 않음" '! grep -q "감시 restart_watch" "$SB/k4/out"'
kill "$(cat "$SB/k4/pid")" 2>/dev/null

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
