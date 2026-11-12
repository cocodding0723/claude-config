#!/usr/bin/env bash
# session_gc.py 검증. orca 는 가짜(fake_orca.py), git 작업 공간은 임시 저장소. 실제 Orca·터미널·Slack 은 쓰지 않음.
# 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함. SG_SCRIPTS=<폴더> 로 변이 검증 가능.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS=${SG_SCRIPTS:-$HERE/../../hubs/scripts}
SG="$SCRIPTS/session_gc.py"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-sessiongc.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
BIN="$SB/bin"; mkdir -p "$BIN"
cp "$HERE/fake_orca.py" "$BIN/orca"; chmod +x "$BIN/orca"
export FAKE_DIR="$SB/fake" LRM_HUB_DIR="$SB/hub" LRM_ORCA="$BIN/orca" HUBS_TSV="$SB/hubs.tsv" LRM_MAIN_ENV="$SB/hub/main.env" LRM_GC_LOG="$SB/hub/gc.log"
mkdir -p "$FAKE_DIR" "$LRM_HUB_DIR"
printf 'alpha\t알파 허브\t-\t-\t-\n' > "$HUBS_TSV"
printf 'MAIN_TERMINAL=term_0000aaaa-0000-4000-8000-000000000001\n' > "$LRM_HUB_DIR/main.env"

# 작업 공간: 깨끗한 저장소·미커밋 있는 저장소·git 아님
mkrepo() { git init -q "$1" && git -C "$1" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init; }
mkrepo "$SB/clean1"; mkrepo "$SB/clean4"; mkrepo "$SB/clean5"; mkrepo "$SB/clean6"; mkrepo "$SB/dirty"; mkrepo "$SB/busy"
echo x > "$SB/dirty/new.txt"; mkdir -p "$SB/nogit"

mkfix() {
python3 - "$SB" <<'PY'
import json, os, sys, time
sb = sys.argv[1]; d = os.environ["FAKE_DIR"]; now = int(time.time() * 1000)
IDLE = "작업 끝\n❯ "
APPR = "Do you want to proceed?\n❯ 1. Yes\n  2. No"
def term(h, title, wt, mins, preview=IDLE, agent="claude"):
    return {"handle": h, "title": title, "worktreePath": wt, "agentIdentity": agent, "connected": True, "lastOutputAt": now - int(mins * 60000), "preview": preview}
def worker(disp, h, owned=True, archive=True, state="succeeded", dstat="completed", tstate="retained", reason=None):
    res = {"ownershipState": "owned", "releaseState": "not_requested", "retainedReason": reason, "archive": {"source": "transcript", "status": "captured"} if archive else None} if owned else None
    return {"dispatchId": disp, "agentTerminalHandle": h, "workerState": state, "dispatchStatus": dstat, "terminalState": tstate, "resource": res}
terms = [term("term_ca", "알파 새 코디", "/x/coord-alpha-hub", 1), term("term_cb", "알파 옛 코디", "/x/coord-alpha-hub", 90),
         term("term_w1", "끝난 1", sb + "/clean1", 45), term("term_w2", "미커밋", sb + "/dirty", 45), term("term_w3", "승인 대기", sb + "/clean1", 60, APPR),
         term("term_w4", "끝난 채택 창", sb + "/clean4", 50), term("term_w5", "방금 끝남", sb + "/clean5", 5), term("term_w6", "기록 없음", sb + "/clean6", 50),
         term("term_w7", "git 아님", sb + "/nogit", 50), term("term_w8", "사용자 소유", sb + "/clean1", 50), term("term_w9", "진행 워커와 공유", sb + "/busy", 50)]
retained = [worker("ctx_1", "term_w1"), worker("ctx_2", "term_w2"), worker("ctx_3", "term_w3"), worker("ctx_4", "term_w4", owned=False), worker("ctx_5", "term_w5"),
            worker("ctx_6", "term_w6", owned=False), worker("ctx_7", "term_w7"), worker("ctx_8", "term_w8", reason="user_takeover"), worker("ctx_9", "term_w9"),
            worker("ctx_f", "term_w1", state="failed", dstat="failed")]
active = [worker("ctx_a", "term_w9", state="ready", dstat="dispatched", tstate="active")]
for name, val in (("terminals", terms), ("workers_retained", retained), ("workers_active", active)):
    json.dump(val, open(os.path.join(d, name + ".json"), "w"), ensure_ascii=False)
for disp in ("ctx_4", "ctx_7"):  # 대화 기록이 읽히는 워커(ctx_1~3 등은 resource.archive 로 확인)
    json.dump([1], open(os.path.join(d, "transcript_%s.json" % disp), "w"))
PY
}
mutating() { grep -E '^(terminal send|terminal close|orchestration worker-release)' "$FAKE_DIR/calls.log" 2>/dev/null; }
sg() { python3 -I "$SG" "$@"; }
mkfix

