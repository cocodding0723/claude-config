#!/usr/bin/env bash
# panel_collect.sh 시험. 가짜 orca·gh(PATH 앞이 아니라 PANEL_ORCA·PANEL_GH 로 지정)와 임시 작업 폴더(mktemp -d)만 씀.
# 실제 orca·gh·Slack·네트워크는 쓰지 않음. COLLECT_DIR=<폴더> 로 변형 사본을 시험할 수 있음(변이 확인용, tests/panel/mutate.sh).
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1

HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS="${COLLECT_DIR:-$HERE/../../hubs/scripts}"
PENDING="$SCRIPTS/slack_pending.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-panel.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
FAKE="$SB/fake" H="$SB/hub"
mkdir -p "$FAKE" "$H"
# 산출물에 새면 안 되는 값. 저장소 검사에 걸리지 않게 조각으로 만듦
MARK="SCREEN-$(printf '%s' BODY)-7f3a"
TOK="xox""b-1234567890-abcdefghijklmnop"
python3 -I "$HERE/fixtures.py" "$FAKE" "$H" "$MARK" "$TOK" || { echo "시험 준비 실패"; exit 1; }
printf '{"updatedAt":"손으로 쓴 값","approvals":[],"todos":[]}\n' > "$H/panel.json"
MAIN_SUM=$(shasum "$H/panel.json" | cut -d' ' -f1)
OUT="$H/panel-auto.json" LOG="$H/panel-collect.log"

run_c() { # 인자를 수집기에 전달. 결과: RC, $SB/out, $SB/err
  env -u PANEL_GH_REPOS -u PANEL_HUB_ROOT LRM_HUB_DIR="$H" FAKE_ORCA_DIR="$FAKE" PANEL_ORCA="$HERE/fake_orca.sh" PANEL_GH="$HERE/fake_gh.sh" \
    SLACK_BOT_TOKEN="$TOK" "$@" > "$SB/out" 2> "$SB/err"
  RC=$?
}
col() { run_c bash "$SCRIPTS/panel_collect.sh" "$@"; }
colenv() { local e="$1"; shift; run_c env $e bash "$SCRIPTS/panel_collect.sh" "$@"; }  # e: "K=V K2=V2"
q() { jq -r "$1" "$OUT" 2>/dev/null; }
ghn() { if [ -f "$FAKE/gh.calls" ]; then wc -l < "$FAKE/gh.calls" | tr -d ' '; else echo 0; fi; }

# ---- 0. 문법·인자 -------------------------------------------------------------------------------
bash -n "$SCRIPTS/panel_collect.sh" && ok "bash -n 통과" || ko "bash -n" ""
python3 -I -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$SCRIPTS/panel_collect.py" && ok "python 구문 통과" || ko "python 구문" ""
col --help; [ "$RC" = 0 ] && grep -q -- '--once' "$SB/out" && ok "--help 종료 0" || ko "--help" "rc=$RC"
col; [ "$RC" = 2 ] && ok "인자 없음 → 종료 2" || ko "인자 없음" "rc=$RC"
col --bogus; [ "$RC" = 2 ] && ok "모르는 인자 → 종료 2" || ko "모르는 인자" "rc=$RC"
colenv "PANEL_GH_REPOS=not-a-repo" --once; [ "$RC" = 2 ] && [ ! -e "$OUT" ] && ok "저장소 형식 오류 → 종료 2, 파일 안 씀" || ko "저장소 형식" "rc=$RC"
colenv "PANEL_GH_INTERVAL=5" --once; [ "$RC" = 2 ] && ok "gh 간격 범위 밖 → 종료 2" || ko "간격 범위" "rc=$RC"

# ---- 1. --print 는 아무것도 쓰지 않음 -----------------------------------------------------------
col --print
[ "$RC" = 0 ] && jq -e '.sources.hubs.ok' "$SB/out" >/dev/null && [ ! -e "$OUT" ] && [ ! -e "$LOG" ] && [ ! -e "$H/.panel-gh.stamp" ] && ok "--print: 표준출력 JSON, 파일·로그·간격 표식 없음" || ko "--print" "rc=$RC"
cp "$SB/out" "$SB/print.json"

