#!/usr/bin/env bash
# restart_hub.sh·hub_dups.py 시험. 가짜 orca(PATH 앞)·임시 폴더만 씀. 실제 터미널·보관 폴더(~/.config/lrm-hubs)는 건드리지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
RH="$REPO/hubs/scripts/restart_hub.sh"
HD="$REPO/hubs/scripts/hub_dups.py"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-restarthub.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
BIN="$SB/bin"; mkdir -p "$BIN" "$SB/hubroot/coord-x-hub"
echo "인계" > "$SB/hubroot/coord-x-hub/HANDOFF.md"
HUB="$SB/hubroot/coord-x-hub"
export HUB_ROOT="$SB/hubroot" ROLE_FILE="$SB/role.txt" COMMON_FILE="" LRM_HUB_INBOX="$SB/inbox" FAKE_DIR="$SB" LRM_ORCA="$BIN/orca" READY_TIMEOUT=2
export HUB_DUPS_PY="$HD"

# 가짜 orca: 호출을 orca.log 에 기록. 응답은 $SB/terms.json(list), workers.json(worker-list), launch 는 NEW 핸들 반환
# NEW_READY=0 이면 새 세션이 lastOutputAt 없이 목록에 나옴, LAUNCH_FAIL=1 이면 기동 실패, WORKERS_FAIL=1 이면 worker-list 실패
cat > "$BIN/orca" <<'FAKE'
#!/bin/bash
echo "$*" >> "$FAKE_DIR/orca.log"
case "$1 $2" in
  "agent launch") [ "${LAUNCH_FAIL:-}" = 1 ] && exit 1; echo '{"ok":true,"result":{"terminal":{"handle":"term_new"}}}' ;;
  "terminal list") cat "$FAKE_DIR/terms.json" ;;
  "orchestration worker-list") [ "${WORKERS_FAIL:-}" = 1 ] && exit 1; cat "$FAKE_DIR/workers.json" ;;
  "terminal read") echo '{"ok":true,"result":{"terminal":{"handle":"x","tail":["마지막 줄 1","마지막 줄 2"]}}}' ;;
  "terminal close") [ "${CLOSE_FAIL:-}" = "$4" ] && exit 1; echo '{"ok":true,"result":{}}' ;;
esac
FAKE
chmod +x "$BIN/orca"
export PATH="$BIN:$PATH"
NOW_MS=$(python3 -c "import time;print(int(time.time()*1000))")
# tm <handle> <경로> <agent> <connected> <writable> <orphaned> <출력 경과 분|null>
tm() { printf '{"handle":"%s","worktreePath":"%s","agentIdentity":"%s","connected":%s,"writable":%s,"orphaned":%s,"lastOutputAt":%s,"title":"제목 %s"}' "$1" "$2" "$3" "$4" "$5" "$6" "$( [ "$7" = null ] && echo null || echo $((NOW_MS - $7 * 60000)) )" "$1"; }
# 기본 상황: 같은 허브 폴더에 옛 코디네이터 2(old1·old2), 워커 1(term_wk), 다른 허브 코디네이터 1, codex 1, 새 세션 1
setup() {
  : > "$SB/orca.log"; rm -rf "$SB/inbox"
  printf '{"result":{"terminals":[%s,%s,%s,%s,%s,%s]}}' \
    "$(tm term_old1 "$HUB" claude true true false 30)" "$(tm term_old2 "$HUB" claude false true true 50)" \
    "$(tm term_wk "$HUB" claude true true false 1)" "$(tm term_other "$SB/hubroot/coord-y-hub" claude true true false 1)" \
    "$(tm term_cx "$HUB" codex true true false 1)" "$(tm term_new "$HUB" claude true true false ${NEW_AGE:-0})" > "$SB/terms.json"
  printf '{"result":{"workers":[{"dispatchId":"d1","terminal":"term_wk","agentTerminalHandle":"term_wk","liveness":"live","terminalState":"active"}]}}' > "$SB/workers.json"
}
closed() { grep '^terminal close' "$SB/orca.log" | sed 's/.*--terminal \(term_[a-z0-9]*\).*/\1/' | sort | tr '\n' ' '; }

# 1) 정상: 새 세션 준비 뒤 옛 코디네이터만 닫음, 워커·다른 허브·codex·새 세션은 유지
setup
OUT=$(bash "$RH" term_old1 coord-x-hub x 2>"$SB/err"); RC=$?
chk "정상: 종료 코드 0, stdout 은 새 핸들 한 줄" '[ "$RC" = 0 ] && [ "$OUT" = term_new ]' "rc=$RC out=$OUT"
chk "옛 코디네이터(old1·old2)만 닫음" '[ "$(closed)" = "term_old1 term_old2 " ]' "$(closed)"
chk "워커·다른 허브·codex·새 세션은 닫지 않음" '! closed | grep -qE "term_wk|term_other|term_cx|term_new"'
chk "새 세션 준비 확인(list) 뒤에 닫음(순서)" '[ "$(grep -n "^terminal list" "$SB/orca.log" | head -1 | cut -d: -f1)" -lt "$(grep -n "^terminal close" "$SB/orca.log" | head -1 | cut -d: -f1)" ]'
chk "닫기 전에 마지막 화면 보관(허브별 폴더)" '[ "$(ls "$SB/inbox/x" | wc -l | tr -d " ")" = 2 ] && grep -q "마지막 줄 2" "$SB/inbox/x/"*term_old1.txt'
chk "강제 종료 없음(kill 미사용, close 만)" '! grep -qi "kill" "$SB/orca.log"'

