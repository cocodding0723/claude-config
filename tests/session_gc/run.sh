#!/usr/bin/env bash
# session_gc.py 검증. orca 는 가짜(fake_orca.py), git 작업 공간은 임시 저장소. 실제 Orca·터미널·Slack 은 쓰지 않음.
# 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함. SG_SCRIPTS=<폴더> 로 변이 검증 가능.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
SCRIPTS=${SG_SCRIPTS:-$REPO/hubs/scripts}
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

mkrepo() { git init -q "$1" && git -C "$1" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init; }
for r in clean1 cleanf clean4 clean5 clean6 clean8 clean10 clean11 clean12 dirty untracked busy; do mkrepo "$SB/$r"; done
echo x > "$SB/dirty/new.txt"; echo y > "$SB/untracked/note.txt"; mkdir -p "$SB/nogit"
# 합성 토큰은 실행 중에 조립(저장소 비밀 패턴 검사에 걸리지 않게)
export SECRET1='hunter2secret' SECRET2='xo'"xb-111-222-aaaabbbb" SECRET3='xo'"xb-999-888-cccc"

mkfix() {
python3 - "$SB" <<'PY'
import json, os, sys, time
sb = sys.argv[1]; d = os.environ["FAKE_DIR"]; now = int(time.time() * 1000)
IDLE = "작업 끝\n❯ "
APPR = "Do you want to proceed?\n❯ 1. Yes\n  2. No"
DRAFT = "작업 끝\n❯ 쓰다 만 글"
BUSY = "✻ Thinking… (esc to interrupt)\n"
def term(h, title, wt, mins, preview=IDLE, agent="claude"):
    return {"handle": h, "title": title, "worktreePath": wt, "agentIdentity": agent, "connected": True, "lastOutputAt": now - int(mins * 60000), "preview": preview}
def worker(disp, h, task, res="owned", archive=True, state="succeeded", dstat="completed", tstate="retained", reason=None):
    r = {"ownershipState": res, "releaseState": "not_requested", "retainedReason": reason, "archive": {"source": "transcript", "status": "captured"} if archive else None} if res else None
    return {"dispatchId": disp, "agentTerminalHandle": h, "taskId": task, "runId": "run_1", "workerState": state, "dispatchStatus": dstat, "terminalState": tstate, "resource": r}
terms = [term("term_ca", "알파 새 코디", "/x/coord-alpha-hub", 1), term("term_cb", "알파 옛 코디 token=" + os.environ["SECRET1"], "/x/coord-alpha-hub", 90, BUSY),
         term("term_xa", "엑스 허브 A", "/a/coord-x-hub", 1), term("term_xb", "엑스 허브 B", "/b/coord-x-hub", 90),
         term("term_ga", "감마 코디", "/x/coord-gamma-hub", 1), term("term_gw", "감마 허브 폴더의 워커 창", "/x/coord-gamma-hub", 90),
         term("term_w1", "끝난 1 password=" + os.environ["SECRET1"] + " " + os.environ["SECRET2"], sb + "/clean1", 45), term("term_wf", "실패로 끝남", sb + "/cleanf", 45),
         term("term_w2", "미커밋", sb + "/dirty", 45), term("term_w2b", "미추적", sb + "/untracked", 45),
         term("term_w3", "승인 대기", sb + "/clean1", 60, APPR), term("term_w4", "채택 창", sb + "/clean4", 50), term("term_w5", "방금 끝남", sb + "/clean5", 5),
         term("term_w6", "기록 없음", sb + "/clean6", 50), term("term_w7", "git 아님", sb + "/nogit", 50), term("term_w8", "사용자 인수", sb + "/clean8", 50),
         term("term_w9", "진행 워커와 공유", sb + "/busy", 50), term("term_w10", "소유권 unknown", sb + "/clean10", 50),
         term("term_w11", "쓰던 글", sb + "/clean11", 50, DRAFT), term("term_w12", "작업은 진행 중", sb + "/clean12", 50)]
retained = [worker("ctx_1", "term_w1", "T1"), worker("ctx_f", "term_wf", "Tf", state="failed", dstat="failed"), worker("ctx_2", "term_w2", "T1"), worker("ctx_2b", "term_w2b", "T1"),
            worker("ctx_3", "term_w3", "T1"), worker("ctx_4", "term_w4", "T1", res=None), worker("ctx_5", "term_w5", "T1"), worker("ctx_6", "term_w6", "T1", archive=False),
            worker("ctx_7", "term_w7", "T1"), worker("ctx_8", "term_w8", "T1", reason="user_takeover " + os.environ["SECRET3"]), worker("ctx_9", "term_w9", "T1"),
            worker("ctx_10", "term_w10", "T1", res="unknown"), worker("ctx_11", "term_w11", "T1"), worker("ctx_12", "term_w12", "T2"),
            worker("ctx_gw", "term_gw", "T1", state="stopped", dstat="failed")]
active = [worker("ctx_a", "term_w9", "T3", state="ready", dstat="dispatched", tstate="active")]
tasks = {"run_1": [{"id": "T1", "status": "completed"}, {"id": "Tf", "status": "failed"}, {"id": "T2", "status": "in_progress"}, {"id": "T3", "status": "in_progress"}]}
for name, val in (("terminals", terms), ("workers_retained", retained), ("workers_active", active), ("tasks", tasks)):
    json.dump(val, open(os.path.join(d, name + ".json"), "w"), ensure_ascii=False)
for disp in ("ctx_4", "ctx_7", "ctx_10"):
    json.dump([1], open(os.path.join(d, "transcript_%s.json" % disp), "w"))
PY
}
mutating() { grep -E '^(terminal send|terminal close|orchestration worker-release)' "$FAKE_DIR/calls.log" 2>/dev/null; }
releases() { grep -c '^orchestration worker-release' "$FAKE_DIR/calls.log" || true; }
released() { grep -qx "orchestration worker-release --dispatch $1 --json" "$FAKE_DIR/calls.log"; }
sg() { python3 -I "$SG" "$@"; }
jq_py() { python3 -c "import json,sys; d=json.load(open('$1')); $2"; }
reset() { : > "$FAKE_DIR/calls.log"; rm -f "$FAKE_DIR/after.json"; }
mkfix