# ---- 1. 시험 모드 ----------------------------------------------------------------------------
: > "$FAKE_DIR/calls.log"
sg --json > "$SB/o1"
chk "시험 모드: 중복 코디네이터 — 최근 출력 쪽 유지, 옛 창이 종료 요청 대상" 'python3 -c "
import json; d=json.load(open(\"$SB/o1\")); i=[x for x in d[\"items\"] if x[\"kind\"]==\"dup_coord\"]
assert len(i)==1 and i[0][\"handle\"]==\"term_cb\" and i[0][\"keep\"]==\"알파 새 코디\", i"'
chk "시험 모드: 끝난 워커 close 대상(ctx_1 release, ctx_4 채택 창은 terminal close, ctx_7 기록 읽힘이지만 git 아님은 제외)" 'python3 -c "
import json; d=json.load(open(\"$SB/o1\")); m={x[\"dispatch\"]:x[\"method\"] for x in d[\"items\"] if x[\"kind\"]==\"done_worker\"}
assert m=={\"ctx_1\":\"worker-release\",\"ctx_4\":\"terminal-close\"}, m"'
skipwhy() { python3 -c "
import json; d=json.load(open('$SB/o1')); print([s['reason'] for s in d['skipped'] if s['target']=='$1'][0])"; }
chk "미커밋 있는 워커 제외" '[[ "$(skipwhy ctx_2)" == *미커밋* ]]'
chk "승인 대기 워커 제외(카드 경로 담당)" '[[ "$(skipwhy ctx_3)" == *승인\ 대기* ]]'
chk "idle 기준 미달·기록 없음·git 조회 실패·사용자 소유·진행 워커 공유 모두 제외" '[[ "$(skipwhy ctx_5)" == idle* && "$(skipwhy ctx_6)" == *보관* && "$(skipwhy ctx_7)" == *git* && "$(skipwhy ctx_8)" == *사용자* && "$(skipwhy ctx_9)" == *진행\ 워커* ]]'
chk "실패 워커(ctx_f)는 후보·제외 목록 모두에 없음(끝난 워커만 판정)" '! grep -q ctx_f "$SB/o1"'
chk "시험 모드에서 orca 변경 명령 0회" '[ -z "$(mutating)" ]'
chk "조회는 읽기 전용(worker-list·terminal list·worker-read 만)" '! grep -Ev "^(orchestration (worker-list|worker-read)|terminal list)" "$FAKE_DIR/calls.log" | grep -q .'
chk "로그 1줄 이상(시험 모드 표시·후보 수)" 'grep -q "dry-run listed 후보 3건" "$LRM_GC_LOG"'
chk "시험 모드는 상태 파일을 만들지 않음" '[ ! -e "$LRM_HUB_DIR/session-gc.json" ]'

# ---- 2. 첫 --apply 는 목록만 ------------------------------------------------------------------
: > "$FAKE_DIR/calls.log"
sg --apply --json > "$SB/o2"
chk "첫 --apply: 목록만(orca 변경 0회)·상태 기록" '[ -z "$(mutating)" ] && grep -q first_apply_listing "$SB/o2" && [ -e "$LRM_HUB_DIR/session-gc.json" ]'

# ---- 3. 둘째 --apply 부터 동작 ----------------------------------------------------------------
: > "$FAKE_DIR/calls.log"
sg --apply --json > "$SB/o3"
chk "--apply: 종료 요청 1회(옛 코디네이터 창, HANDOFF 문구)" '[ "$(grep -c "^terminal send" "$FAKE_DIR/calls.log")" = 1 ] && grep -q "^terminal send --terminal term_cb --text .*HANDOFF.* --enter" "$FAKE_DIR/calls.log"'
chk "--apply: 코디네이터 창은 닫지 않음(close 대상은 워커 창뿐)" '! grep -q "term_cb\|term_ca" <(grep "^terminal close" "$FAKE_DIR/calls.log")'
chk "--apply: 소유 워커는 worker-release, 채택 창은 terminal close" 'grep -qx "orchestration worker-release --dispatch ctx_1 --json" "$FAKE_DIR/calls.log" && grep -qx "terminal close --terminal term_w4 --json" "$FAKE_DIR/calls.log"'
chk "--apply: 제외 대상(미커밋·승인 대기·idle 미달 등)에는 변경 명령 없음" '[ "$(mutating | grep -c .)" = 3 ]' "변경 명령 수 $(mutating | grep -c .)"
chk "로그에 실행 결과 기록" 'grep -q "apply applied" "$LRM_GC_LOG" && grep -q "worker-release 완료" "$LRM_GC_LOG"'
: > "$FAKE_DIR/calls.log"
sg --apply --json > /dev/null
chk "종료 요청은 같은 창에 6시간 안 재전송 없음" '[ "$(grep -c "^terminal send" "$FAKE_DIR/calls.log")" = 0 ]'

# ---- 4. 조회 실패·판정 불가: 무동작 --------------------------------------------------------------
: > "$FAKE_DIR/calls.log"
FAKE_ORCA_FAIL=1 sg --apply --json > "$SB/o4" 2> "$SB/e4"; rc=$?
chk "조회 전체 실패: 종료 코드 1·무동작·로그" '[ $rc = 1 ] && [ -z "$(mutating)" ] && grep -q unavailable "$SB/o4" && grep -q "apply unavailable" "$LRM_GC_LOG" && grep -q "^ERROR:" "$SB/e4"'
: > "$FAKE_DIR/calls.log"
FAKE_ORCA_FAIL_ON=worker-list sg --apply --json > "$SB/o5" 2>/dev/null; rc=$?
chk "worker-list 만 실패: 종료 코드 1·무동작" '[ $rc = 1 ] && [ -z "$(mutating)" ]'
python3 - <<'PY'
import json, os
d = os.environ["FAKE_DIR"]; t = json.load(open(d + "/terminals.json"))
for x in t:
    if x["handle"] in ("term_ca", "term_cb"):
        x["lastOutputAt"] = 1000  # 최근 출력 비교 불가(동률)
json.dump(t, open(d + "/terminals.json", "w"), ensure_ascii=False)
PY
rm -f "$LRM_HUB_DIR/session-gc.json"; : > "$FAKE_DIR/calls.log"
sg --json > "$SB/o6"
chk "코디네이터 최근 출력이 같으면 판정 불가로 제외" 'python3 -c "
import json; d=json.load(open(\"$SB/o6\")); assert not [x for x in d[\"items\"] if x[\"kind\"]==\"dup_coord\"] and [s for s in d[\"skipped\"] if s[\"target\"]==\"허브 alpha\"]"'

# ---- 5. 인자 ------------------------------------------------------------------------------------
sg --bogus > /dev/null 2>&1; chk "알 수 없는 인자: 종료 코드 2" '[ $? = 2 ]'
sg --help | grep -q "session_gc.py --apply"; chk "--help 에 예시" '[ $? = 0 ]'

printf '\n%d 통과, %d 실패\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
