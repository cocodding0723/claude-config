#!/usr/bin/env bash
# stall_watch.py·card_actions.py(notify) 검증. orca·slack_notify.sh 는 가짜(fake_orca.py)이며 실제 Orca·Slack·터미널은 쓰지 않음.
# 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함. SW_SCRIPTS=<폴더> 로 변이 검증 가능.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS=${SW_SCRIPTS:-$HERE/../../hubs/scripts}
SW="$SCRIPTS/stall_watch.py" CA="$SCRIPTS/card_actions.py"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-stallwatch.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
BIN="$SB/bin"; mkdir -p "$BIN"
cp "$HERE/fake_orca.py" "$BIN/orca"; chmod +x "$BIN/orca"
cat > "$BIN/notify" <<'SH'
#!/bin/bash
[ "${FAKE_NOTIFY_FAIL:-}" = 1 ] && exit 1
{ printf '%s\n' "$@"; echo '---'; } >> "$FAKE_NOTIFY_LOG"
SH
chmod +x "$BIN/notify"
export FAKE_DIR="$SB/fake" LRM_HUB_DIR="$SB/hub" LRM_ORCA="$BIN/orca" LRM_NOTIFY="$BIN/notify" HUBS_TSV="$SB/hubs.tsv" FAKE_NOTIFY_LOG="$SB/notify.log" LRM_MAIN_ENV="$SB/hub/main.env"
mkdir -p "$FAKE_DIR" "$LRM_HUB_DIR"; : > "$FAKE_NOTIFY_LOG"
printf 'alpha\t알파 허브\t-\t-\t-\nbeta\t베타 허브\t-\t-\t-\n' > "$HUBS_TSV"
MAINH=term_0000aaaa-0000-4000-8000-000000000001
printf 'MAIN_TERMINAL=%s\n' "$MAINH" > "$LRM_HUB_DIR/main.env"

# 픽스처 생성: python 으로 JSON 작성. now 기준 분 단위 관찰 시각
mkfix() { # $1=w3 관찰 몇 분 전, $2=w6(새로 멈춘 워커) 포함 여부 0|1
python3 - "$1" "$2" <<'PY'
import json, os, sys, time
d = os.environ["FAKE_DIR"]; mins = float(sys.argv[1]); with_w6 = sys.argv[2] == "1"
now_ms = int(time.time() * 1000)
def w(disp, task, state, term, seen_min, run="run_1"):
    ev = {"lastObservedAt": None if seen_min is None else now_ms - int(seen_min * 60000)}
    return {"dispatchId": disp, "taskId": task, "runId": run, "workerState": state, "terminalState": term, "projection": {"evidence": ev}}
retained = [w("ctx_1", "T1", "stopped", "retained", None), w("ctx_2", "T2", "stopped", "retained", None)]
if with_w6:
    retained.append(w("ctx_6", "T6", "stopped", "retained", None))
active = [w("ctx_3", "T3", "ready", "active", mins), w("ctx_4", "T4", "ready", "active", 5), w("ctx_5", "T5", "ready", "active", None)]
def t(i, status, title, hub):
    return {"id": i, "status": status, "title": title, "created_by_process_incarnation": "wt::/x/coord-%s-hub@@abc:def" % hub if hub else "wt::/x/other@@a:b"}
tasks = {"run_1": [t("T1", "blocked", "알파 옛 작업 LRM-1", "alpha"), t("T2", "completed", "끝난 작업", "alpha"), t("T3", "dispatched", "베타 구현 LRM-3", "beta"),
                   t("T4", "dispatched", "최근 관찰 작업", "beta"), t("T5", "dispatched", "관찰 없음 작업", "beta"), t("T6", "blocked", "알파 새 작업 <@U1>", "alpha")]}
def term(h, title, wt, agent="claude", conn=True):
    return {"handle": h, "title": title, "worktreePath": wt, "agentIdentity": agent, "connected": conn}
terms = [term("term_a1", "알파 코디", "/x/coord-alpha-hub"), term("term_b1", "서버 허브 이어받기", "/x/coord-beta-hub"), term("term_b2", "서버 허브 복원", "/x/coord-beta-hub"),
         term("term_b3", "베타 죽은 창", "/x/coord-beta-hub", conn=False)]
for name, val in (("workers_retained", retained), ("workers_active", active), ("tasks", tasks), ("terminals", terms)):
    json.dump(val, open(os.path.join(d, name + ".json"), "w"), ensure_ascii=False)
PY
}
sw() { python3 -I "$SW" check "$@"; }
cards() { grep -c '^---$' "$FAKE_NOTIFY_LOG" || true; }
STATE="$LRM_HUB_DIR/stall-watch.json"

