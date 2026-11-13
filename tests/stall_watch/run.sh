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
# 게이트: 열리는 파일이 생길 때까지(최대 10초) 전송을 붙잡아 잠금 보유 상태를 시험이 원하는 만큼 유지
if [ -n "${FAKE_NOTIFY_GATE:-}" ]; then for _ in $(seq 200); do [ -e "$FAKE_NOTIFY_GATE" ] && break; sleep 0.05; done; fi
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
def term(h, title, wt, agent="claude", conn=True, out=0):
    return {"handle": h, "title": title, "worktreePath": wt, "agentIdentity": agent, "connected": conn, "writable": True, "orphaned": False, "lastOutputAt": out}
terms = [term("term_a1", "알파 코디", "/x/coord-alpha-hub"), term("term_b1", "서버 허브 이어받기", "/x/coord-beta-hub", out=1000), term("term_b2", "서버 허브 복원", "/x/coord-beta-hub", out=2000),
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
chk "코디네이터 2개인 베타: 마지막 출력이 최근인 term_b2 에만 전송 + 중복 사실은 메인에" 'grep -q "^term_b2	.*베타 구현" "$FAKE_DIR/sends.log" && ! grep -q "^term_b1" "$FAKE_DIR/sends.log" && grep -q "^$MAINH	.*중복 코디네이터: beta (서버 허브 이어받기 / 서버 허브 복원)" "$FAKE_DIR/sends.log"'
chk "선택 근거 한 줄이 결과 문구에 있음, 종료 0" 'grep -q "beta: 코디네이터 2개 중 마지막 출력이 가장 최근인 1개에 전송" "$SB/o"'
chk "전송 총 3건뿐, 워커·터미널 종료·재시작 명령 없음" '[ "$(wc -l < "$FAKE_DIR/sends.log" | tr -d " ")" = 3 ] && ! grep -Eq "worker-(stop|release|start)|terminal (close|stop|kill)" "$FAKE_DIR/calls.log"'

# ---- 6. 승인 대기(확인 창에서 5분 이상 멈춤) ----------------------------------------------------------
# addappr 분 디스패치 작업 허브: 확인 창이 뜬 진행 워커·작업·터미널·화면 줄을 픽스처에 추가
addappr() {
python3 - "$@" <<'PY'
import json, os, sys, time
d = os.environ["FAKE_DIR"]; mins, disp, task, hub = float(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
def rd(n): return json.load(open(os.path.join(d, n + ".json")))
def wr(n, v): json.dump(v, open(os.path.join(d, n + ".json"), "w"), ensure_ascii=False)
now_ms = int(time.time() * 1000)
a = rd("workers_active")
a.append({"dispatchId": disp, "taskId": task, "runId": "run_1", "workerState": "ready", "terminalState": "active", "agentTerminalHandle": "term_" + disp,
          "projection": {"evidence": {"lastObservedAt": now_ms - 60000}}})
wr("workers_active", a)
t = rd("tasks"); t["run_1"].append({"id": task, "status": "dispatched", "title": "승인 질문 작업 " + task, "created_by_process_incarnation": "wt::/x/coord-%s-hub@@a:b" % hub}); wr("tasks", t)
ts = rd("terminals"); ts.append({"handle": "term_" + disp, "title": "worker", "worktreePath": "/x/ws", "agentIdentity": "claude", "connected": True,
                                "lastOutputAt": now_ms - int(mins * 60000), "preview": "Do you want to proceed?\n❯ 1. Yes\n  2. No"}); wr("terminals", ts)
secret = "xox" + "b-123456789012-abcdefABCDEF"
wr("read_" + disp, ["─" * 40, "Bash command", "  rm -rf ./build && curl -H 'Authorization: Bearer %s' x" % secret, "  token: " + secret, "Do you want to proceed?", "❯ 1. Yes", "  2. No", "─" * 40])
PY
}
BEFORE=$(cards)
addappr 4 ctx_w8 T8 alpha
sw --json > "$SB/o"
chk "승인 대기 4분: 카드 없음(기준 5분 미만)" '[ "$(cards)" = "$BEFORE" ] && grep -q none "$SB/o"'
addappr 6 ctx_w7 T7 alpha
sw --json > "$SB/o"
chk "승인 대기 6분: 새 카드 1장" '[ "$(cards)" = "$((BEFORE + 1))" ] && grep -q "\"count\": *1\|\"count\":1" "$SB/o"'
LAST=$(python3 -c "import sys;print([c for c in open(sys.argv[1],encoding='utf-8').read().split('---\n') if c.strip()][-1])" "$FAKE_NOTIFY_LOG")
chk "카드 줄: 허브·작업·'승인 대기 6분째(확인 창은 사용자만 누름)'" 'printf "%s" "$LAST" | grep -q "• 알파 허브 · 승인 질문 작업 T7 — 승인 대기 6분째(확인 창은 사용자만 누름)"'
chk "카드: 화면 요약에 질문·명령 포함, 구분선 제외" 'printf "%s" "$LAST" | grep -q "화면: .*Bash command.*rm -rf ./build.*Do you want to proceed?" && ! printf "%s" "$LAST" | grep -q "────"'
chk "카드: 비밀 값 가림(토큰 원문 없음)" 'printf "%s" "$LAST" | grep -q "\[가림\]" && ! printf "%s" "$LAST" | grep -q "123456789012-abcdef"'
chk "카드: 4분짜리(T8)는 포함되지 않음" '! printf "%s" "$LAST" | grep -q "T8"'
DID=$(printf "%s" "$LAST" | grep -Eo "D-ST[0-9a-f]{10}" | head -1)
: > "$FAKE_DIR/sends.log"; : > "$FAKE_DIR/calls.log"
python3 -I "$CA" handle --did "$DID" --pick 1 > "$SB/o"
chk "승인 대기 알리기: 허브 코디네이터에 한 줄만, 확인 창에는 입력하지 않음" '[ "$(wc -l < "$FAKE_DIR/sends.log" | tr -d " ")" = 1 ] && grep -q "^term_a1	.*승인 대기 6분째" "$FAKE_DIR/sends.log" && ! grep -q "term_ctx_w7" "$FAKE_DIR/sends.log"'

# ---- 7. 동시 실행: 프로세스 간 잠금 ---------------------------------------------------------------------
python3 - <<'PY'
import json, os
d = os.environ["FAKE_DIR"]
def rd(n): return json.load(open(os.path.join(d, n + ".json")))
def wr(n, v): json.dump(v, open(os.path.join(d, n + ".json"), "w"), ensure_ascii=False)
r = rd("workers_retained"); r.append({"dispatchId": "ctx_c1", "taskId": "T9", "runId": "run_1", "workerState": "stopped", "terminalState": "retained", "projection": {"evidence": {"lastObservedAt": None}}}); wr("workers_retained", r)
t = rd("tasks"); t["run_1"].append({"id": "T9", "status": "blocked", "title": "동시 시험 작업", "created_by_process_incarnation": "wt::/x/coord-alpha-hub@@a:b"}); wr("tasks", t)
PY
C0=$(cards)
GATE="$SB/gate"; rm -f "$GATE"
FAKE_NOTIFY_GATE="$GATE" sw --json > "$SB/p1" 2>&1 &
FAKE_NOTIFY_GATE="$GATE" sw --json > "$SB/p2" 2>&1 &
# 한쪽이 busy 로 즉시 끝날 때까지 기다린 뒤(= 다른 쪽이 잠금을 쥔 채 전송 게이트에서 대기 중) 게이트를 엶. 어느 쪽이 먼저 잡든 같은 판정
for _ in $(seq 200); do grep -qs busy "$SB/p1" "$SB/p2" && break; sleep 0.05; done
BUSY_EARLY=$(grep -ls busy "$SB/p1" "$SB/p2" | wc -l | tr -d ' ')
: > "$GATE"; wait
chk "동시 check 2개: 카드 1장만 전송" '[ "$(cards)" = "$((C0 + 1))" ]' "$(( $(cards) - C0 ))장"
chk "하나는 carded·하나는 busy(순서 무관), busy 는 전송이 끝나기 전에 이미 반환(대기 없음)" '[ "$(cat "$SB/p1" "$SB/p2" | grep -c "\"carded\"")" = 1 ] && [ "$(cat "$SB/p1" "$SB/p2" | grep -c busy)" = 1 ] && [ "$BUSY_EARLY" = 1 ]'
sw --json > "$SB/p3"; chk "잠금 해제 뒤: 같은 dispatch 6시간 중복 방지 유지(카드 추가 없음)" '[ "$(cards)" = "$((C0 + 1))" ] && grep -q none "$SB/p3"'

# ---- 8. 화면 요약 비밀 가림: JSON·key=value·Bearer·xox 토큰 ------------------------------------------------
addappr 6 ctx_w9 T10 alpha
python3 - <<'PY'
import json, os
xox = "xox" + "b-111122223333-zzzzYYYYxxxx"
lines = ['{"password": "example123!", "user": "kim"}', "api_key=plainvalue99 next", "Authorization: Bearer abcdefgh12345678", "export SLACK=" + xox, "Do you want to proceed?"]
json.dump(lines, open(os.path.join(os.environ["FAKE_DIR"], "read_ctx_w9.json"), "w"), ensure_ascii=False)
PY
sw --json > "$SB/o"
CARD=$(python3 -c "import sys;print([c for c in open(sys.argv[1],encoding='utf-8').read().split('---\n') if c.strip()][-1])" "$FAKE_NOTIFY_LOG")
chk "비밀 가림 시험용 카드 전송됨(화면 줄 포함)" 'printf "%s" "$CARD" | grep -q "화면: .*Do you want to proceed?"'
chk "JSON 비밀 필드: password 값 전체 가림(키·다른 필드는 유지)" '! printf "%s" "$CARD" | grep -q "example123" && printf "%s" "$CARD" | grep -q "password.*\[가림\]" && printf "%s" "$CARD" | grep -q "user"'
chk "key=value: api_key 값 가림" '! printf "%s" "$CARD" | grep -q "plainvalue99" && printf "%s" "$CARD" | grep -q "api_key=\[가림\]"'
chk "Authorization Bearer 값 가림" '! printf "%s" "$CARD" | grep -q "abcdefgh12345678"'
chk "xox 토큰 가림" '! printf "%s" "$CARD" | grep -q "111122223333" && ! printf "%s" "$CARD" | grep -q "zzzzYYYY"'

# ---- 9. 여러 줄로 나뉜 비밀(키 줄과 값 줄 분리) ----------------------------------------------------------
addappr 6 ctx_w10 T11 alpha
python3 - <<'PY'
import json, os
lines = ['{"password":', '  "example123!",', '"user": "kim"}', "Authorization:", "", "  Bearer zzzz9999yyyy8888", "token =", "plainsplit77", "Do you want to proceed?"]
json.dump(lines, open(os.path.join(os.environ["FAKE_DIR"], "read_ctx_w10.json"), "w"), ensure_ascii=False)
PY
sw --json > "$SB/o"
CARD=$(python3 -c "import sys;print([c for c in open(sys.argv[1],encoding='utf-8').read().split('---\n') if c.strip()][-1])" "$FAKE_NOTIFY_LOG")
chk "여러 줄 시험용 카드 전송됨(화면 줄 포함)" 'printf "%s" "$CARD" | grep -q "화면: .*Do you want to proceed?"'
chk "여러 줄 JSON: 다음 줄의 password 값 가림" '! printf "%s" "$CARD" | grep -q "example123"'
chk "빈 줄을 사이에 둔 Authorization: / Bearer 값 가림" '! printf "%s" "$CARD" | grep -q "zzzz9999yyyy8888"'
chk "token = 다음 줄 값 가림" '! printf "%s" "$CARD" | grep -q "plainsplit77"'

# ---- 10. 다중 터미널 선택·orca 경로 -------------------------------------------------------------------
python3 - <<'PY'
import json, os
d = os.environ["FAKE_DIR"]
def t(h, out, **kw):
    r = {"handle": h, "title": "c", "worktreePath": "/x/coord-gamma-hub", "agentIdentity": "claude", "connected": True, "writable": True, "orphaned": False, "lastOutputAt": out}
    r.update(kw); return r
json.dump([t("term_g1", 100), t("term_g2", 900, orphaned=True), t("term_g3", 800, writable=False), t("term_g4", 700, connected=False), t("term_g5", 300), t("term_g6", 500, agentIdentity="codex")],
          open(os.path.join(d, "terminals.json"), "w"))
PY
: > "$FAKE_DIR/sends.log"
python3 - <<PY > "$SB/o"
import sys; sys.path.insert(0, "$SCRIPTS")
import card_actions as c
print(c.run_notify({"hubs": [{"slug": "gamma", "lines": ["x"]}]}))
PY
chk "다중 터미널: 고아·쓰기 불가·끊김·codex 제외 뒤 가장 최근 출력(term_g5)에 전송" '[ "$(wc -l < "$FAKE_DIR/sends.log" | tr -d " ")" = 1 ] && grep -q "^term_g5	" "$FAKE_DIR/sends.log"'
python3 -c 'import json,os;json.dump([],open(os.path.join(os.environ["FAKE_DIR"],"terminals.json"),"w"))'
: > "$FAKE_DIR/sends.log"
python3 - <<PY > "$SB/o"
import sys; sys.path.insert(0, "$SCRIPTS")
import card_actions as c
print(c.run_notify({"hubs": [{"slug": "gamma", "lines": ["x"]}]}))
PY
chk "후보 0개: '코디네이터 터미널 없음'" 'grep -q "gamma: 코디네이터 터미널 없음" "$SB/o" && [ ! -s "$FAKE_DIR/sends.log" ]'
# launchd 최소 PATH(env -i): orca 경로를 bare 'orca' 로 두면 rc 127 → '없음' 오판. 후보 경로 탐색 결과와 같아야 함
PYBIN=$(command -v python3)
MINI=$(env -i PATH=/usr/bin:/bin "$PYBIN" -I -c "
import sys; sys.path.insert(0, '$SCRIPTS')
import card_actions as c, main_terminal as mt
print(c.ORCA == mt.find_orca(), c.ORCA)")
chk "env -i 최소 PATH: card_actions.ORCA 가 find_orca() 와 같음" 'printf "%s" "$MINI" | grep -q "^True"'
chk "find_orca: PATH 에 없어도 후보 경로에서 찾음(bare 'orca' 아님)" '[ "$(env -i "$PYBIN" -I -c "
import sys; sys.path.insert(0, \"$SCRIPTS\")
import main_terminal as mt
print(mt.find_orca(env={}, path=\"/usr/bin:/bin\", candidates=(\"$BIN/orca\",)))")" = "$BIN/orca" ]'
chk "bare orca 하드코딩 없음(hubs/scripts 의 orca 실행 경로는 find_orca 경유)" '! grep -nE "(environ\.get\(\"[A-Z_]*ORCA\"(, \"orca\"\)|\) or \"orca\"))" "$SCRIPTS"/*.py | grep -v panel_collect'

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