# ---- 2. 1회 수집 --------------------------------------------------------------------------------
col --once
[ "$RC" = 0 ] && jq -e . "$OUT" >/dev/null && ok "--once: 종료 0, panel-auto.json 유효 JSON" || ko "--once" "rc=$RC $(head -c 200 "$SB/err")"
[ "$(q '.version')" = 1 ] && [ "$(q '.updatedAt|type')" = number ] && q '.updatedAtText' | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8}$' && ok "updatedAt epoch + 사람용 문자열" || ko "updatedAt" "$(q '.updatedAt')"
[ "$(q '[.sources[] | has("ok") and has("err") and has("at") and has("stale") and has("value")] | all')" = true ] && ok "원료마다 ok·err·at·stale·value" || ko "원료 키" ""

# ---- 3. 허브 분류 -------------------------------------------------------------------------------
st() { q ".sources.hubs.value.rows[] | select(.hub == \"$1\") | .state" | head -1; }
[ "$(st coord-busy-hub)" = busy ] && ok "분류: 회전 기호+… → 작업 중(입력 칸이 보여도)" || ko "busy" "$(st coord-busy-hub)"
[ "$(st coord-esc-hub)" = busy ] && ok "분류: esc to interrupt → 작업 중" || ko "esc" "$(st coord-esc-hub)"
[ "$(st coord-idle-hub)" = idle ] && ok "분류: 빈 입력 칸(끝난 턴 요약 줄은 작업 중 아님) → 대기" || ko "idle" "$(st coord-idle-hub)"
[ "$(st coord-ask-hub)" = approval ] && ok "분류: Do you want·❯ 1. → 승인창" || ko "approval" "$(st coord-ask-hub)"
[ "$(st coord-unk-hub)" = unknown ] && ok "분류: 표식 없음·방금 출력 → 확인불가" || ko "unknown" "$(st coord-unk-hub)"
[ "$(st coord-quiet-hub)" = idle ] && [ "$(q '.sources.hubs.value.rows[] | select(.hub=="coord-quiet-hub") | .quietMin')" -ge 11 ] && ok "분류: 표식 없음·12분 출력 없음 → 대기, quietMin 계산" || ko "quiet" "$(st coord-quiet-hub)"
[ "$(st coord-gone-hub)" = unknown ] && [ "$(q '.sources.hubs.value.rows[] | select(.hub=="coord-gone-hub") | .connected')" = false ] && ok "분류: 끊긴 터미널 → 확인불가 + connected:false" || ko "gone" "$(st coord-gone-hub)"
[ "$(q '[.sources.hubs.value.rows[] | select(.hub=="coord-dup-hub") | .dup] | all and length == 2')" = true ] && ok "같은 허브 창 2개 → dup:true" || ko "dup" ""
[ "$(q '[.sources.hubs.value.rows[].hub] | (index("coord-noagent-hub") == null) and (index("feature-branch") == null)')" = true ] && ok "허브 식별: 에이전트 없는 창·coord-* 아닌 폴더 제외" || ko "식별" "$(q '[.sources.hubs.value.rows[].hub]|join(",")')"
[ "$(q '.sources.hubs.value.counts | "\(.busy) \(.approval) \(.idle) \(.unknown)"')" = "3 1 4 2" ] && ok "분류 건수: 작업 중 3·승인창 1·대기 4·확인불가 2" || ko "counts" "$(q '.sources.hubs.value.counts')"
[ "$(q '.sources.hubs.value.rows[] | select(.hub=="coord-busy-hub") | .name')" = busy ] && ok "표시 이름: coord-·-hub 제거" || ko "name" ""
colenv "PANEL_HUB_ROOT=/ws/proj" --print
[ "$(jq -r '[.sources.hubs.value.rows[].hub] | index("coord-elsewhere-hub") == null' "$SB/out")" = true ] && [ "$(jq '.sources.hubs.value.rows|length' "$SB/out")" = 9 ] && ok "PANEL_HUB_ROOT 지정 → 그 아래 허브만" || ko "hub root" "$(jq '.sources.hubs.value.rows|length' "$SB/out")"

