#!/usr/bin/env bash
# 메인 터미널 자동 재탐색 검증(slack_push·slack_socket·set_main_terminal.sh --auto). orca 는 가짜 명령(PATH 앞)이며 실제 Orca·Slack·터미널은 쓰지 않음.
# 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함. 가짜 orca 는 tests/slack_poll/fake_orca.sh 를 공유함(list·핸들별 show 응답 추가분).
# MT_SCRIPTS=<폴더> 를 주면 그 폴더의 스크립트로 시험함(변이 검증용).
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS=${MT_SCRIPTS:-$HERE/../../hubs/scripts}
PUSH="$SCRIPTS/slack_push.sh" SETMAIN="$SCRIPTS/set_main_terminal.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-maintermal.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
FB="$SB/bin"; mkdir -p "$FB"
cp "$HERE/../slack_poll/fake_orca.sh" "$FB/orca"; chmod 755 "$FB/orca"
cat > "$FB/notify" <<'SH'
#!/bin/bash
printf '%s' "$*" | tr '\n' ' ' >> "$FAKE_ORCA_DIR/notify.log"; echo >> "$FAKE_ORCA_DIR/notify.log"
SH
chmod 755 "$FB/notify"
export LRM_PUSH_NOTIFY="$FB/notify" PATH="$FB:$PATH"
MAIN="$SB/main-checkout"
export FAKE_MAIN="$MAIN" LRM_PUSH_MAIN="$MAIN" LRM_PUSH_REPOLL=off LRM_PUSH_QUIET=0 LRM_PUSH_LIFE=3 LRM_PUSH_HOLD_RETRY=0.5
OLD=term_0000feed-0000-4000-8000-000000000001 NEW=term_0000beef-0000-4000-8000-000000000002 NEW2=term_0000cafe-0000-4000-8000-000000000003  # 가짜 핸들

newhub() { # 이름: 허브·가짜 orca 폴더. main.env 는 옛 핸들(show 가 gone 응답) + 다른 줄 2개
  H="$SB/$1/hub" FO="$SB/$1/fo"; mkdir -p "$H" "$FO"
  export LRM_HUB_DIR="$H" FAKE_ORCA_DIR="$FO" LRM_MAIN_ENV="$H/main.env"
  printf '# 주석 줄\nOTHER_KEY=keep me\nMAIN_TERMINAL=%s\nTAIL=end\n' "$OLD" > "$H/main.env"; chmod 600 "$H/main.env"
  echo gone > "$FO/show.mode.$OLD"
  : > "$H/slack-inbox.jsonl"
  jq -nc '{type:"note",ts:"1000.000001",text:"안녕",channel:"Dfake",received_at:0}' | sed 's/"received_at":0/"received_at":'"$(python3 -I -c 'import time;print(time.time())')"'/' >> "$H/slack-inbox.jsonl"
}
row() { # 핸들 제목 worktree [agent]
  jq -nc --arg h "$1" --arg t "$2" --arg p "$3" --arg a "${4:-claude}" '{handle:$h,title:$t,worktreePath:$p,connected:true,writable:true,orphaned:false,agentIdentity:$a,preview:""}'
}
setlist() { jq -sc '.' > "$FO/list.json"; }  # 행 JSON 을 stdin 으로
nsend() { [ -f "$FO/sends.log" ] && wc -l < "$FO/sends.log" | tr -d ' ' || echo 0; }
nlist() { grep -c '^list' "$FO/calls.log" 2>/dev/null || true; }
envval() { grep '^MAIN_TERMINAL=' "$H/main.env" | cut -d= -f2; }
noise() { row term_0000aaaa-0000-4000-8000-00000000000a "다른 작업" "$MAIN"; row term_0000aaaa-0000-4000-8000-00000000000b "MAIN-HANDOFF 다른 체크아웃" "$SB/elsewhere"; row term_0000aaaa-0000-4000-8000-00000000000c "MAIN-HANDOFF codex" "$MAIN" codex; }
W() { bash "$PUSH" --run; }

