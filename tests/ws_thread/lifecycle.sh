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
export LRM_SPRINT_DATE=20261118   # 브랜치의 스프린트 폴더 날짜 = 스프린트 시작일. 시험은 고정
: > "$CALLS"
epic_of() { (cd "$BOARD" && bd list -t epic -l "feature:$1" --all --json | jq -r '.[0].id // empty'); }

# ---- feature_open: 기능 스레드 시작 예약 ----
bash "$SCR/feature_open.sh" --ticket LRM-701 --name "우편함" --base dev --slug mail > "$SB/out" 2>"$SB/err"; RC=$?
EPIC=$(epic_of mail)
chk "feature_open: epic 주제로 시작 한 줄 예약(허브 feat-mail)" '[ "$RC" = 0 ] && [ "$(cat "$CALLS")" = "add --hub feat-mail --task 없음 --kind info --card $EPIC --text 기능 시작: 우편함 (LRM-701, 통합 브랜치 sprint-20261118/feat-LRM-701-mail)" ]' "rc=$RC $(cat "$CALLS" "$SB/err")"
: > "$CALLS"; bash "$SCR/feature_open.sh" --ticket LRM-701 --name "우편함" --base dev --slug mail >/dev/null 2>&1
chk "feature_open 재실행: 같은 내용을 다시 예약(중복은 hub_report 가 무시)" '[ "$(wc -l < "$CALLS" | tr -d " ")" = 1 ]'
# 브랜치 이름은 게임 저장소 규칙(CLAUDE.md): 스프린트 안에서 끝나면 sprint-YYYYMMDD/타입-티켓-작업명, 여러 스프린트에 걸치는 모듈만 feature/<이름>
: > "$CALLS"; LRM_SPRINT_DATE=20261125 bash "$SCR/feature_open.sh" --ticket LRM-701 --name "우편함" --base dev --slug mail >/dev/null 2>&1
chk "스프린트가 바뀐 뒤 다시 실행해도 처음 만든 브랜치를 그대로 씀" 'grep -q "통합 브랜치 sprint-20261118/feat-LRM-701-mail)" "$CALLS"' "$(cat "$CALLS")"
chk "새 기능 카드에 새 흐름 표시(flow:v1)·첫 단계(접수)·브랜치가 적힘" '(cd "$BOARD" && bd show "$EPIC" --json | jq -e "(if type==\"array\" then .[0] else . end).labels | (index(\"flow:v1\") != null) and (index(\"said:v1\") != null) and (index(\"stage:intake\") != null) and (index(\"branch:sprint-20261118/feat-LRM-701-mail\") != null)") >/dev/null'
bjson() { bash "$SCR/feature_open.sh" "$@" --base dev --dry-run --json 2>"$SB/err" | jq -r .branch; }
chk "버그 작업은 타입 fix" '[ "$(bjson --ticket LRM-711 --name 버그 --slug gauge-overflow --type fix)" = "sprint-20261118/fix-LRM-711-gauge-overflow" ]'
chk "여러 스프린트에 걸치는 모듈(--long)은 feature/<이름>" '[ "$(bjson --ticket LRM-712 --name 현지화 --slug localization --long)" = "feature/localization" ]'
chk "작업명을 안 주면 티켓 번호까지만 쓰고 경고" '[ "$(bjson --ticket LRM-713 --name 이름없음)" = "sprint-20261118/feat-LRM-713" ] && grep -q "작업명" "$SB/err"' "$(cat "$SB/err")"
bash "$SCR/feature_open.sh" --ticket LRM-714 --name x --base dev --slug y --type hotfix --dry-run >/dev/null 2>"$SB/err"; RC=$?
chk "모르는 타입은 종료 2" '[ "$RC" = 2 ] && grep -q "feat|fix" "$SB/err"' "rc=$RC $(cat "$SB/err")"
# 스프린트 폴더의 날짜는 스프린트 시작일(사용자 11-18). 오늘 날짜가 아니므로 계산하지 않고 정해 둔 값을 읽음: --sprint > LRM_SPRINT_DATE > 설정 파일
nosprint() { env -u LRM_SPRINT_DATE LRM_SPRINT_FILE="$SB/sprint-start" bash "$SCR/feature_open.sh" "$@" --base dev --dry-run --json 2>"$SB/err"; }
rm -f "$SB/sprint-start"; nosprint --ticket LRM-715 --name x --slug nodate >/dev/null; RC=$?
chk "스프린트 시작일을 모르면 만들지 않고 멈춤(오늘 날짜로 지어내지 않음)" '[ "$RC" = 2 ] && grep -q "스프린트 시작일" "$SB/err" && ! grep -q "$(date +%Y%m%d)" "$SB/err"' "rc=$RC $(cat "$SB/err")"
echo 20261107 > "$SB/sprint-start"
chk "설정 파일의 스프린트 시작일을 씀" '[ "$(nosprint --ticket LRM-715 --name x --slug nodate | jq -r .branch)" = "sprint-20261107/feat-LRM-715-nodate" ]' "$(cat "$SB/err")"
chk "--sprint 로 준 날짜가 우선" '[ "$(nosprint --ticket LRM-715 --name x --slug nodate --sprint 20261125 | jq -r .branch)" = "sprint-20261125/feat-LRM-715-nodate" ]' "$(cat "$SB/err")"
nosprint --ticket LRM-715 --name x --slug nodate --sprint 2026-11-25 >/dev/null; RC=$?
chk "날짜 형식이 틀리면 종료 2" '[ "$RC" = 2 ] && grep -q "YYYYMMDD" "$SB/err"' "rc=$RC $(cat "$SB/err")"
rm -f "$SB/sprint-start"
chk "여러 스프린트에 걸치는 모듈(--long)은 스프린트 시작일이 없어도 됨" '[ "$(nosprint --ticket LRM-716 --name x --slug longmod --long | jq -r .branch)" = "feature/longmod" ]' "$(cat "$SB/err")"
# 접수(0단계): 목표와 확인 방법을 기능 카드 설명에 적어 줌. 안 주면 빈 칸으로 남아 단계 도구가 채우라고 알려 줌
desc_of() { (cd "$BOARD" && bd show "$1" --json | jq -r '(if type=="array" then .[0] else . end).description'); }
bash "$SCR/feature_open.sh" --ticket LRM-721 --name "결말" --base dev --slug ending --goal "마지막 날 시작해도 결말이 열린다" --verify "마지막 날 세이브로 시작해 장면이 열리는 캡처" >/dev/null 2>&1
D=$(desc_of "$(epic_of ending)")
chk "feature_open --goal·--verify: 기능 카드 설명에 접수 칸으로 적힘(원문 그대로)" 'printf "%s" "$D" | grep -qx -- "- 목표: 마지막 날 시작해도 결말이 열린다" && printf "%s" "$D" | grep -qx -- "- 확인 방법: 마지막 날 세이브로 시작해 장면이 열리는 캡처"' "$D"
chk "접수 칸을 안 준 기능 카드에는 빈 칸이 남음" 'desc_of "$EPIC" | grep -q -- "^- 목표:[[:space:]]*$" && desc_of "$EPIC" | grep -q -- "^- 확인 방법:[[:space:]]*$"' "$(desc_of "$EPIC")"
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
chk "feature_close --yes: 결과 문서(epic 코멘트의 html 경로)를 붙여 마무리 한 줄 예약" '[ "$RC" = 0 ] && [ "$(cat "$CALLS")" = "add --hub feat-mail --task 없음 --kind done --card $EPIC --text 기능 마무리: 남은 카드 0개·미머지 브랜치 0개. 통합 브랜치 sprint-20261118/feat-LRM-701-mail 유지 --file $SB/result.html" ]' "rc=$RC $(cat "$CALLS" "$SB/err")"
chk "feature_close: 마무리 예약 뒤 epic 닫음" '[ "$(cd "$BOARD" && bd list --all --id "$EPIC" --limit 0 --json | jq -r ".[0].status")" = closed ]'
chk "feature_close 재실행: 닫힌 epic 이라 다시 예약하지 않음" ': > "$CALLS"; bash "$SCR/feature_close.sh" --slug mail --yes >/dev/null 2>&1; [ ! -s "$CALLS" ]'

mkdir -p "$WSR/coord-feat-rank-hub"; RANK=$(epic_of rank)
: > "$CALLS"; bash "$SCR/feature_close.sh" --slug rank --yes --result "$SB/none.html" >"$SB/out" 2>"$SB/err"; RC=$?
chk "feature_close: 없는 결과 문서는 경고 뒤 문서 없이 한 줄만" '[ "$RC" = 0 ] && grep -q "결과 문서 없음" "$SB/err" && [ "$(cat "$CALLS")" = "add --hub feat-rank --task 없음 --kind done --card $RANK --text 기능 마무리: 남은 카드 0개·미머지 브랜치 0개. 통합 브랜치 sprint-20261118/feat-LRM-703-rank 유지" ]' "rc=$RC $(cat "$CALLS" "$SB/err")"

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