# ---- 4. 결정 대기 -------------------------------------------------------------------------------
[ "$(q '.sources.pending.value.count')" = 2 ] && ok "결정 대기 2건(반응·완료 기록·답글·비결정·같은 제목 제외)" || ko "pending" "$(q '.sources.pending.value.count')"
[ "$(q '[.sources.pending.value.items[].title | length <= 40] | all')" = true ] && [ "$(q '.sources.pending.value.items[0].ageMin')" -ge 9 ] && ok "결정 제목 40자 이내·경과 분" || ko "pending 항목" ""
# slack_pending.sh 판정식이 바뀌면 수집기와 어긋남 → 같은 규칙인지 원본에서 대조(slack_pending.sh 는 수정하지 않고 읽기만)
grep -qF 'decisions = [s for s in sent if s.get("ts") and not s.get("thread_ts") and "결정" in s.get("title", "")]' "$PENDING" \
  && grep -qF 'reacted[d.get("parent_ts")] = d' "$PENDING" && grep -qF 'done_file = os.path.join(H, "slack-done.txt")' "$PENDING" \
  && ok "slack_pending.sh 의 결정 대기 판정식이 수집기와 같은 규칙(바뀌면 이 시험이 실패)" || ko "판정식 대조" "slack_pending.sh 규칙이 바뀜 — panel_collect.py collect_pending 갱신 필요"

# ---- 5. 최근 기록 -------------------------------------------------------------------------------
[ "$(q '[.sources.push.value.lines[].event] | join(",")')" = "hold,off,fail,ok,hold" ] && [ "$(q '.sources.push.value.lines[0].reason')" = dialog ] && ok "slack-push.log 끝 5줄: 결과·사유 코드만" || ko "push" "$(q '[.sources.push.value.lines[].event]|join(",")')"
[ "$(q '.sources.push.value.lines[0] | keys | join(",")')" = "at,event,reason" ] && ok "push 줄에 시각·결과·사유 외 값 없음(핸들·숫자 없음)" || ko "push 키" "$(q '.sources.push.value.lines[0]|keys')"
[ "$(q '.sources.workers.value.live')" = 1 ] && [ "$(q '[.sources.workers.value.recent[].status] | join(",")')" = "succeeded,failed,stopped" ] && ok "최근 종료 작업 상태 3건·살아 있는 워커 1" || ko "workers" "$(q '.sources.workers.value')"
[ "$(q '.sources.workers.value.recent[0].at')" = "$(python3 -I -c 'import calendar,time; print(calendar.timegm(time.strptime("2026-11-10 05:04:08","%Y-%m-%d %H:%M:%S")))')" ] && [ "$(q '.sources.workers.value.recent[0].title | length')" -le 40 ] && [ "$(q '.sources.workers.value.recent[1].title')" = "설치 정리" ] && ok "작업 제목(task-list, 40자)·종료 시각(UTC→epoch)" || ko "worker 제목·시각" "$(q '.sources.workers.value.recent[0]')"
colenv "FAKE_ORCA_FAIL=tasks" --print
[ "$(jq -r '.sources.workers.ok' "$SB/out")" = true ] && [ "$(jq -r '.sources.workers.value.recent[0].title' "$SB/out")" = null ] && [ "$(jq -r '.sources.workers.value.recent[0].status' "$SB/out")" = succeeded ] && ok "task-list 실패 → 제목만 빠지고 상태 유지" || ko "task-list 실패" ""

# ---- 6. gh: 끔·간격·실패 -------------------------------------------------------------------------
[ "$(q '.sources.gh.off')" = true ] && [ "$(ghn)" = 0 ] && ok "PANEL_GH_REPOS 없음 → gh 안 부름(off)" || ko "gh off" "calls=$(ghn)"
R="PANEL_GH_REPOS=acme/app,acme/web"
colenv "$R" --once
[ "$(ghn)" = 2 ] && [ "$(q '.sources.gh.value.open')" = 8 ] && [ "$(q '.sources.gh.value.ciFail')" = 4 ] && [ "$(q '.sources.gh.ok')" = true ] && ok "gh: 저장소 2개 → 열린 PR 8·CI 실패 4" || ko "gh 집계" "calls=$(ghn) $(q '.sources.gh.value')"
GH_AT=$(q '.sources.gh.at')
colenv "$R" --once
[ "$(ghn)" = 2 ] && [ "$(q '.sources.gh.value.open')" = 8 ] && [ "$(q '.sources.gh.at')" = "$GH_AT" ] && grep -q 'gh=skip' "$LOG" && ok "gh: 5분 안 재실행 → 호출 안 함, 이전 값·시각 유지" || ko "gh 간격" "calls=$(ghn)"
touch -t 202601010000 "$H/.panel-gh.stamp"
colenv "$R FAKE_GH_MODE=fail" --once
[ "$(ghn)" = 3 ] && [ "$(q '.sources.gh.ok')" = false ] && [ "$(q '.sources.gh.stale')" = true ] && [ "$(q '.sources.gh.err')" = exit ] && [ "$(q '.sources.gh.value.open')" = 8 ] && [ "$(q '.sources.gh.at')" = "$GH_AT" ] && ok "gh 실패 → ok:false·stale·이전 값과 성공 시각 유지" || ko "gh 실패" "$(q '.sources.gh|del(.value)')"
touch -t 202601010000 "$H/.panel-gh.stamp"
colenv "$R FAKE_GH_MODE=auth" --once
[ "$(q '.sources.gh.err')" = auth ] && ok "gh 미인증 → err:auth" || ko "gh auth" "$(q '.sources.gh.err')"
touch -t 202601010000 "$H/.panel-gh.stamp"
colenv "$R PANEL_GH=$SB/nope" --once
[ "$(q '.sources.gh.err')" = missing ] && [ "$RC" = 0 ] && ok "gh 미설치 → err:missing, 종료 0" || ko "gh missing" "$(q '.sources.gh.err') rc=$RC"