# ---- 후보 1개: main.env 갱신·로그·전달 계속 ---------------------------------------------------------
newhub A; { noise; row "$NEW" "MAIN-HANDOFF.md 메인" "$MAIN"; } | setlist
W
[ "$(envval)" = "$NEW" ] && ok "후보 1개 → MAIN_TERMINAL 을 새 핸들로 교체" || ko "후보 1개 교체" "$(envval)"
[ "$(sed -n 1,2p "$H/main.env")" = $'# 주석 줄\nOTHER_KEY=keep me' ] && [ "$(sed -n 3p "$H/main.env")" = TAIL=end ] && [ "$(wc -l < "$H/main.env" | tr -d ' ')" = 4 ] && ok "다른 줄(주석·키·뒤 줄) 순서·내용 보존" || ko "다른 줄 보존" "$(cat "$H/main.env")"
[ "$(stat -f %Lp "$H/main.env")" = 600 ] && ls -A "$H" | grep -q '^\.main\.env\.' && ko "임시 파일 남음" "$(ls -A "$H")" || { [ "$(stat -f %Lp "$H/main.env")" = 600 ] && ok "권한 600·임시 파일 남지 않음"; }
[ "$(nsend)" = 1 ] && [ "$(awk -F'\t' '{print $3}' "$FO/sends.log")" = "$NEW" ] && ok "같은 회차에 새 핸들로 전달 계속(전달 1회)" || ko "전달 계속" "sends=$(nsend)"
grep -q " push relink note=재탐색으로_갱신 handle=term_000 " "$H/slack-push.log" && ! grep -qF "$NEW" "$H/slack-push.log" && ok "로그에 '재탐색으로 갱신' 한 줄, 핸들은 앞 8자만" || ko "로그" "$(cat "$H/slack-push.log")"
[ ! -e "$FO/notify.log" ] && ok "성공 시 '메인 연결 끊김' 알림 없음" || ko "알림" "$(cat "$FO/notify.log")"

# ---- 후보 0개: 보류 + 알림에 후보 0 --------------------------------------------------------------
newhub B; noise | setlist; cp "$H/main.env" "$SB/B.before"
W
[ "$(nsend)" = 0 ] && cmp -s "$H/main.env" "$SB/B.before" && ok "후보 0개 → 보류, main.env 불변" || ko "후보 0 보류" "sends=$(nsend)"
grep -q '메인 연결 끊김' "$FO/notify.log" && grep -q '후보: 0개' "$FO/notify.log" && [ "$(wc -l < "$FO/notify.log" | tr -d ' ')" = 1 ] && ok "알림 문구에 후보 0개, 1회" || ko "후보 0 알림" "$(cat "$FO/notify.log" 2>/dev/null)"

# ---- 후보 2개: 보류 + 알림에 후보 2·사람이 고르게 ---------------------------------------------------
newhub C; { row "$NEW" "MAIN-HANDOFF 하나" "$MAIN"; row "$NEW2" "MAIN-HANDOFF 둘" "$MAIN"; } | setlist; cp "$H/main.env" "$SB/C.before"
W
[ "$(nsend)" = 0 ] && cmp -s "$H/main.env" "$SB/C.before" && ok "후보 2개 → 보류, main.env 불변" || ko "후보 2 보류" "sends=$(nsend)"
grep -q '후보: 2개' "$FO/notify.log" && grep -q '골라' "$FO/notify.log" && ! grep -q 'term_' "$FO/notify.log" && ok "알림 문구에 후보 2개·사람이 고르라는 안내(핸들 값 없음)" || ko "후보 2 알림" "$(cat "$FO/notify.log" 2>/dev/null)"

