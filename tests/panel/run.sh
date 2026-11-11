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
trap '[ -n "${KEEP:-}" ] || rm -rf "${SB:?}"' EXIT
FAKE="$SB/fake" H="$SB/hub" FHOME="$SB/home"
mkdir -p "$FAKE" "$H" "$FHOME/ws"
# 산출물에 새면 안 되는 값. 저장소 검사에 걸리지 않게 조각으로 만듦
MARK="SCREEN-$(printf '%s' BODY)-7f3a"
TOK="xox""b-1234567890-abcdefghijklmnop"
FIX_HOME="$FHOME" python3 -I "$HERE/fixtures.py" "$FAKE" "$H" "$MARK" "$TOK" || { echo "시험 준비 실패"; exit 1; }
printf '{"updatedAt":"손으로 쓴 값","approvals":[],"todos":[]}\n' > "$H/panel.json"
MAIN_SUM=$(shasum "$H/panel.json" | cut -d' ' -f1)
OUT="$H/panel-auto.json" LOG="$H/panel-collect.log"

run_c() { # 인자를 수집기에 전달. 결과: RC, $SB/out, $SB/err
  env -u PANEL_GH_REPOS -u PANEL_HUB_ROOT -u PANEL_WS_DIR -u PANEL_DISK_MIN_GB HOME="$FHOME" LRM_HUB_DIR="$H" FAKE_ORCA_DIR="$FAKE" \
    PANEL_ORCA="$HERE/fake_orca.sh" PANEL_GH="$HERE/fake_gh.sh" PANEL_DF="$HERE/fake_df.sh" PANEL_DU="$HERE/fake_du.sh" PANEL_GIT="$HERE/fake_git.sh" \
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
[ "$(st coord-unk-hub)" = unknown ] && ok "분류: 표식 없음·방금 출력(글머리 '· 한국어…' 줄은 작업 표식 아님) → 확인불가" || ko "unknown" "$(st coord-unk-hub)"
[ "$(st coord-quiet-hub)" = idle ] && [ "$(q '.sources.hubs.value.rows[] | select(.hub=="coord-quiet-hub") | .quietMin')" -ge 11 ] && ok "분류: 표식 없음·12분 출력 없음 → 대기, quietMin 계산" || ko "quiet" "$(st coord-quiet-hub)"
[ "$(st coord-gone-hub)" = unknown ] && [ "$(q '.sources.hubs.value.rows[] | select(.hub=="coord-gone-hub") | .connected')" = false ] && ok "분류: 끊긴 터미널 → 확인불가 + connected:false" || ko "gone" "$(st coord-gone-hub)"
[ "$(q '[.sources.hubs.value.rows[] | select(.hub=="coord-dup-hub") | "\(.dup) \(.quietMin)"] | join(",")')" = "true 5" ] && ok "같은 허브 창 2개 → 최근 출력 1개만 남기고 dup:true" || ko "dup" ""
[ "$(q '[.sources.hubs.value.rows[].hub] | (index("coord-noagent-hub") == null) and (index("feature-branch") == null)')" = true ] && ok "허브 식별: 에이전트 없는 창·coord-* 아닌 폴더 제외" || ko "식별" "$(q '[.sources.hubs.value.rows[].hub]|join(",")')"
[ "$(q '.sources.hubs.value.counts | "\(.busy) \(.approval) \(.idle) \(.unknown)"')" = "3 1 3 2" ] && ok "분류 건수(허브당 1줄): 작업 중 3·승인창 1·대기 3·확인불가 2" || ko "counts" "$(q '.sources.hubs.value.counts')"
[ "$(q '.sources.hubs.value.rows[] | select(.hub=="coord-busy-hub") | .name')" = busy ] && ok "표시 이름: coord-·-hub 제거" || ko "name" ""
colenv "PANEL_HUB_ROOT=/ws/proj" --print
[ "$(jq -r '[.sources.hubs.value.rows[].hub] | index("coord-elsewhere-hub") == null' "$SB/out")" = true ] && [ "$(jq '.sources.hubs.value.rows|length' "$SB/out")" = 8 ] && ok "PANEL_HUB_ROOT 지정 → 그 아래 허브만" || ko "hub root" "$(jq '.sources.hubs.value.rows|length' "$SB/out")"

# ---- 4. 결정 대기 -------------------------------------------------------------------------------
[ "$(q '.sources.pending.value.count')" = 2 ] && ok "결정 대기 2건(반응·완료 기록·답글·비결정·같은 제목 제외)" || ko "pending" "$(q '.sources.pending.value.count')"
[ "$(q '[.sources.pending.value.items[].title | length <= 40] | all')" = true ] && [ "$(q '.sources.pending.value.items[0].ageMin')" -ge 9 ] && ok "결정 제목 40자 이내·경과 분" || ko "pending 항목" ""
[ "$(q '.sources.pending.value.items[0] | "\(.id) \(.state)"')" = "null null" ] && ok "옛 결정 카드: 결정 번호·상태 없음(null)" || ko "pending 옛 카드" "$(q '.sources.pending.value.items[0]')"
# 번호 카드 형식(이모지 카드 브랜치) 기록: 제목에 '결정' 이 없어도 대기, 보류·충돌은 대기 + 상태, 해석 안 된 반응은 답 아님
python3 -I "$HERE/fixtures_cards.py" "$SB/cards" "$MARK" || ko "번호 카드 기록 준비" ""
colenv "LRM_HUB_DIR=$SB/cards" --print
cp "$SB/out" "$SB/print-cards.json"
pc() { jq -r ".sources.pending.value | $1" "$SB/print-cards.json"; }
[ "$(pc '.count')" = 7 ] && [ "$(pc '[.items[].title] | join("|")')" = "🙋 결정 옛 카드 대기|배포 순서|문서 위치|같은 제목|같은 제목|이름 그대로 반응|번호 없음" ] \
  && ok "번호 카드: 대기 7건(선택·추천대로·완료 기록은 닫힘, 같은 제목도 카드마다 따로, 옛 카드 규칙 유지)" || ko "번호 카드 대기" "$(pc '[.items[].title]|join("|")')"
[ "$(pc '[.items[] | "\(.id):\(.state)"] | join(",")')" = "null:null,D-0002:hold,D-0003:conflict,D-0004:null,D-0005:null,D-0006:null,null:null" ] \
  && ok "번호 카드: 결정 번호와 상태(보류 hold·충돌 conflict·답 없음 null)" || ko "번호 카드 상태" "$(pc '[.items[]|"\(.id):\(.state)"]|join(",")')"
# 결정 번호가 손상·누락된 번호 카드는 옛 경로: 반응 하나로 닫히고(번호 손상), 반응이 없으면 번호 없이 제목만 대기(번호 누락)
! grep -qF 'bad id' "$SB/print-cards.json" && [ "$(pc '[.items[] | select(.title == "번호 손상")] | length')" = 0 ] \
  && [ "$(pc '[.items[] | select(.title == "번호 없음") | "\(.id):\(.state)"] | join(",")')" = "null:null" ] \
  && ok "번호 카드: 결정 번호 손상 → 반응으로 닫힘·번호 안 실음, 누락 → 제목만 대기" || ko "번호 손상·누락" "$(pc '[.items[]|"\(.title):\(.id)"]|join(",")')"
# slack_pending.sh 판정식이 바뀌면 수집기와 어긋남 → 같은 규칙인지 원본에서 대조(slack_pending.sh 는 수정하지 않고 읽기만).
# main 형식(옛 카드만)과 번호 카드 형식(이모지 카드 브랜치) 둘 다 인정. 어느 쪽도 아니면 실패
has() { grep -qF -- "$1" "$PENDING"; }
if has 'decisions = [s for s in sent if s.get("ts") and not s.get("thread_ts") and "결정" in s.get("title", "")]' \
  && has 'reacted[d.get("parent_ts")] = d' && has 'done_file = os.path.join(H, "slack-done.txt")'; then
  ok "slack_pending.sh 판정식 = 수집기 규칙(main 형식, 바뀌면 이 시험이 실패)"
elif has 'decisions = [s for s in sent if s.get("ts") and not s.get("thread_ts")' \
  && has 'and ("결정" in s.get("title", "") or s["ts"] in cards or (s.get("form_kind") == "decide" and "options" in s))]' \
  && has 'done = {t for t in reacted if t not in cards} | {t for t, st in cards.items() if st and st[0] == "closed"}' \
  && has 'if s.get("ts") and s.get("form_kind") == "decide" and "options" in s' \
  && has 'and isinstance(s.get("decision_id"), str) and DECISION_ID_RE.fullmatch(s["decision_id"])}' \
  && has 'if not isinstance(did, str) or not DECISION_ID_RE.fullmatch(did):' \
  && has 'key = ("card", s.get("decision_id"), s["ts"]) if card else s["title"]' \
  && has "$(grep -m1 '^DECISION_ID_RE = ' "$SCRIPTS/panel_collect.py")" \
  && has 'if len(keys) > 1 or any(d.get("conflict") is True for d in ans):' \
  && has 'k = "hold" if d["action"] == "hold" else str(n) if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 4 else None' \
  && has "$(grep -m1 '^ANSWER_ACTIONS = ' "$SCRIPTS/panel_collect.py")" && has 'done_file = os.path.join(H, "slack-done.txt")'; then
  ok "slack_pending.sh 판정식 = 수집기 규칙(번호 카드 형식, 바뀌면 이 시험이 실패)"
else
  ko "판정식 대조" "slack_pending.sh 규칙이 바뀜 — panel_collect.py collect_pending·card_state 갱신 필요"
fi

# ---- 5. 최근 기록 -------------------------------------------------------------------------------
[ "$(q '[.sources.push.value.lines[].event] | join(",")')" = "hold,off,fail,ok,hold" ] && [ "$(q '.sources.push.value.lines[0].reason')" = dialog ] && ok "slack-push.log 끝 5줄: 결과·사유 코드만" || ko "push" "$(q '[.sources.push.value.lines[].event]|join(",")')"
[ "$(q '.sources.push.value.lines[0] | keys | join(",")')" = "at,event,reason" ] && ok "push 줄에 시각·결과·사유 외 값 없음(핸들·숫자 없음)" || ko "push 키" "$(q '.sources.push.value.lines[0]|keys')"
[ "$(q '.sources.workers.value.live')" = 1 ] && [ "$(q '[.sources.workers.value.recent[].status] | join(",")')" = "succeeded,failed,stopped" ] && ok "최근 종료 작업 상태 3건·살아 있는 워커 1" || ko "workers" "$(q '.sources.workers.value')"
[ "$(q '.sources.workers.value.recent[0].at')" = "$(python3 -I -c 'import calendar,time; print(calendar.timegm(time.strptime("2026-11-10 05:04:08","%Y-%m-%d %H:%M:%S")))')" ] && [ "$(q '.sources.workers.value.recent[0].title | length')" -le 40 ] && [ "$(q '.sources.workers.value.recent[1].title')" = "설치 정리" ] && ok "작업 제목(task-list, 40자)·종료 시각(UTC→epoch)" || ko "worker 제목·시각" "$(q '.sources.workers.value.recent[0]')"
colenv "FAKE_ORCA_FAIL=tasks" --print
[ "$(jq -r '.sources.workers.ok' "$SB/out")" = true ] && [ "$(jq -r '.sources.workers.value.recent[0].title' "$SB/out")" = null ] && [ "$(jq -r '.sources.workers.value.recent[0].status' "$SB/out")" = succeeded ] && ok "task-list 실패 → 제목만 빠지고 상태 유지" || ko "task-list 실패" ""
# 예상 밖 JSON 모양(tasks 가 숫자) → 그 원료만 실패(오류 종류만), 다른 원료·파일 쓰기는 그대로
colenv "FAKE_ORCA_ALT=tasks" --print
[ "$RC" = 0 ] && [ "$(jq -r '.sources.workers.ok' "$SB/out")" = false ] && [ "$(jq -r '.sources.workers.err' "$SB/out")" = "internal:TypeError" ] \
  && [ "$(jq -r '.sources.hubs.ok' "$SB/out")" = true ] && [ "$(jq -r '.sources.idle.err' "$SB/out")" = "workers:internal:TypeError" ] \
  && ok "예상 밖 모양 → 그 원료(부분)만 internal:<예외 이름>, 다른 원료 정상" || ko "예상 밖 모양" "rc=$RC $(jq -c '[.sources.workers.err, .sources.idle.err]' "$SB/out" 2>/dev/null)"

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
[ "$(q '.sources.gh.value.capped')" = false ] && ok "gh: PR 4건 → capped:false" || ko "gh capped" "$(q '.sources.gh.value.capped')"
[ "$(ghn)" = 3 ] && [ "$(q '.sources.gh.ok')" = false ] && [ "$(q '.sources.gh.stale')" = true ] && [ "$(q '.sources.gh.err')" = exit ] && [ "$(q '.sources.gh.value.open')" = 8 ] && [ "$(q '.sources.gh.at')" = "$GH_AT" ] && ok "gh 실패 → ok:false·stale·이전 값과 성공 시각 유지" || ko "gh 실패" "$(q '.sources.gh|del(.value)')"
touch -t 202601010000 "$H/.panel-gh.stamp"
colenv "$R FAKE_GH_MODE=auth" --once
[ "$(q '.sources.gh.err')" = auth ] && ok "gh 미인증 → err:auth" || ko "gh auth" "$(q '.sources.gh.err')"
touch -t 202601010000 "$H/.panel-gh.stamp"
colenv "$R PANEL_GH=$SB/nope" --once
[ "$(q '.sources.gh.err')" = missing ] && [ "$RC" = 0 ] && ok "gh 미설치 → err:missing, 종료 0" || ko "gh missing" "$(q '.sources.gh.err') rc=$RC"
touch -t 202601010000 "$H/.panel-gh.stamp"
colenv "PANEL_GH_REPOS=acme/app FAKE_GH_MODE=many" --once
[ "$(q '.sources.gh.value.open')" = 100 ] && [ "$(q '.sources.gh.value.capped')" = true ] && [ "$(q '.sources.gh.value.repos[0].capped')" = true ] && ok "gh: --limit 만큼 꽉 차면 capped:true(숫자는 하한)" || ko "gh capped" "$(q '.sources.gh.value|del(.repos)')"

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
# 실행 중 잠금이 회수돼 다른 실행의 새 잠금이 되면(폴더를 옮기고 새로 만들어 흉내) 끝날 때 그 잠금을 지우지 않음
( colenv "FAKE_ORCA_SLEEP=2" --once ) & P1=$!
sleep 0.5
mv "$H/.panel-collect.lock" "$SB/lock-a" && mkdir "$H/.panel-collect.lock"
wait "$P1"
[ -d "$H/.panel-collect.lock" ] && tail -1 "$LOG" | grep -q 'lock lost' && ok "잠금이 회수·재생성되면 남의 잠금은 지우지 않음(로그 lock lost)" || ko "남의 잠금" "$(tail -1 "$LOG")"
mv "$H/.panel-collect.lock" "$SB/lock-b"

# ---- 10. 메인 파일·비밀·화면 본문 --------------------------------------------------------------------
[ "$(shasum "$H/panel.json" | cut -d' ' -f1)" = "$MAIN_SUM" ] && ok "메인 panel.json 바이트 단위 불변" || ko "panel.json" "바뀜"
leak=0
for f in "$OUT" "$LOG" "$SB/print.json" "$SB/print-cards.json"; do grep -qF "$MARK" "$f" && leak=1; grep -qF "$TOK" "$f" && leak=1; grep -Eq 'xox[abprs]-|gh[pousr]_[A-Za-z0-9]{20}' "$f" && leak=1; done
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

# ---- 13. 쉬는 자원(idle): 목록·숫자만, 정리는 사람이 ------------------------------------------------
col --once
iv() { q ".sources.idle.value | $1"; }
[ "$(q '.sources.idle.ok')" = true ] && [ "$(iv '.terminals | "\(.total) \(.agents)"')" = "12 11" ] && ok "쉬는 자원: 터미널 총 12·에이전트 11(terminal list 재사용)" || ko "idle 터미널" "$(q '.sources.idle|del(.value)')"
[ "$(iv '.workers | "\(.live) \(.succeeded) \(.failed) \(.stopped) \(.abandoned) \(.leftover)"')" = "1 1 1 1 1 2" ] && [ "$(iv '.workers.items | map("\(.dispatch) \(.title) \(.terminal)") | join(",")')" = "ctx_left 설치 정리 retained,ctx_aband 버려진 작업 reclaimable" ] && ok "워커 상태별 건수·종료 뒤 남은 터미널 2건(3쪽 전부, 쪽 사이 겹친 행 1번, abandoned 포함)" || ko "idle 워커" "$(iv '.workers|del(.terminalStates)')"
[ "$(iv '.workers | "\(.seen) \(.total) \(.complete) \(.terminalStates.retained) \(.terminalStates.reclaimable)"')" = "5 5 true 1 1" ] && [ "$(grep -c 'worker-list' "$FAKE/orca.calls")" -ge 3 ] \
  && ok "worker-list: nextCursor 로 끝 쪽까지, page.total·Orca counts(터미널 상태별 총계) 실음" || ko "worker 쪽 넘기기" "$(iv '.workers|del(.items)')"
n_wl=$(grep -c 'worker-list' "$FAKE/orca.calls")
colenv "FAKE_ORCA_ALT=workers_c3" --print
n_wl=$(($(grep -c 'worker-list' "$FAKE/orca.calls") - n_wl))
[ "$(jq -r '.sources.idle.value.workers.complete' "$SB/out")" = false ] && [ "$(jq -r '.sources.idle.ok' "$SB/out")" = true ] && [ "$(jq -r '.sources.idle.value.workers.seen' "$SB/out")" = 5 ] && [ "$n_wl" = 60 ] \
  && ok "끝나지 않는 커서 → 쪽 수 상한(60쪽)에서 멈춤, complete:false" || ko "쪽 수 상한" "쪽=$n_wl $(jq -c '.sources.idle.value.workers|del(.items)' "$SB/out")"
# 쪽이 느리면 시간 상한이 쪽 수 상한보다 먼저 멈춤(쪽마다 0.3초, 시험용 상한 1초 → 60쪽 전에 끝)
n_wl=$(grep -c 'worker-list' "$FAKE/orca.calls"); t0=$(date +%s)
colenv "FAKE_ORCA_ALT=workers_c3 FAKE_ORCA_SLEEP=0.3 PANEL_WORKER_BUDGET=1" --print
n_wl=$(($(grep -c 'worker-list' "$FAKE/orca.calls") - n_wl)); el=$(($(date +%s) - t0))
[ "$(jq -r '.sources.idle.value.workers.complete' "$SB/out")" = false ] && [ "$(jq -r '.sources.idle.ok' "$SB/out")" = true ] && [ "$n_wl" -ge 2 ] && [ "$n_wl" -le 10 ] \
  && ok "느린 쪽 → 시간 상한에서 멈춤(${n_wl}쪽, ${el}초), complete:false" || ko "시간 상한" "쪽=$n_wl ${el}초 $(jq -c '.sources.idle.value.workers|del(.items)' "$SB/out")"
colenv "FAKE_ORCA_FAIL=workers_c2" --print
[ "$(jq -r '.sources.workers.ok' "$SB/out")" = false ] && [ "$(jq -r '.sources.idle.err' "$SB/out")" = "workers:exit" ] \
  && ok "중간 쪽 실패 → 워커 숫자는 실패 처리(부분 숫자를 맞는 값처럼 싣지 않음)" || ko "중간 쪽 실패" "$(jq -c '[.sources.workers.err,.sources.idle.err]' "$SB/out")"
colenv "FAKE_ORCA_ALT=terms" --print
[ "$(jq -r '.sources.hubs.value.truncated' "$SB/out")" = true ] && [ "$(jq -r '.sources.idle.value.workspaces.truncated' "$SB/out")" = true ] && [ "$(q '.sources.hubs.value.truncated')" = false ] \
  && ok "terminal list 가 잘리면 허브·워크스페이스 truncated:true" || ko "terminal 잘림" "$(jq -c '[.sources.hubs.value.truncated,.sources.idle.value.workspaces.truncated]' "$SB/out")"
[ "$(iv '.workspaces | "\(.total) \(.noTerminal) \(.items | length) \(.more)"')" = "25 22 20 2" ] && ok "터미널 없는 워크스페이스 22/25, 목록 20개 + 외 2개(메인 제외)" || ko "idle 워크스페이스" "$(iv '.workspaces|del(.items)')"
[ "$(iv '.workspaces.items[0].name')" = ws-03 ] && [ "$(iv '.workspaces.items[-1].name')" != ws-05 ] && [ "$(iv '[.workspaces.items[].path | startswith("~/ws/")] | all')" = true ] && ok "오래 쉰 것부터, 경로는 ~ 표기, 활동 시각 0 은 만든 시각·없으면 뒤로" || ko "idle 정렬·경로" "$(iv '[.workspaces.items[].name]|join(",")')"
[ "$(iv '.workspaces.items[0].merged')" = true ] && [ "$(iv '.workspaces.items[] | select(.name=="ws-04") | .merged')" = null ] && [ "$(iv '.workspaces.items[] | select(.name=="ws-06") | .merged')" = false ] && [ "$(iv '.workspaces.items[0].branch')" = feat/w3 ] && ok "병합 여부(git merge-base)·기준 없으면 null·브랜치 이름" || ko "idle 병합" ""
[ "$(iv '.disk | "\(.freeGB == 100) \(.usedPct) \(.warn)"')" = "true 89 false" ] && ok "디스크: 여유 100GB·사용률 89%, 50GB 이상이면 경고 없음" || ko "idle 디스크" "$(iv '.disk')"
colenv "FAKE_DF_AVAIL=20971520" --print
[ "$(jq -r '.sources.idle.value.disk.warn' "$SB/out")" = true ] && ok "디스크 여유 20GB < 50GB → warn" || ko "디스크 경고" "$(jq -c '.sources.idle.value.disk' "$SB/out")"
colenv "FAKE_DF_AVAIL=20971520 PANEL_DISK_MIN_GB=10" --print
[ "$(jq -r '.sources.idle.value.disk.warn' "$SB/out")" = false ] && ok "경고 기준 PANEL_DISK_MIN_GB 로 변경" || ko "디스크 기준" ""
WS_BEFORE=$(iv '.workspaces | tojson'); IAT=$(q '.sources.idle.at'); sleep 1
colenv "FAKE_DF_MODE=fail FAKE_ORCA_FAIL=worktrees" --once
[ "$(q '.sources.idle.ok')" = false ] && [ "$(q '.sources.idle.stale')" = true ] && [ "$(q '.sources.idle.err')" = "workspaces:exit,disk:exit" ] && [ "$(iv '.workspaces | tojson')" = "$WS_BEFORE" ] && [ "$(iv '.disk.freeGB == 100')" = true ] && [ "$(q '.sources.idle.at')" = "$IAT" ] && [ "$(iv '.terminals.total')" = 12 ] && ok "부분 실패 → 그 부분만 이전 값 유지, ok:false·stale·err 에 부분:종류" || ko "idle 부분 실패" "$(q '.sources.idle|del(.value)')"
colenv "FAKE_DF_MODE=bad" --print
[ "$(jq -r '.sources.idle.err' "$SB/out")" = "disk:parse" ] && ok "df 출력 모양 이상 → disk:parse" || ko "df 파싱" "$(jq -r '.sources.idle.err' "$SB/out")"
colenv "PANEL_WS_DIR=$FHOME/ws" --print
[ ! -f "$FAKE/du.calls" ] && ok "--print 는 폴더 크기(du)를 재지 않음" || ko "--print du" ""
colenv "PANEL_WS_DIR=$FHOME/ws" --once
colenv "PANEL_WS_DIR=$FHOME/ws" --once
[ "$(wc -l < "$FAKE/du.calls" | tr -d ' ')" = 1 ] && [ "$(iv '.wsSize.gb == 2')" = true ] && [ "$(iv '.wsSize.dir')" = "~/ws" ] && [ "$(iv '.wsSize.err')" = null ] && ok "폴더 크기: 30분 간격으로 du 1회, 값 유지" || ko "du 간격" "calls=$(cat "$FAKE/du.calls" 2>/dev/null | wc -l)"
# du 가 시간 상한을 넘김: 자식까지 끝내고 직전 값 유지 + err:timeout, 간격 표식은 갱신(다음 측정은 30분 뒤)
WS_AT=$(iv '.wsSize.at'); touch -t 202601010000 "$H/.panel-du.stamp"; t0=$(date +%s)
colenv "PANEL_WS_DIR=$FHOME/ws PANEL_DU_TIMEOUT=1 FAKE_DU_SLEEP=20" --once
el=$(($(date +%s) - t0)); child=$(cat "$FAKE/du.child" 2>/dev/null || echo 0)
if [ "$child" != 0 ] && kill -0 "$child" 2>/dev/null; then alive=1; kill "$child" 2>/dev/null; else alive=0; fi
[ "$el" -lt 10 ] && [ "$alive" = 0 ] && [ "$(iv '.wsSize.err')" = timeout ] && [ "$(iv '.wsSize.gb == 2')" = true ] && [ "$(iv '.wsSize.at')" = "$WS_AT" ] \
  && [ "$(q '.sources.idle.ok')" = true ] && tail -1 "$LOG" | grep -q 'du=timeout' \
  && ok "du 시간 초과 → ${el}초 안에 자식까지 정리, 직전 값 유지 + err:timeout, 로그 du=timeout" || ko "du 시간 초과" "${el}초 자식생존=$alive $(iv '.wsSize|tojson')"
n_du=$(wc -l < "$FAKE/du.calls" | tr -d ' ')
colenv "PANEL_WS_DIR=$FHOME/ws PANEL_DU_TIMEOUT=1 FAKE_DU_SLEEP=20" --once
[ "$(wc -l < "$FAKE/du.calls" | tr -d ' ')" = "$n_du" ] && [ "$(iv '.wsSize.err')" = timeout ] && ! tail -1 "$LOG" | grep -q 'du=' \
  && ok "시간 초과 뒤에도 30분 간격 유지(바로 다시 재지 않음), 표시는 남고 로그는 잰 회차만" || ko "du 시간 초과 뒤 간격" "calls=$(wc -l < "$FAKE/du.calls")"
colenv "PANEL_DU_TIMEOUT=0" --once; [ "$RC" = 2 ] && ok "PANEL_DU_TIMEOUT 범위 밖 → 종료 2" || ko "du 상한 범위" "rc=$RC"
# 정리는 사람이 한다: 수집기에 삭제·종료 명령이 들어가면 실패(문자열 + 구문 트리 + 실제로 부른 명령)
DEL_RE='"rm"|\brm -|worktree.{0,4}(rm|remove|delete|prune)|terminal.{0,4}close|worker-stop|orchestration.{0,4}stop|rmtree|branch.{0,4}-[dD]\b|"(close|stop|kill|remove|unlink|prune)"'
if grep -nE "$DEL_RE" "$SCRIPTS/panel_collect.py" "$SCRIPTS/panel_collect.sh" >/dev/null; then
  ko "삭제 문자열" "$(grep -nE "$DEL_RE" "$SCRIPTS/panel_collect.py" "$SCRIPTS/panel_collect.sh" | head -2 | cut -c1-80)"
else
  ok "수집기에 rm·worktree rm·terminal close·worker-stop·branch -D 같은 삭제·종료 문자열 없음"
fi
r=$(python3 -I "$HERE/no_delete_check.py" "$SCRIPTS/panel_collect.py" 2>&1)
[ "$r" = ok ] && ok "구문 트리: os.remove·os.kill·unlink 등은 임시 파일·잠금·로그 회전·시간 초과 자식 정리 자리에만" || ko "삭제 호출" "$r"
bad_calls=$( { sed 's/ tok=.*//' "$FAKE/orca.calls" | grep -vE '^(terminal list|orchestration (worker-list|task-list)|worktree list)( |$)'
  grep -vE '^merge-base --is-ancestor HEAD ' "$FAKE/git.calls" 2>/dev/null
  grep -vE '^pr list ' "$FAKE/gh.calls" 2>/dev/null
  grep -vE '^-sk ' "$FAKE/du.calls" 2>/dev/null
  grep -vE '^-k <path>$' "$FAKE/df.calls" 2>/dev/null; } | head -3)
[ -z "$bad_calls" ] && [ -s "$FAKE/git.calls" ] && [ -s "$FAKE/df.calls" ] && ok "이번 시험에서 부른 orca·git·gh·du·df 명령은 읽기 명령뿐" || ko "부른 명령" "$(echo "$bad_calls" | cut -c1-60 | tr '\n' ' ')"

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