# 2) 새 세션이 준비되지 않으면 옛 세션 유지(닫기 호출 0)
setup; NEW_AGE=null; setup
OUT=$(bash "$RH" term_old1 coord-x-hub x 2>"$SB/err"); RC=$?
chk "준비 실패: 종료 코드 1·옛 세션 닫기 0·진단" '[ "$RC" = 1 ] && [ -z "$(closed)" ] && grep -q "옛 세션 유지" "$SB/err"' "rc=$RC closed=$(closed)"
unset NEW_AGE

# 3) 기동 실패: 옛 세션 유지(예전에는 기동 전에 옛 터미널부터 닫았음)
setup
LAUNCH_FAIL=1 bash "$RH" term_old1 coord-x-hub x >/dev/null 2>&1; RC=$?
chk "기동 실패: 종료 코드 비정상·닫기 0" '[ "$RC" != 0 ] && [ -z "$(closed)" ]' "rc=$RC"

# 4) worker-list 조회 실패: 워커를 가려낼 수 없으니 아무것도 닫지 않음
setup
WORKERS_FAIL=1 bash "$RH" term_old1 coord-x-hub x >/dev/null 2>"$SB/err"; RC=$?
chk "worker-list 실패: 닫기 0·종료 코드 1" '[ "$RC" = 1 ] && [ -z "$(closed)" ] && grep -q "worker-list" "$SB/err"' "rc=$RC"

# 5) 보관 실패(read 실패) 또는 닫기 실패: 해당 터미널만 건너뛰고 종료 코드 1
setup
CLOSE_FAIL=term_old1 bash "$RH" term_old1 coord-x-hub x >/dev/null 2>"$SB/err"; RC=$?
chk "일부 닫기 실패: 종료 코드 1·진단·나머지는 처리" '[ "$RC" = 1 ] && grep -q "닫기 실패" "$SB/err" && closed | grep -q term_old2'

# 6) 워커가 --also 로 들어와도 안 닫음
setup
python3 "$HD" clean --path "$HUB" --keep term_new --also term_wk >/dev/null 2>&1
chk "--also 에 워커를 줘도 닫지 않음" '! closed | grep -q term_wk'

# 7) clean: 유지 세션이 살아 있지 않으면 거부
setup
python3 "$HD" clean --path "$HUB" --keep term_old2 >/dev/null 2>"$SB/err"; RC=$?
chk "유지 대상이 죽은(고아) 세션이면 거부·닫기 0" '[ "$RC" = 2 ] && [ -z "$(closed)" ]' "rc=$RC"
python3 "$HD" clean --path "$HUB" --keep term_nope >/dev/null 2>&1; RC=$?
chk "유지 핸들이 목록에 없으면 거부" '[ "$RC" = 2 ] && [ -z "$(closed)" ]'

# 8) --dry-run: 닫지 않음
setup
OUT=$(python3 "$HD" clean --path "$HUB" --keep term_new --dry-run 2>&1); RC=$?
chk "clean --dry-run: 대상 출력·닫기 0·보관 0" '[ "$RC" = 0 ] && [ -z "$(closed)" ] && printf "%s" "$OUT" | grep -c "dry-run" | grep -q 2 && [ ! -d "$SB/inbox" ]'
OUT=$(bash "$RH" --dry-run term_old1 coord-x-hub x 2>&1); RC=$?
chk "restart_hub.sh --dry-run: 기동·닫기 호출 0" '[ "$RC" = 0 ] && ! grep -qE "^(agent launch|terminal close)" "$SB/orca.log"'

# 9) list: 중복 허브만, 워커 제외
OUT=$(python3 "$HD" list 2>&1)
chk "list: x 허브 코디네이터 3개(old1·old2·new), 워커·codex 제외" 'printf "%s" "$OUT" | grep -c "^x	" | grep -q 3 && ! printf "%s" "$OUT" | grep -qE "term_wk|term_cx|^y	"'
chk "list --json 은 JSON 배열" 'python3 "$HD" list --json | python3 -c "import json,sys;d=json.load(sys.stdin);assert len(d)==3 and sum(r[\"keep\"] for r in d)==1"'

# 10) CLI 규약
chk "--help(restart_hub·hub_dups)" 'bash "$RH" --help | grep -q "종료 코드" && python3 "$HD" --help | grep -q clean'
chk "인자 부족: 종료 코드 2" 'bash "$RH" >/dev/null 2>&1; [ $? = 2 ]'
chk "HANDOFF.md 없음: 종료 코드 2" 'bash "$RH" term_old1 coord-none-hub x >/dev/null 2>&1; [ $? = 2 ]'

printf '\n%s\n' "결과: PASS $PASS / FAIL $FAIL"
[ "$FAIL" = 0 ]
