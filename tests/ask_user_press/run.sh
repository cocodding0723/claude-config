#!/bin/bash
# ask_user_press.sh·patrol.sh 워커 소멸 시험. 가짜 orca 로 화면·터미널 목록을 흉내 냄
set -u
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
ASK="$ROOT/hubs/scripts/ask_user_press.sh"; PATROL="$ROOT/skills/orca-main/scripts/patrol.sh"
SB=$(mktemp -d "${TMPDIR:-/tmp}/ask-press.XXXXXX"); trap 'rm -rf "$SB"' EXIT
PASS=0 FAIL=0
ok() { PASS=$((PASS+1)); echo "PASS  $1"; }
ko() { FAIL=$((FAIL+1)); echo "FAIL  $1 — ${2:-}"; }
cat > "$SB/orca" <<'O'
#!/bin/sh
echo "$*" >> "$FAKE_DIR/calls"
case "$1 $2" in
  "terminal read") cat "$FAKE_DIR/screen" ;;
  "terminal list") cat "$FAKE_DIR/list" ;;
  "terminal send") exit "${FAKE_SEND_RC:-0}" ;;
esac
O
chmod +x "$SB/orca"; export FAKE_DIR="$SB" LRM_ORCA="$SB/orca" LRM_MAIN_ENV="$SB/main.env"
printf 'MAIN_TERMINAL=term-main\nHUB_ROOT=%s/ws\n' "$SB" > "$SB/main.env"
screen() { python3 -c 'import json,sys;print(json.dumps({"ok":True,"result":{"terminal":{"tail":sys.argv[1:]}}}))' "$@" > "$SB/screen"; }

screen "작업 중" "Do you want to proceed?" "❯ 1. Yes" "  2. No"
out=$("$ASK" --terminal term_w1 --expect 'Do you want' "2 No 눌러 주세요" 2>&1); rc=$?
[ $rc = 0 ] && grep -q -- '--terminal term-main' "$SB/calls" && grep -q 'Do you want to proceed' "$SB/calls" && ok "창 문구 있음 → 화면 인용과 함께 메인 전송" || ko "전송" "rc=$rc $out"
: > "$SB/calls"; screen "❯" "커밋 완료 a1b2c3"
out=$("$ASK" --terminal term_w1 --expect 'Do you want' "2 No 눌러 주세요" 2>&1); rc=$?
[ $rc = 3 ] && ! grep -q 'terminal send' "$SB/calls" && grep -q '확인 창 안 보임' <<<"$out" && ok "창 문구 없음 → 종료 3·전송 안 함" || ko "거부" "rc=$rc $out"
echo '{"ok":false,"error":{"code":"terminal_handle_stale"}}' > "$SB/screen"
"$ASK" --terminal term_x --expect 'Do' "요청" >/dev/null 2>&1; rc=$?
[ $rc = 2 ] && ok "터미널 없음 → 종료 2" || ko "터미널 없음" "rc=$rc"
"$ASK" --terminal term_x "요청" >/dev/null 2>&1; [ $? = 2 ] && ok "--expect 없으면 종료 2" || ko "인자 검사" ""
screen "Do you want x"; FAKE_SEND_RC=1 "$ASK" --terminal t --expect Do "r" >/dev/null 2>&1; [ $? = 1 ] && ok "메인 전송 실패 → 종료 1" || ko "전송 실패" ""

# patrol: 워커 사라짐 감지(1회만)
mkdir -p "$SB/ws/feat-a"; git -C "$SB/ws/feat-a" init -q -b feat/a && git -C "$SB/ws/feat-a" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "워커 결과 커밋"
lst() { python3 -c 'import json,sys;print(json.dumps({"result":{"terminals":[json.loads(x) for x in sys.argv[1:]]}}))' "$@" > "$SB/list"; }
W="{\"handle\":\"term_worker_a1\",\"worktreePath\":\"$SB/ws/feat-a\",\"branch\":\"refs/heads/feat/a\",\"agentIdentity\":\"codex\"}"
H="{\"handle\":\"term_hub_1\",\"worktreePath\":\"$SB/ws/coord-ui\",\"agentIdentity\":\"claude\",\"lastOutputAt\":0}"
export PATH="$SB:$PATH" ORCA_MAIN_CONFIG="$SB/main.env" PATROL_STATE="$SB/pstate.json"
lst "$W" "$H"; o1=$(bash "$PATROL" 2>&1)
lst "$H"; o2=$(bash "$PATROL" 2>&1); o3=$(bash "$PATROL" 2>&1)
grep -q '사라진 워커 0개' <<<"$o1" && grep -q '🫥 워커 사라짐 feat-a term_worker_a .*feat/a .*워커 결과 커밋' <<<"$o2" && ! grep -q '🫥' <<<"$o3" && ok "사라진 워커를 마지막 커밋과 함께 1회 표시" || ko "워커 소멸" "$o2 | $o3"
lst "$H"; j=$(bash "$PATROL" --json); python3 -c 'import json,sys;d=json.loads(sys.argv[1]);assert "hubs" in d and d["gone"]==[]' "$j" && ok "--json 은 hubs·gone" || ko "json" "$j"
printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"; [ "$FAIL" = 0 ]