# ---- 1. 시험 모드 ----------------------------------------------------------------------------
reset; sg --json > "$SB/o1"
chk "release 대상은 소유·종료(completed/failed)·입력 칸 비어 있음·미커밋 0·기록 보관 확인된 ctx_1, ctx_f 뿐" 'jq_py "$SB/o1" "assert sorted(x[\"dispatch\"] for x in d[\"items\"] if x[\"kind\"]==\"release\")==[\"ctx_1\",\"ctx_f\"]"'
chk "소유권 unknown·resource 없음(채택)·사용자 인수 창은 '사람 확인' 목록에만" 'jq_py "$SB/o1" "assert sorted(x[\"dispatch\"] for x in d[\"items\"] if x[\"kind\"]==\"human\")==[\"ctx_10\",\"ctx_4\",\"ctx_8\"]"'
why() { jq_py "$SB/o1" "print([s['reason'] for s in d['skipped'] if s['target']=='$1'][0])"; }
chk "미커밋·미추적·승인 대기·쓰던 글·작업 진행 중은 제외" '[[ "$(why ctx_2)" == *미커밋* && "$(why ctx_2b)" == *미추적* && "$(why ctx_3)" == *승인\ 대기* && "$(why ctx_11)" == *입력\ 칸* && "$(why ctx_12)" == *작업\ 미종료* ]]'
chk "idle 미달·기록 없음·git 실패·진행 워커 공유 모두 제외" '[[ "$(why ctx_5)" == idle* && "$(why ctx_6)" == *보관* && "$(why ctx_7)" == *git* && "$(why ctx_9)" == *진행\ 워커* ]]'
chk "종료 보고 없이 멈춘 워커(stopped)는 목록에 없음" '! grep -q ctx_gw "$SB/o1"'
chk "중복 코디네이터는 허브 전체 경로 기준: 알파만 1건(옛 창), 다른 경로 같은 이름·워커 창은 아님" 'jq_py "$SB/o1" "i=[x for x in d[\"items\"] if x[\"kind\"]==\"dup_coord\"]; assert len(i)==1 and i[0][\"handle\"]==\"term_cb\" and i[0][\"hub\"]==\"/x/coord-alpha-hub\", i"'
chk "시험 모드: orca 변경 명령 0회" '[ -z "$(mutating)" ]'
chk "조회는 읽기 전용(worker-list·task-list·worker-read·terminal list 만, show 재조회 없음)" '! grep -Ev "^(orchestration (worker-list|task-list|worker-read)|terminal list)" "$FAKE_DIR/calls.log" | grep -q .'
chk "로그 1줄 이상" 'grep -q "dry-run listed release 2건 사람 확인 3건 중복 코디네이터 1건" "$LRM_GC_LOG"'
chk "시험 모드는 상태 파일을 만들지 않음" '[ ! -e "$LRM_HUB_DIR/session-gc.json" ]'

# ---- 2. 비밀 값 가림 ----------------------------------------------------------------------------
sg > "$SB/o1t"
chk "출력·JSON·로그에 제목·사유의 합성 토큰이 없음(가림 표시는 있음)" '! grep -q "$SECRET1\|$SECRET2\|$SECRET3" "$SB/o1" "$SB/o1t" "$LRM_GC_LOG" && grep -q "\[가림\]" "$SB/o1t"'

