#!/usr/bin/env bash
# 기능 열기·닫기의 Slack 스레드 예약 시험. 임시 저장소·임시 작업판·가짜 orca·가짜 create_hub·가짜 hub_report 만 씀(실제 Slack·Orca·작업판 무관).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SCR="$HERE/../../hubs/scripts"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-life.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
BIN="$SB/bin" WSR="$SB/ws" BOARD="$SB/board" REPO="$SB/repo" ROLES="$SB/roles"
mkdir -p "$BIN" "$WSR" "$BOARD" "$ROLES"
git init -q -b dev "$REPO"; git -C "$REPO" commit -q --allow-empty -m seed
(cd "$BOARD" && git init -q && bd init --prefix t -q >/dev/null 2>&1) || { echo "임시 작업판 준비 실패"; exit 1; }

# 가짜 orca: 작업 공간·워커·터미널 모두 비어 있음
cat > "$BIN/orca" <<'EOS'
#!/bin/bash
case "$1 $2" in
  "worktree list") echo '{"ok":true,"result":{"worktrees":[]}}' ;;
  "orchestration worker-list") echo '{"ok":true,"result":{"workers":[]}}' ;;
  "terminal list") echo '{"ok":true,"result":{"terminals":[]}}' ;;
  *) echo '{"ok":true,"result":{}}' ;;
esac
EOS
printf '#!/bin/bash\necho "허브 열림"\n' > "$BIN/create_hub.sh"
printf '#!/bin/bash\nexit 0\n' > "$BIN/ws_gc.sh"
# 가짜 hub_report: 호출 인자를 한 줄로 기록. FAKE_REPORT_FAIL=1 이면 실패
printf '#!/usr/bin/env python3\nimport os, sys\nif os.environ.get("FAKE_REPORT_FAIL") == "1": sys.exit(1)\nopen(os.environ["CALLS"], "a").write(" ".join(sys.argv[1:]) + "\\n")\n' > "$BIN/hub_report.py"
chmod +x "$BIN/orca" "$BIN/create_hub.sh" "$BIN/ws_gc.sh" "$BIN/hub_report.py"
export PATH="$BIN:$PATH" CALLS="$SB/calls" REPO WS_ROOT="$WSR" LRM_BOARD="$BOARD" BOARD_DIR="$BOARD" LRM_CREATE_HUB="$BIN/create_hub.sh" LRM_ROLES_DIR="$ROLES" LRM_HUB_REPORT="$BIN/hub_report.py" LRM_WS_GC="$BIN/ws_gc.sh"
: > "$CALLS"
epic_of() { (cd "$BOARD" && bd list -t epic -l "feature:$1" --all --json | jq -r '.[0].id // empty'); }

# ---- feature_open: 기능 스레드 시작 예약 ----
bash "$SCR/feature_open.sh" --ticket LRM-701 --name "우편함" --base dev --slug mail > "$SB/out" 2>"$SB/err"; RC=$?
EPIC=$(epic_of mail)
chk "feature_open: epic 주제로 시작 한 줄 예약(허브 feat-mail)" '[ "$RC" = 0 ] && [ "$(cat "$CALLS")" = "add --hub feat-mail --task 없음 --kind info --card $EPIC --text 기능 시작: 우편함 (LRM-701, 통합 브랜치 feature/mail)" ]' "rc=$RC $(cat "$CALLS" "$SB/err")"
: > "$CALLS"; bash "$SCR/feature_open.sh" --ticket LRM-701 --name "우편함" --base dev --slug mail >/dev/null 2>&1
chk "feature_open 재실행: 같은 내용을 다시 예약(중복은 hub_report 가 무시)" '[ "$(wc -l < "$CALLS" | tr -d " ")" = 1 ]'
: > "$CALLS"; bash "$SCR/feature_open.sh" --ticket LRM-702 --name "상점" --base dev --slug shop --dry-run >/dev/null 2>&1
chk "feature_open --dry-run: 예약 없음" '[ ! -s "$CALLS" ]'
: > "$CALLS"; FAKE_REPORT_FAIL=1 bash "$SCR/feature_open.sh" --ticket LRM-703 --name "랭킹" --base dev --slug rank >/dev/null 2>"$SB/err"; RC=$?
chk "feature_open: 예약 실패는 경고만(종료 0·epic 생성 유지)" '[ "$RC" = 0 ] && grep -q "스레드 시작 예약 실패" "$SB/err" && [ -n "$(epic_of rank)" ]' "rc=$RC $(cat "$SB/err")"

# ---- feature_close: 기능 스레드 마무리 예약 ----
mkdir -p "$WSR/coord-feat-mail-hub"
printf '<p>결과</p>' > "$SB/result.html"
(cd "$BOARD" && bd comments add "$EPIC" "기능 완료. 결과: $SB/result.html" >/dev/null)
: > "$CALLS"; bash "$SCR/feature_close.sh" --slug mail --yes --dry-run >"$SB/out" 2>"$SB/err"; RC=$?
chk "feature_close --yes --dry-run: 예약은 계획 단계로만 표시" '[ "$RC" = 0 ] && grep -q "기능 스레드 마무리 보고 예약" "$SB/out" && [ ! -s "$CALLS" ]' "rc=$RC $(cat "$SB/out" "$SB/err")"
: > "$CALLS"; bash "$SCR/feature_close.sh" --slug mail --yes >"$SB/out" 2>"$SB/err"; RC=$?
chk "feature_close --yes: 결과 문서(epic 코멘트의 html 경로)를 붙여 마무리 한 줄 예약" '[ "$RC" = 0 ] && [ "$(cat "$CALLS")" = "add --hub feat-mail --task 없음 --kind done --card $EPIC --text 기능 마무리: 남은 카드 0개·미머지 브랜치 0개. 통합 브랜치 feature/mail 유지 --file $SB/result.html" ]' "rc=$RC $(cat "$CALLS" "$SB/err")"
chk "feature_close: 마무리 예약 뒤 epic 닫음" '[ "$(cd "$BOARD" && bd list --all --id "$EPIC" --limit 0 --json | jq -r ".[0].status")" = closed ]'
chk "feature_close 재실행: 닫힌 epic 이라 다시 예약하지 않음" ': > "$CALLS"; bash "$SCR/feature_close.sh" --slug mail --yes >/dev/null 2>&1; [ ! -s "$CALLS" ]'

mkdir -p "$WSR/coord-feat-rank-hub"; RANK=$(epic_of rank)
: > "$CALLS"; bash "$SCR/feature_close.sh" --slug rank --yes --result "$SB/none.html" >"$SB/out" 2>"$SB/err"; RC=$?
chk "feature_close: 없는 결과 문서는 경고 뒤 문서 없이 한 줄만" '[ "$RC" = 0 ] && grep -q "결과 문서 없음" "$SB/err" && [ "$(cat "$CALLS")" = "add --hub feat-rank --task 없음 --kind done --card $RANK --text 기능 마무리: 남은 카드 0개·미머지 브랜치 0개. 통합 브랜치 feature/rank 유지" ]' "rc=$RC $(cat "$CALLS" "$SB/err")"

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