# ---- 7. 원료 실패: 다른 원료는 그대로, 실패한 원료는 이전 값 유지 ------------------------------------
HUBS_BEFORE=$(q '.sources.hubs.value | tojson'); HUBS_AT=$(q '.sources.hubs.at')
sleep 1
colenv "FAKE_ORCA_FAIL=terms" --once
[ "$RC" = 0 ] && [ "$(q '.sources.hubs.ok')" = false ] && [ "$(q '.sources.hubs.stale')" = true ] && [ "$(q '.sources.hubs.value | tojson')" = "$HUBS_BEFORE" ] && [ "$(q '.sources.hubs.at')" = "$HUBS_AT" ] && [ "$(q '.sources.pending.ok')" = true ] && ok "허브 원료 실패 → 이전 값·성공 시각 유지 + stale, 다른 원료 정상" || ko "원료 실패" "$(q '.sources.hubs|del(.value)')"
grep -q 'hubs=fail:exit' "$LOG" && ok "로그: 원료별 결과 코드" || ko "로그 코드" "$(tail -1 "$LOG")"
col --once
[ "$(q '.sources.hubs.ok')" = true ] && [ "$(q '.sources.hubs.stale')" = false ] && ok "원료 회복 → ok·stale 해제" || ko "회복" ""

# ---- 8. 원자 쓰기·깨진 파일 복구 -------------------------------------------------------------------
mkdir -p "$SB/atomic"
r=$(LRM_HUB_DIR="$SB/atomic" python3 -I "$HERE/atomic_check.py" "$SCRIPTS" 2>&1)
[ "$r" = ok ] && ok "쓰기 도중 실패 → 기존 파일 온전·임시 파일 없음(임시→rename)" || ko "원자 쓰기" "$r"
# 읽는 쪽이 늘 유효 JSON 을 보는지: 연속 쓰기 중 반복 읽기
( for _ in 1 2 3 4 5 6; do col --once; done ) &
WP=$!
bad=0 reads=0
while kill -0 "$WP" 2>/dev/null; do
  if [ -e "$OUT" ]; then reads=$((reads + 1)); jq -e . "$OUT" >/dev/null 2>&1 || bad=$((bad + 1)); fi
done
wait "$WP"
[ "$bad" = 0 ] && [ "$reads" -gt 0 ] && ok "연속 쓰기 중 읽기 ${reads}회 모두 유효 JSON" || ko "동시 읽기" "깨진 읽기 $bad/$reads"
[ -z "$(find "$H" -name '.panel-auto.*.tmp')" ] && ok "임시 파일 잔존 없음" || ko "임시 파일" "$(find "$H" -name '.panel-auto.*.tmp' | wc -l)"
printf '{"version": 1, "sour' > "$OUT"
col --once
[ "$RC" = 0 ] && jq -e '.sources.hubs.ok' "$OUT" >/dev/null && tail -1 "$LOG" | grep -q 'prev=corrupt' && ok "깨진 panel-auto.json → 재실행으로 복구(로그 prev=corrupt)" || ko "복구" "rc=$RC"