# ---- 1. 첫 실행: 기준선만 ---------------------------------------------------------------------
mkfix 5 0
sw --json > "$SB/o"
chk "첫 실행: 옛 멈춘 워커는 기준선만 기록(카드 없음)" '[ "$(cards)" = 0 ] && grep -q baseline "$SB/o" && grep -q ctx_1 "$STATE"'
chk "조회는 읽기 전용(worker-list·task-list·terminal list 만)" '! grep -Ev "^(orchestration (worker-list|task-list)|terminal list)" "$FAKE_DIR/calls.log" | grep -q .'
sw --json > "$SB/o"; chk "이어서 변화 없음: 감지 없음" '[ "$(cards)" = 0 ] && grep -q none "$SB/o"'

# ---- 2. 새로 멈춤·응답 없음: 허브별 묶음 카드 1장 ---------------------------------------------------
mkfix 45 1
sw --json > "$SB/o"
chk "묶음 카드 1장(w3 45분 무응답 + w6 새로 멈춤)" '[ "$(cards)" = 1 ] && grep -q "\"count\": *2\|\"count\":2" "$SB/o"'
chk "결정 카드·선택지 담당 허브에 알리기/무시" 'grep -qx decide "$FAKE_NOTIFY_LOG" && grep -qx "1=담당 허브에 알리기,2=무시" "$FAKE_NOTIFY_LOG" && grep -Eqx "D-ST[0-9a-f]{10}" "$FAKE_NOTIFY_LOG"'
chk "허브별 한 줄: 허브·작업 제목·상태(분)" 'grep -q "• 베타 허브 · 베타 구현 LRM-3 — 4[0-9]분째 응답 없음" "$FAKE_NOTIFY_LOG" && grep -q "• 알파 허브 · 알파 새 작업 @U1 — 종료 보고 없이 멈춤" "$FAKE_NOTIFY_LOG"'
chk "기준선 워커(ctx_1)·끝난 작업(ctx_2)·최근 관찰(ctx_4)·관찰 없음(ctx_5)은 제외" '! grep -q "알파 옛 작업\|끝난 작업\|최근 관찰\|관찰 없음" "$FAKE_NOTIFY_LOG"'
chk "문구는 '응답 없음', '죽음' 표현 없음·Slack 멘션 문자 제거" 'grep -q "응답 없음" "$FAKE_NOTIFY_LOG" && ! grep -q "죽" "$FAKE_NOTIFY_LOG" && ! grep -q "<@U1>" "$FAKE_NOTIFY_LOG"'
chk "중복 코디네이터 줄: 베타 허브 · 제목 2개(연결 끊긴 창은 세지 않음)" 'grep -q "• 중복 코디네이터: 베타 허브 · 서버 허브 이어받기 / 서버 허브 복원 (2개)" "$FAKE_NOTIFY_LOG" && ! grep -q "베타 죽은 창" "$FAKE_NOTIFY_LOG"'
chk "알파는 코디네이터 1개라 중복 줄 없음" '! grep -q "중복 코디네이터: 알파" "$FAKE_NOTIFY_LOG"'

# ---- 3. 6시간 중복 방지 -------------------------------------------------------------------------
sw --json > "$SB/o"; chk "바로 다시: 같은 dispatch 재알림 없음" '[ "$(cards)" = 1 ] && grep -q none "$SB/o"'
python3 - "$STATE" 5 <<'PY'
import json, sys, time
p = sys.argv[1]; d = json.load(open(p)); h = float(sys.argv[2])
for k, v in d["seen"].items():
    if not v.get("baseline"): v["at"] = time.time() - h * 3600