# ---- 3. 첫 --apply 는 목록만, 이후 release 만 --------------------------------------------------------
reset; sg --apply --json > "$SB/o2"
chk "첫 --apply: 목록만(변경 0회·재조회 없음)·상태 기록" '[ -z "$(mutating)" ] && grep -q first_apply_listing "$SB/o2" && [ -e "$LRM_HUB_DIR/session-gc.json" ]'
reset; sg --apply --json > "$SB/o3"
chk "--apply: worker-release 는 ctx_1·ctx_f 두 건뿐" '[ "$(releases)" = 2 ] && released ctx_1 && released ctx_f'
chk "--apply: terminal send·close 호출 코드 없음(중복 코디네이터·사람 확인·채택 창에 동작 0)" '! grep -E "^terminal (send|close)" "$FAKE_DIR/calls.log" | grep -q .'
chk "--apply: release 직전 건마다 재조회(worker-show·terminal show·task-list)" '[ "$(grep -c "^orchestration worker-show" "$FAKE_DIR/calls.log")" = 2 ] && [ "$(grep -c "^terminal show" "$FAKE_DIR/calls.log")" = 2 ]'
chk "로그에 release 결과 기록" 'grep -q "worker-release 완료" "$LRM_GC_LOG"'

# ---- 4. 수집 뒤 상태 변화: 재검증에서 건너뜀 --------------------------------------------------------------
after() { reset; printf '%s' "$1" > "$FAKE_DIR/after.json"; sg --apply --json > "$SB/oa"; }
after "{\"terminals\": {\"term_w1\": {\"worktreePath\": \"$SB/dirty\"}}}"
chk "수집 뒤 미커밋 생김 → ctx_1 건너뜀, ctx_f 는 진행" '! released ctx_1 && released ctx_f && grep -q "건너뜀: 재검증 — 작업 공간 미커밋" "$SB/oa"'
after '{"terminals": {"term_w1": {"preview": "Do you want to proceed?\n❯ 1. Yes\n  2. No"}}}'
chk "수집 뒤 승인 대기로 바뀜 → 건너뜀" '! released ctx_1 && released ctx_f && grep -q "재검증 — 승인 대기" "$SB/oa"'
after '{"terminals": {"term_w1": {"preview": "작업 끝\n❯ 사용자가 쓰는 중"}}}'
chk "수집 뒤 사용자 입력 초안 생김 → 건너뜀" '! released ctx_1 && released ctx_f && grep -q "재검증 — 입력 칸" "$SB/oa"'
after '{"tasks": {"T1": "in_progress"}}'
chk "수집 뒤 작업이 다시 진행 중 → 건너뜀" '! released ctx_1 && grep -q "재검증 — 작업 미종료" "$SB/oa"'
after '{"workers": {"ctx_1": {"ownershipState": "user_owned", "retainedReason": "user_takeover"}}}'
chk "수집 뒤 사용자 인수 → 건너뜀" '! released ctx_1 && released ctx_f && grep -q "재검증 — 소유권" "$SB/oa"'
after '{"terminals": {"term_w1": {"lastOutputAt": '"$(python3 -c 'import time;print(int(time.time()*1000))')"'}}}'
chk "수집 뒤 새 출력(idle 해제) → 건너뜀" '! released ctx_1 && released ctx_f && grep -q "재검증 — idle" "$SB/oa"'
reset; : > "$FAKE_DIR/x"; FAKE_ORCA_FAIL_ON=terminal sg --apply --json > /dev/null 2>&1
chk "재조회 실패 → release 없음" '! released ctx_1'

# ---- 5. 조회 실패·불완전: 무동작 ------------------------------------------------------------------------
noop() { [ "$1" = 1 ] && [ -z "$(mutating)" ]; }
reset; FAKE_ORCA_FAIL=1 sg --apply --json > "$SB/o4" 2> "$SB/e4"; rc=$?
chk "조회 전체 실패: 종료 코드 1·무동작·로그·ERROR 안내" 'noop $rc && grep -q "apply unavailable" "$LRM_GC_LOG" && grep -q "^ERROR:" "$SB/e4"'
reset; FAKE_ORCA_FAIL_ON=worker-list sg --apply > /dev/null 2>&1; rc=$?; chk "worker-list 실패: 무동작" 'noop $rc'
reset; FAKE_ORCA_FAIL_ON=task-list sg --apply > /dev/null 2>&1; rc=$?; chk "task-list 실패: 무동작" 'noop $rc'
reset; FAKE_PAGES=6 LRM_STALL_PAGES=5 sg --apply --json > "$SB/o5" 2>/dev/null; rc=$?
chk "worker-list 가 쪽 상한(5)에 닿고 6쪽이 남음(6쪽에만 있던 핸들 재사용 가정): 조회 불완전 → 무동작" 'noop $rc && grep -q "조회 불완전" "$SB/o5"'
reset; FAKE_PAGES=3 LRM_STALL_PAGES=5 sg --apply > /dev/null 2>&1; rc=$?
chk "쪽 상한 안에 끝까지 읽으면 정상 동작" '[ $rc = 0 ] && released ctx_1'
reset; FAKE_TERM_CURSOR=1 sg --apply > /dev/null 2>&1; rc=$?; chk "terminal list 가 더 남음: 무동작" 'noop $rc'