# ---- 9. 잠금 -----------------------------------------------------------------------------------
mkdir "$H/.panel-collect.lock"
U0=$(q '.updatedAt'); sleep 1
col --once
[ "$RC" = 0 ] && [ "$(q '.updatedAt')" = "$U0" ] && tail -1 "$LOG" | grep -q 'skip reason=locked' && ok "잠금 있음 → 건너뜀(종료 0, 파일 그대로)" || ko "잠금" "rc=$RC"
touch -t 202601010000 "$H/.panel-collect.lock"
col --once
[ "$(q '.updatedAt')" != "$U0" ] && grep -q 'lock reclaimed' "$LOG" && [ ! -d "$H/.panel-collect.lock" ] && ok "10분 넘은 잠금 회수 후 실행, 끝나면 잠금 해제" || ko "오래된 잠금" ""
n0=$(grep -c 'skip reason=locked' "$LOG")
( colenv "FAKE_ORCA_SLEEP=2" --once ) & P1=$!
sleep 0.5
colenv "FAKE_ORCA_SLEEP=2" --once
wait "$P1"
[ "$(($(grep -c 'skip reason=locked' "$LOG") - n0))" = 1 ] && ok "동시 2회 실행 → 하나만 수집, 하나는 건너뜀" || ko "동시 실행" "skip=$(($(grep -c 'skip reason=locked' "$LOG") - n0))"

# ---- 10. 메인 파일·비밀·화면 본문 --------------------------------------------------------------------
[ "$(shasum "$H/panel.json" | cut -d' ' -f1)" = "$MAIN_SUM" ] && ok "메인 panel.json 바이트 단위 불변" || ko "panel.json" "바뀜"
leak=0
for f in "$OUT" "$LOG" "$SB/print.json"; do grep -qF "$MARK" "$f" && leak=1; grep -qF "$TOK" "$f" && leak=1; grep -Eq 'xox[abprs]-|gh[pousr]_[A-Za-z0-9]{20}' "$f" && leak=1; done
[ "$leak" = 0 ] && ok "산출물·로그·--print 에 화면 본문·Slack 본문·토큰 모양 없음" || ko "누출" "본문 또는 토큰이 남음"
! grep -q 'preview' "$OUT" && ok "panel-auto.json 에 preview 키 없음" || ko "preview 키" ""
! grep -q "tok=$TOK" "$FAKE/orca.calls" && ok "orca 자식에 Slack 토큰 상속 안 됨" || ko "토큰 상속" ""
! grep -Eq '["/](slack-bot|main)\.env' "$SCRIPTS/panel_collect.py" "$SCRIPTS/panel_collect.sh" && ok "수집기가 slack-bot.env·main.env 경로를 쓰지 않음" || ko "env 참조" ""

# ---- 11. panel.env 설정 파일 --------------------------------------------------------------------
printf 'PANEL_GH_REPOS=acme/app\nSLACK_BOT_TOKEN=%s\nPANEL_RECENT=2\nrm -rf /\n' "$TOK" > "$H/panel.env"
touch -t 202601010000 "$H/.panel-gh.stamp"
g0=$(ghn)
col --once
[ "$(($(ghn) - g0))" = 1 ] && [ "$(q '.sources.workers.value.recent | length')" = 2 ] && ! grep -qF "$TOK" "$OUT" && ok "panel.env: 정해진 키만 읽음(저장소 1개·최근 2건), 다른 줄 무시" || ko "panel.env" "gh=$(($(ghn) - g0))"
colenv "PANEL_RECENT=3" --print
[ "$(jq '.sources.workers.value.recent | length' "$SB/out")" = 3 ] && ok "환경 변수가 panel.env 보다 우선" || ko "우선순위" ""

# ---- 12. mod 순수 함수(합치기·오래됨 판정) — node 내장 러너 ------------------------------------------
if command -v node >/dev/null && node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 23 ? 0 : 1)'; then
  out=$(node --test "$HERE/board.test.mjs" 2>&1); rc=$?
  n=$(echo "$out" | sed -n 's/^ℹ pass //p')
  [ "$rc" = 0 ] && ok "mod 순수 함수 node 시험 ${n}건 통과" || ko "mod 순수 함수" "$(echo "$out" | grep -m2 '✖' | tr '\n' ' ')"
else
  printf 'SKIP  mod 순수 함수 시험 — node 23 이상(타입 제거 내장) 없음\n'
fi

echo
echo "결과: 통과 $PASS, 실패 $FAIL"
[ "$FAIL" = 0 ]
