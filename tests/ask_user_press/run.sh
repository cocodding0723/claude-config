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
chmod +x "$SB/orca"; export FAKE_DIR="$SB" LRM_ORCA="$SB/orca" LRM_MAIN_ENV="$SB/main.env" LRM_HUB_DIR="$SB/hub"; INB="$SB/hub/hub-inbox/main.jsonl"
printf 'MAIN_TERMINAL=term-main\nHUB_ROOT=%s/ws\n' "$SB" > "$SB/main.env"
screen() { python3 -c 'import json,sys;print(json.dumps({"ok":True,"result":{"terminal":{"tail":sys.argv[1:]}}}))' "$@" > "$SB/screen"; }

screen "작업 중" "Do you want to proceed?" "❯ 1. Yes" "  2. No"
out=$("$ASK" --terminal term_w1 --expect 'Do you want' "2 No 눌러 주세요" 2>&1); rc=$?
[ $rc = 0 ] && ! grep -q 'terminal send' "$SB/calls" && grep -q 'Do you want to proceed' "$INB" && grep -q '"source":"ask_user_press"' "$INB" && ok "창 문구 있음 → 화면 인용과 함께 메인 받은 편지함에 기록(터미널 send 없음)" || ko "전송" "rc=$rc $out"
: > "$SB/calls"; screen "❯" "커밋 완료 a1b2c3"
out=$("$ASK" --terminal term_w1 --expect 'Do you want' "2 No 눌러 주세요" 2>&1); rc=$?
[ $rc = 3 ] && ! grep -q 'terminal send' "$SB/calls" && grep -q '확인 창 안 보임' <<<"$out" && ok "창 문구 없음 → 종료 3·전송 안 함" || ko "거부" "rc=$rc $out"
echo '{"ok":false,"error":{"code":"terminal_handle_stale"}}' > "$SB/screen"
"$ASK" --terminal term_x --expect 'Do' "요청" >/dev/null 2>&1; rc=$?
[ $rc = 2 ] && ok "터미널 없음 → 종료 2" || ko "터미널 없음" "rc=$rc"
"$ASK" --terminal term_x "요청" >/dev/null 2>&1; [ $? = 2 ] && ok "--expect 없으면 종료 2" || ko "인자 검사" ""
screen "Do you want x"; mv "$SB/hub" "$SB/hub.bak"; : > "$SB/hub"; "$ASK" --terminal t --expect Do "r" >/dev/null 2>&1; rc=$?; rm -f "$SB/hub"; mv "$SB/hub.bak" "$SB/hub"
[ $rc = 1 ] && ok "편지함 쓰기 실패 → 종료 1" || ko "전송 실패" "rc=$rc"
out=$("$ASK" --terminal t --expect Do --dry-run "r" 2>&1); n=$(wc -l < "$INB" | tr -d " ")
"$ASK" --terminal t --expect Do --dry-run "r" >/dev/null 2>&1; [ "$(wc -l < "$INB" | tr -d " ")" = "$n" ] && grep -q '\[허브→메인\] 창 확인 요청' <<<"$out" && ok "--dry-run → 편지함 불변·보낼 글 출력" || ko "dry-run" "$out"

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

# patrol --inbox: 받은 편지함 새 줄만 1회 읽고 읽은 위치 기록. orca 조회 없음. 임시 작업 폴더만 씀
HD="$SB/inbox-hub"; mkdir -p "$HD"; cp "$ROOT/hubs/scripts/main_inbox.py" "$HD/"; : > "$SB/calls"
printf 'HUB_ROOT=%s/ws\nHUB_SCRIPTS=%s\n' "$SB" "$HD" > "$SB/main-inbox.env"
PI() { ORCA_MAIN_CONFIG="$SB/main-inbox.env" LRM_HUB_DIR="$HD" bash "$PATROL" --inbox "$@"; }
n0=$(PI 2>&1 | tail -1)
LRM_HUB_DIR="$HD" python3 -I "$HD/main_inbox.py" append --kind ci --source ci_watch "CI 실패 PR#9" >/dev/null
LRM_HUB_DIR="$HD" python3 -I "$HD/main_inbox.py" append --kind stall --source card_actions "응답 없는 워커" >/dev/null
p1=$(PI --peek 2>&1); p2=$(PI 2>&1); p3=$(PI 2>&1)
grep -q '새 줄 0건' <<<"$n0" && grep -q 'CI 실패 PR#9' <<<"$p1" && grep -q '응답 없는 워커' <<<"$p2" && grep -q '새 줄 0건' <<<"$p3" && [ -s "$HD/main-inbox.offset" ] && ! grep -q '^terminal' "$SB/calls" \
  && ok "patrol --inbox: 새 줄 표 출력(--peek 은 위치 불변)·읽은 뒤 다시 읽으면 0건·orca 호출 없음" || ko "patrol --inbox" "$n0 | $p1 | $p2 | $p3"
LRM_HUB_DIR="$HD" python3 -I "$HD/main_inbox.py" append --kind review --source adv_review "지적 1건" >/dev/null
j=$(PI --json 2>&1); python3 -c 'import json,sys;r=json.loads(sys.argv[1].splitlines()[0]);assert r["kind"]=="review" and r["source"]=="adv_review"' "$j" && ok "patrol --inbox --json: 줄마다 JSON" || ko "inbox json" "$j"
printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"; [ "$FAIL" = 0 ]