json.dump(d, open(p, "w"))
PY
sw --json > "$SB/o"; chk "5시간 뒤: 재알림 없음" '[ "$(cards)" = 1 ]'
python3 - "$STATE" 7 <<'PY'
import json, sys, time
p = sys.argv[1]; d = json.load(open(p)); h = float(sys.argv[2])
for k, v in d["seen"].items():
    if not v.get("baseline"): v["at"] = time.time() - h * 3600
json.dump(d, open(p, "w"))
PY
sw --json > "$SB/o"; chk "7시간 뒤: 다시 알림(총 2장), 기준선 워커는 여전히 제외" '[ "$(cards)" = 2 ] && ! tail -20 "$FAKE_NOTIFY_LOG" | grep -q "알파 옛 작업"'

# ---- 4. 조회 실패·전송 실패 ---------------------------------------------------------------------
FAKE_ORCA_FAIL=1 sw --json > "$SB/o" 2>"$SB/e"; rc=$?
chk "조회 실패: 종료 1·카드 없음·상태 유지" '[ $rc = 1 ] && grep -q unavailable "$SB/o" && [ "$(cards)" = 2 ]'
python3 - "$STATE" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p)); d["seen"] = {k: v for k, v in d["seen"].items() if v.get("baseline")}; json.dump(d, open(p, "w"))
PY
PENDING=$(python3 -I "$CA" list | wc -l | tr -d ' ')
FAKE_NOTIFY_FAIL=1 sw --json > "$SB/o" 2>"$SB/e"; rc=$?
chk "전송 실패: 종료 1·등록 철회·dispatch 미기록(다음 주기 재시도)" '[ $rc = 1 ] && [ "$(python3 -I "$CA" list | wc -l | tr -d " ")" = "$PENDING" ] && ! grep -q ctx_3 "$STATE"'
sw --json > "$SB/o"; chk "재시도 성공(총 3장)" '[ "$(cards)" = 3 ]'

# ---- 5. 버튼: 🅰️ 알림만 / 🅱️ 무동작 --------------------------------------------------------------
DID=$(grep -Eo "D-ST[0-9a-f]{10}" "$FAKE_NOTIFY_LOG" | tail -1)
: > "$FAKE_DIR/sends.log"; : > "$FAKE_DIR/calls.log"
python3 -I "$CA" handle --did "$DID" --pick 2 > "$SB/o"
chk "무시(2번): 전송·orca 호출 없음" '[ ! -s "$FAKE_DIR/sends.log" ] && [ ! -s "$FAKE_DIR/calls.log" ]'
python3 -I "$CA" handle --did "$DID" --pick 1 > "$SB/o"; chk "무시로 지워진 카드는 다시 눌러도 동작 없음" '[ ! -s "$FAKE_DIR/sends.log" ]'
python3 - <<PY
import sys; sys.path.insert(0, "$SCRIPTS")
import card_actions as c
c.register("D-STTEST000001", "notify", {"hubs": [{"slug": "alpha", "lines": ["알파 새 작업 — 종료 보고 없이 멈춤"]}, {"slug": "beta", "lines": ["베타 구현 — 45분째 응답 없음"]}],
                                        "dups": [{"slug": "beta", "titles": ["서버 허브 이어받기", "서버 허브 복원"]}]})
PY
python3 -I "$CA" handle --did D-STTEST000001 --pick 1 > "$SB/o"
chk "알리기(1번): 알파 코디네이터(1개)에만 한 줄 전송" 'grep -c "^term_a1	" "$FAKE_DIR/sends.log" | grep -qx 1 && grep -q "^term_a1	\[멈춘 워커 감시\] 응답 없는 워커: 알파 새 작업" "$FAKE_DIR/sends.log"'
chk "코디네이터 2개인 베타는 허브에 보내지 않고 메인에 중복 사실만" '! grep -q "^term_b" "$FAKE_DIR/sends.log" && grep -q "^$MAINH	.*중복 코디네이터: beta (서버 허브 이어받기 / 서버 허브 복원)" "$FAKE_DIR/sends.log"'
chk "전송 총 2건뿐, 워커·터미널 종료·재시작 명령 없음" '[ "$(wc -l < "$FAKE_DIR/sends.log" | tr -d " ")" = 2 ] && ! grep -Eq "worker-(stop|release|start)|terminal (close|stop|kill)" "$FAKE_DIR/calls.log"'

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