# ---- 6. 코디네이터 판정 ---------------------------------------------------------------------------------
python3 - <<'PY'
import json, os
d = os.environ["FAKE_DIR"]; t = json.load(open(d + "/terminals.json"))
for x in t:
    if x["handle"] in ("term_ca", "term_cb"):
        x["lastOutputAt"] = 1000
json.dump(t, open(d + "/terminals.json", "w"), ensure_ascii=False)
PY
reset; sg --json > "$SB/o6"
chk "코디네이터 최근 출력이 같으면 판정 불가로 제외(목록 안 함)" 'jq_py "$SB/o6" "assert not [x for x in d[\"items\"] if x[\"kind\"]==\"dup_coord\"] and [s for s in d[\"skipped\"] if s[\"target\"].startswith(\"허브 \")]"'

# ---- 7. launchd: 짧은 PATH·임시 HOME 에서 실행 -----------------------------------------------------------
PL="$REPO/hubs/launchd/ai.lrm.sessiongc.plist"
python3 -c "import plistlib,sys; plistlib.load(open(sys.argv[1],'rb'))" "$PL" 2>/dev/null; chk "plist: XML 파서(주석 안 '--' 금지 포함)로 읽힘" '[ $? = 0 ]'
chk "plist: python 은 __PYTHON3__ 자리표시(설치 때 확인한 절대 경로), --apply 있음(11-13 D-CFGAPPLY 승인), 30분" 'grep -q "<string>__PYTHON3__</string>" "$PL" && ! grep -q "/usr/bin/python3" "$PL" && grep -q "<string>--apply</string>" "$PL" && grep -q "<integer>1800</integer>" "$PL" && plutil -lint "$PL" > /dev/null'
FAKEHOME="$SB/home"; mkdir -p "$FAKEHOME/.config/lrm-hubs"
cp "$SCRIPTS"/*.py "$FAKEHOME/.config/lrm-hubs/" 2>/dev/null
sed -e "s#__HOME__#$FAKEHOME#g" -e "s#__PYTHON3__#$(command -v python3)#g" "$PL" > "$SB/sessiongc.plist"
cmd=$(python3 - "$SB/sessiongc.plist" <<'PY'
import plistlib, shlex, sys
p = plistlib.load(open(sys.argv[1], "rb")); print(shlex.join(p["ProgramArguments"] + ["--help"])); open(sys.argv[1] + ".path", "w").write(p["EnvironmentVariables"]["PATH"])
PY
)
out=$(cd / && env -i HOME="$FAKEHOME" PATH="$(cat "$SB/sessiongc.plist.path")" LRM_HUB_DIR="$SB/hub2" bash -c "$cmd" 2>&1); rc=$?
chk "plist 인자 그대로(임시 HOME·plist PATH)에서 --help 실행: 종료 0·TypeError 없음" '[ $rc = 0 ] && grep -q "session_gc.py" <<<"$out" && ! grep -q "TypeError" <<<"$out"' "$out"
if [ -x /usr/bin/python3 ] && /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info < (3, 10) else 1)' 2>/dev/null; then
  out=$(/usr/bin/python3 "$SG" --help 2>&1); rc=$?
  chk "시스템 Python 3.9 로 실행하면 TypeError 대신 안내하고 종료 코드 2" '[ $rc = 2 ] && grep -q "3.10 이상" <<<"$out" && ! grep -q "TypeError" <<<"$out"' "$out"
else
  ok "시스템 Python 이 3.10 이상이라 3.9 안내 시험은 건너뜀"
fi

# ---- 8. 인자 -------------------------------------------------------------------------------------------
sg --bogus > /dev/null 2>&1; chk "알 수 없는 인자: 종료 코드 2" '[ $? = 2 ]'
sg --help | grep -q "session_gc.py --apply"; chk "--help 에 예시" '[ $? = 0 ]'

printf '\n%d 통과, %d 실패\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