# ---- 조회 실패(일시 오류)는 재탐색 안 함 ------------------------------------------------------------
newhub D; { row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist; echo unavail > "$FO/show.mode.$OLD"; cp "$H/main.env" "$SB/D.before"
W
[ "$(nlist)" = 0 ] && [ "$(nsend)" = 0 ] && cmp -s "$H/main.env" "$SB/D.before" && [ ! -e "$FO/notify.log" ] && ok "show 조회 실패(runtime_unavailable) → terminal list 호출 0·main.env 불변·알림 없음" || ko "조회 실패" "list=$(nlist) sends=$(nsend)"
newhub D2; { row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist; echo fail > "$FO/list.mode"; cp "$H/main.env" "$SB/D2.before"
W
[ "$(nsend)" = 0 ] && cmp -s "$H/main.env" "$SB/D2.before" && grep -q '후보: 미확인' "$FO/notify.log" && ok "terminal list 조회 실패 → 보류, main.env 불변, 알림은 후보 미확인" || ko "list 실패" "$(cat "$FO/notify.log" 2>/dev/null)"
newhub D3; { row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist; echo trunc > "$FO/list.mode"
W
[ "$(envval)" = "$OLD" ] && [ "$(nsend)" = 0 ] && ok "잘린 목록(truncated) → 후보 수를 믿지 않고 갱신 안 함" || ko "잘린 목록" "$(envval)"

# ---- 심볼릭 링크: 링크는 두고 대상 교체 -------------------------------------------------------------
newhub E; { row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist
mkdir -p "$SB/E/real"; mv "$H/main.env" "$SB/E/real/main.env"; ln -s "$SB/E/real/main.env" "$H/main.env"
W
[ -L "$H/main.env" ] && [ "$(grep '^MAIN_TERMINAL=' "$SB/E/real/main.env" | cut -d= -f2)" = "$NEW" ] && grep -q '^OTHER_KEY=keep me$' "$SB/E/real/main.env" && ok "main.env 가 링크면 링크 유지·최종 대상 파일만 교체" || ko "링크" "$(ls -l "$H/main.env")"

# ---- set_main_terminal.sh --auto -------------------------------------------------------------------
newhub F; { noise; row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist
out=$(bash "$SETMAIN" --auto --dry-run 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(envval)" = "$OLD" ] && printf '%s' "$out" | grep -q 'term_000' && ! printf '%s' "$out" | grep -qF "$NEW" && ok "--auto --dry-run: 1개 발견·종료 0·main.env 불변·핸들 앞 8자만 출력" || ko "dry-run" "rc=$rc $out"
out=$(bash "$SETMAIN" --auto 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(envval)" = "$NEW" ] && grep -q '^TAIL=end$' "$H/main.env" && ! printf '%s' "$out" | grep -qF "$NEW" && ok "--auto: 1개 → 기록·종료 0" || ko "--auto" "rc=$rc $out"
newhub G; noise | setlist; cp "$H/main.env" "$SB/G.before"
out=$(bash "$SETMAIN" --auto 2>&1); rc=$?
[ "$rc" = 3 ] && printf '%s' "$out" | grep -q '후보 0개' && cmp -s "$H/main.env" "$SB/G.before" && ok "--auto: 0개 → 종료 3·후보 0개 안내·불변" || ko "--auto 0" "rc=$rc $out"
newhub G2; { row "$NEW" "MAIN-HANDOFF 하나" "$MAIN"; row "$NEW2" "MAIN-HANDOFF 둘" "$MAIN"; } | setlist
out=$(bash "$SETMAIN" --auto 2>&1); rc=$?
[ "$rc" = 3 ] && printf '%s' "$out" | grep -q '후보 2개' && [ "$(envval)" = "$OLD" ] && ok "--auto: 2개 → 종료 3·후보 2개 안내·불변" || ko "--auto 2" "rc=$rc $out"
out=$(bash "$SETMAIN" --auto --bogus 2>&1); rc=$?
[ "$rc" = 2 ] && ok "--auto 잘못된 옵션 → 종료 2" || ko "--auto 옵션 오류" "rc=$rc $out"
bash "$SETMAIN" --help | grep -q -- '--auto' && ok "--help 에 --auto 안내" || ko "--help" ""
echo fail > "$FO/list.mode"; out=$(bash "$SETMAIN" --auto 2>&1); rc=$?
[ "$rc" = 2 ] && ok "--auto: terminal list 실패 → 종료 2" || ko "--auto list 실패" "rc=$rc $out"

# ---- slack_socket.send_main: 같은 재탐색 ------------------------------------------------------------
sock() { # 이름: send_main 을 직접 호출(가짜 slack_sdk 모듈 주입). LRM_ORCA 는 PATH 의 가짜 orca
  python3 -I - "$SCRIPTS/slack_socket.py" <<'PY'
import os, sys, types, runpy
for n in ("slack_sdk", "slack_sdk.socket_mode", "slack_sdk.socket_mode.request", "slack_sdk.socket_mode.response"):
    sys.modules[n] = types.ModuleType(n)
sys.modules["slack_sdk"].WebClient = object
sys.modules["slack_sdk.socket_mode"].SocketModeClient = object
sys.modules["slack_sdk.socket_mode.request"].SocketModeRequest = object
sys.modules["slack_sdk.socket_mode.response"].SocketModeResponse = object
ns = runpy.run_path(sys.argv[1], run_name="not_main")
print("sent" if ns["send_main"]("테스트") else "failed")
PY
}
export LRM_ORCA="$FB/orca"
S() { sock | tail -1; }  # 수신기 log() 는 stdout 에 쓰므로 마지막 줄(결과)만 취함
newhub S1; { row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist; echo fail > "$FO/send.mode.$OLD"
r=$(S)
[ "$r" = sent ] && [ "$(envval)" = "$NEW" ] && [ "$(nsend)" = 2 ] && ok "slack_socket: 옛 핸들 전송 실패+gone → 재탐색 1개 → main.env 갱신 후 새 핸들로 재전송 성공" || ko "socket 후보 1" "r=$r env=$(envval) sends=$(nsend)"
newhub S2; noise | setlist; echo fail > "$FO/send.mode.$OLD"; cp "$H/main.env" "$SB/S2.before"
r=$(S)
[ "$r" = failed ] && cmp -s "$H/main.env" "$SB/S2.before" && ok "slack_socket: 후보 0개 → 실패 반환·main.env 불변" || ko "socket 후보 0" "r=$r"
newhub S3; { row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist; echo unavail > "$FO/show.mode.$OLD"; echo fail > "$FO/send.mode.$OLD"
r=$(S)
[ "$r" = failed ] && [ "$(nlist)" = 0 ] && [ "$(envval)" = "$OLD" ] && ok "slack_socket: 조회 실패(일시 오류) → 재탐색 안 함" || ko "socket 조회 실패" "r=$r list=$(nlist)"
newhub S4; { row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist; printf 'OTHER=1\n' > "$H/main.env"
r=$(S)
[ "$r" = sent ] && [ "$(envval)" = "$NEW" ] && ok "slack_socket: MAIN_TERMINAL 줄 없음 → 재탐색해 추가 후 전송" || ko "socket 핸들 없음" "r=$r env=$(envval)"

# ---- 모호한 전송 뒤 main.env 가 새 핸들로 바뀜(target_changed): 옛 대상이 죽었으면 재전달, 살아 있으면 보류+경고 ----------------
stale() { # 이름: 옛 핸들로 보낸 첫 전송이 모호(ambig)하고 그 직후 main.env 가 NEW 로 바뀜
  newhub "$1"; rm -f "$FO/show.mode.$OLD"; printf '#!/bin/bash\nprintf "MAIN_TERMINAL=%%s\\n" "%s" > "$LRM_HUB_DIR/main.env"\n' "$NEW" > "$FO/send.hook"; chmod 755 "$FO/send.hook"
  echo ambig > "$FO/send.mode.$OLD"
}
stale R1; { row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist  # 옛 핸들은 목록에 없음 = 죽음
LRM_PUSH_LIFE=8 LRM_PUSH_RETRY=0.5 W
[ "$(nsend)" = 2 ] && [ "$(awk -F'\t' 'NR==2{print $3}' "$FO/sends.log")" = "$NEW" ] && [ "$(awk -F'\t' 'NR==1{print $4}' "$FO/sends.log")" != "$(awk -F'\t' 'NR==2{print $4}' "$FO/sends.log")" ] \
  && grep -q 'push redeliver why=target_dead_redeliver' "$H/slack-push.log" && [ "$(jq -r .push_pending "$H/slack-push-state.json")" = null ] && ok "옛 대상 죽음 → 같은 묶음을 새 요청 ID 로 새 대상에 재전달(로그 target_dead_redeliver)" || ko "죽음→재전달" "sends=$(nsend) $(cat "$FO/sends.log") $(cat "$H/slack-push.log")"
stale R2; { row "$OLD" "MAIN-HANDOFF 옛 메인(살아 있음)" "$MAIN"; row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist
LRM_PUSH_LIFE=8 LRM_PUSH_RETRY=0.5 W
[ "$(nsend)" = 1 ] && [ "$(jq -r .push_pending.reason "$H/slack-push-state.json")" = unknown_delivery ] && grep -q 'why=target_changed' "$H/slack-push.log" && ! grep -q redeliver "$H/slack-push.log" \
  && grep -q '옛 메인 터미널' "$FO/notify.log" && [ "$(wc -l < "$FO/notify.log" | tr -d ' ')" = 1 ] && ! grep -q 'term_' "$FO/notify.log" && ok "옛 대상 살아 있음 → 보류 유지·재전달 없음·사람에게 경고 1회(핸들 값 없음)" || ko "살아 있음→보류" "sends=$(nsend) $(cat "$FO/notify.log" 2>/dev/null)"
stale R3; echo fail > "$FO/list.mode"
LRM_PUSH_LIFE=8 LRM_PUSH_RETRY=0.5 W
[ "$(nsend)" = 1 ] && grep -q 'why=target_changed' "$H/slack-push.log" && ! grep -q redeliver "$H/slack-push.log" && ok "옛 대상 생사를 못 알면(list 조회 실패) 보류 유지" || ko "조회 실패→보류" "sends=$(nsend)"
stale R4; { row "$OLD" "MAIN-HANDOFF 끊긴 옛 메인" "$MAIN" | jq -c '.connected=false'; row "$NEW" "MAIN-HANDOFF 메인" "$MAIN"; } | setlist
LRM_PUSH_LIFE=8 LRM_PUSH_RETRY=0.5 W
[ "$(nsend)" = 2 ] && grep -q 'why=target_dead_redeliver' "$H/slack-push.log" && ok "옛 대상이 목록에 있어도 끊김(connected=false)이면 죽음으로 보고 재전달" || ko "끊김→재전달" "sends=$(nsend)"
rm -f "$FO/send.hook"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
