#!/usr/bin/env bash
# create_worker_ws.sh 의 허브당 하위 상한 시험. 임시 저장소·가짜 orca·ws_gc·hub_report 만 씀(실제 작업 공간·Slack 무관).
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
CW="$HERE/../../skills/orca-hub-create/scripts/create_worker_ws.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-cw.XXXXXX")
trap '[ -n "${KEEP:-}" ] || rm -rf "${SB:?}"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
F="$SB/fake" BIN="$SB/bin" WSR="$SB/ws" HUBP="$SB/ws/coord-q-hub"
mkdir -p "$F" "$BIN" "$WSR"

# 원격(dev 브랜치)·본 저장소
git init -q --bare -b dev "$SB/origin.git"
git clone -q "$SB/origin.git" "$SB/seed" 2>/dev/null; echo hi > "$SB/seed/README.md"; git -C "$SB/seed" add -A; git -C "$SB/seed" commit -q -m seed; git -C "$SB/seed" push -q origin HEAD:dev 2>/dev/null
git clone -q "$SB/origin.git" "$SB/repo" 2>/dev/null

# 가짜 orca: list 는 $F/list.json, set 은 기록. FAKE_LIST_FAIL=1 이면 list 실패
cat > "$BIN/orca" <<'EOS'
#!/bin/bash
echo "$*" >> "$FAKE_DIR/orca.calls"
case "$1 $2" in
  "worktree list") [ "${FAKE_LIST_FAIL:-}" = 1 ] && exit 1; cat "$FAKE_DIR/list.json" ;;
  "worktree set") echo '{"ok":true}' ;;
  *) exit 1 ;;
esac
EOS
# 가짜 ws_gc: 인자 기록. FAKE_GC_DROP=n 이면 허브 하위 n 개를 목록에서 뺌
cat > "$BIN/ws_gc.sh" <<'EOS'
#!/bin/bash
echo "$*" >> "$FAKE_DIR/gc.calls"
[ -z "${FAKE_GC_SLEEP:-}" ] || { sleep "$FAKE_GC_SLEEP"; echo done > "$FAKE_DIR/gc.finished"; }
[ "${FAKE_GC_FAIL:-}" != 1 ] || exit 7
# TERM 을 무시하고 4초 뒤 표시를 남기는 자식(시간 초과 뒤 잔존 여부 시험)
[ -z "${FAKE_GC_STUBBORN:-}" ] || { bash -c 'trap "" TERM; sleep 4; echo stray > "$FAKE_DIR/gc.stray"' & sleep 30; }
[ -z "${FAKE_GC_DROP:-}" ] || jq --argjson n "$FAKE_GC_DROP" '(.result.worktrees[] | select(.childWorktreeIds | length > 0) | .childWorktreeIds) |= .[$n:]' "$FAKE_DIR/list.json" > "$FAKE_DIR/list.tmp" && mv "$FAKE_DIR/list.tmp" "$FAKE_DIR/list.json"
EOS
cat > "$BIN/hub_report.py" <<'EOS'
import sys, os
if os.environ.get("FAKE_REPORT_FAIL") == "1": sys.exit(1)
open(os.path.join(os.environ["FAKE_DIR"], "report.calls"), "a").write(" ".join(sys.argv[1:]) + "\n")
EOS
chmod +x "$BIN/orca" "$BIN/ws_gc.sh"

setlist() { # 허브 하위 n 개인 목록
  jq -n --arg h "$HUBP" --argjson n "$1" '{ok:true,result:{worktrees:([{id:"r::hub",path:$h,childWorktreeIds:[range(0;$n)|"r::kid\(.)"]}] + [range(0;$n)|{id:"r::kid\(.)",path:"/x/kid\(.)",childWorktreeIds:[]}])}}' > "$F/list.json"
}
run() { # 이름 [env…]. 결과: RC, $SB/out, $SB/err
  : > "$F/orca.calls"; : > "$F/gc.calls"; : > "$F/report.calls"
  env "${@:2}" PATH="$BIN:$PATH" FAKE_DIR="$F" REPO="$SB/repo" WS_ROOT="$WSR" LRM_WS_GC="$BIN/ws_gc.sh" LRM_HUB_REPORT="$BIN/hub_report.py" \
    bash "$CW" --hub "$HUBP" --name "$1" --paths README.md > "$SB/out" 2>"$SB/err"
  RC=$?
}
made() { [ -d "$WSR/$1" ] && grep -q "^worktree set .*--parent-worktree path:$HUBP" "$F/orca.calls"; }

setlist 3; run w1
chk "상한 미만(3<10): 정리·기록 없이 생성" '[ "$RC" = 0 ] && made w1 && [ ! -s "$F/gc.calls" ] && [ ! -s "$F/report.calls" ]' "rc=$RC $(cat "$SB/err")"
setlist 10; run w2 FAKE_GC_DROP=4
chk "상한 도달(10): 허브 하위만 정리 1회" '[ "$(wc -l < "$F/gc.calls" | tr -d " ")" = 1 ] && grep -q -- "--parent $HUBP --apply" "$F/gc.calls"' "$(cat "$F/gc.calls")"
chk "정리로 줄면(6): 경고·기록 없이 생성" '[ "$RC" = 0 ] && made w2 && [ ! -s "$F/report.calls" ] && ! grep -q "⚠️" "$SB/err"'
setlist 10; run w3
chk "정리 뒤에도 10: stderr 경고" '[ "$RC" = 0 ] && grep -q "정리 뒤에도 하위 작업 공간 10개(상한 10)" "$SB/err"'
chk "정리 뒤에도 10: 허브 보고 block 기록" 'grep -q -- "add --hub q --task 없음 --kind block --text 하위 작업 공간 10개, 남길 것만으로 10개 초과" "$F/report.calls"' "$(cat "$F/report.calls")"
chk "정리 뒤에도 10: 생성은 계속, 경로 출력" 'made w3 && grep -q "$WSR/w3" "$SB/out"'
setlist 3; run w4 LRM_HUB_MAX_CHILDREN=3
chk "상한 환경 변수(3): 3개에서 정리 시작" '[ "$(wc -l < "$F/gc.calls" | tr -d " ")" = 1 ] && grep -q "상한 3" "$SB/err"'
setlist 10; run w5 FAKE_REPORT_FAIL=1
chk "보고 기록 실패해도 생성 계속(종료 0)" '[ "$RC" = 0 ] && made w5 && grep -q "허브 보고 기록 실패" "$SB/err"'
setlist 10; run w6 FAKE_LIST_FAIL=1
chk "orca 목록 조회 실패: 정리 없이 생성" '[ "$RC" = 0 ] && made w6 && [ ! -s "$F/gc.calls" ]' "rc=$RC"

# [8] 정리가 멈추면 제한 시간 뒤 끊고 생성을 계속함
setlist 10; rm -f "$F/gc.finished"; t0=$(date +%s); run w7 FAKE_GC_SLEEP=3 LRM_HUB_GC_TIMEOUT=1; t1=$(date +%s)
chk "[8] 정리가 멈춰도 제한 시간(1초) 뒤 끊고 생성 계속(종료 0, 10초 안에 끝남)" '[ "$RC" = 0 ] && made w7 && [ $((t1 - t0)) -lt 10 ] && grep -q "1초 안에 끝나지 않아 중단" "$SB/err"' "rc=$RC took=$((t1 - t0)) $(cat "$SB/err")"
sleep 4
chk "[8] 끊긴 정리기는 남아 돌지 않음(3초 뒤에도 끝 표시 없음)" '[ ! -e "$F/gc.finished" ]'
setlist 10; run w8 FAKE_GC_FAIL=1
chk "[8] 정리기 실패(종료 7): 경고 뒤 생성 계속" '[ "$RC" = 0 ] && made w8 && grep -q "정리기 실행 실패(종료 7)" "$SB/err"'

# [P2] TERM 을 무시하는 자식이 시간 초과 뒤에도 남지 않음(유예 1초 뒤 그룹 KILL)
setlist 10; rm -f "$F/gc.stray"; run w9 FAKE_GC_STUBBORN=1 LRM_HUB_GC_TIMEOUT=1 LRM_HUB_GC_GRACE=1
chk "[P2] TERM 무시 자식이 있어도 시간 초과 뒤 생성 계속" '[ "$RC" = 0 ] && made w9 && grep -q "1초 안에 끝나지 않아 중단" "$SB/err"' "rc=$RC $(cat "$SB/err")"
sleep 5
chk "[P2] TERM 무시 자식이 그룹 KILL 로 종료됨(4초 뒤 표시 없음)" '[ ! -e "$F/gc.stray" ]'
# ---- 한글 이름·--json·중복 거부 ----
runx() { # 인자 그대로 전달(환경은 LOC 로 로케일 지정). 결과: RC, $SB/out, $SB/err
  : > "$F/orca.calls"; : > "$F/gc.calls"
  env LC_ALL="${LOC:-ko_KR.UTF-8}" PATH="$BIN:$PATH" FAKE_DIR="$F" REPO="$SB/repo" WS_ROOT="$WSR" LRM_WS_GC="$BIN/ws_gc.sh" LRM_HUB_REPORT="$BIN/hub_report.py" \
    bash "$CW" --hub "$HUBP" --paths README.md "$@" > "$SB/out" 2>"$SB/err"
  RC=$?
}
dirbytes() { ls "$WSR" | grep -F -- "$1" | head -1 | tr -d '\n' | xxd -p | tr -d '\n'; }   # 폴더 이름 바이트(정규화·깨짐 확인)
KO='[LRM-507] 한글 이름'
setlist 1
runx --name "$KO"
chk "한글 이름(분리 detached): 폴더 LRM-507-한글-이름, 표시 이름은 원문 그대로" '[ "$RC" = 0 ] && [ -d "$WSR/LRM-507-한글-이름" ] && grep -qF -- "--display-name [LRM-507] 한글 이름" "$F/orca.calls" && [ "$(cat "$SB/out")" = "$WSR/LRM-507-한글-이름" ]' "rc=$RC $(cat "$SB/err")"
runx --name "$KO 둘째" --branch "feat/LRM-507-한글-브랜치"
chk "한글 이름·한글 --branch: 폴더 feat-LRM-507-한글-브랜치, 브랜치 이름 보존" '[ "$RC" = 0 ] && [ -d "$WSR/feat-LRM-507-한글-브랜치" ] && git -C "$SB/repo" rev-parse --verify -q "refs/heads/feat/LRM-507-한글-브랜치" >/dev/null' "rc=$RC $(cat "$SB/err")"
for L in C POSIX en_US.UTF-8; do
  LOC=$L runx --name "[LRM-508] 로케일 $L"
  chk "로케일 $L: 한글 폴더 이름 바이트 보존" '[ "$RC" = 0 ] && [ -d "$WSR/LRM-508-로케일-$L" ]' "rc=$RC $(cat "$SB/err")"
done
NFD=$(python3 -c 'import unicodedata;print(unicodedata.normalize("NFD","[LRM-509] 한글 정규화"),end="")')
runx --name "$NFD"
chk "NFD(분해형) 입력도 NFC 폴더로 통일" '[ "$RC" = 0 ] && [ "$(dirbytes LRM-509)" = "$(printf "LRM-509-한글-정규화" | xxd -p | tr -d "\n")" ]' "got=$(dirbytes LRM-509)"
runx --name "$NFD"
chk "같은 이름(NFC·NFD 만 다름)을 다시 만들면 거부(종료 3)" '[ "$RC" = 3 ] && grep -q "이미 있는 작업 공간" "$SB/err"'
runx --name "$(printf '[LRM-510] \xed\x95')"
chk "잘린 UTF-8 이름: 실패하지 않고 깨진 글자만 버림" '[ "$RC" = 0 ] && [ -d "$WSR/LRM-510" ]' "rc=$RC $(cat "$SB/err")"
runx --name "///"
chk "이름에서 폴더 이름을 만들 수 없으면 종료 2" '[ "$RC" = 2 ] && grep -q "폴더 이름을 만들 수 없음" "$SB/err"'

runx --name "[LRM-511] JSON 출력" --branch "feat/LRM-511-json"
chk "기본 출력은 경로 한 줄(호환)" '[ "$RC" = 0 ] && [ "$(wc -l < "$SB/out" | tr -d " ")" = 1 ] && [ "$(cat "$SB/out")" = "$WSR/feat-LRM-511-json" ]'
runx --name "[LRM-512] JSON 출력" --branch "feat/LRM-512-json" --json
chk "--json: path·branch·displayName·parent 한 줄" '[ "$RC" = 0 ] && [ "$(wc -l < "$SB/out" | tr -d " ")" = 1 ] && jq -e --arg w "$WSR" --arg h "$HUBP" ".path == \$w + \"/feat-LRM-512-json\" and .branch == \"feat/LRM-512-json\" and .displayName == \"[LRM-512] JSON 출력\" and .parent == \$h" "$SB/out" >/dev/null' "$(cat "$SB/out" "$SB/err")"
runx --name "[LRM-513] 한글 JSON" --json
chk "--json: detached 는 branch null, 한글 displayName 보존" '[ "$RC" = 0 ] && jq -e ".branch == null and .displayName == \"[LRM-513] 한글 JSON\" and (.path | endswith(\"/LRM-513-한글-JSON\"))" "$SB/out" >/dev/null' "$(cat "$SB/out")"

# 중복 거부: 폴더가 있으면 덮어쓰지 않고 orca·git 을 건드리지 않음
echo keep > "$WSR/LRM-511-json-marker"; mkdir -p "$WSR/LRM-514-기존"; echo keep > "$WSR/LRM-514-기존/marker"
runx --name "[LRM-514] 기존"
chk "같은 이름 폴더가 있으면 거부(종료 3), 기존 내용·orca 호출 없음" '[ "$RC" = 3 ] && [ "$(cat "$WSR/LRM-514-기존/marker")" = keep ] && ! grep -q "^worktree set" "$F/orca.calls" && [ ! -s "$SB/out" ]' "rc=$RC $(cat "$SB/err")"
setlist 1; jq --arg p "$WSR/LRM-515-등록됨" '.result.worktrees += [{id:"r::x",path:$p,childWorktreeIds:[]}]' "$F/list.json" > "$F/list.tmp" && mv "$F/list.tmp" "$F/list.json"
runx --name "[LRM-515] 등록됨"
chk "폴더는 없어도 Orca 에 같은 경로가 등록돼 있으면 거부(종료 3)" '[ "$RC" = 3 ] && [ ! -e "$WSR/LRM-515-등록됨" ]' "rc=$RC"
runx --name "[LRM-514] 기존" --json
chk "--json 도 중복이면 거부(JSON 출력 없음)" '[ "$RC" = 3 ] && [ ! -s "$SB/out" ]'

echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
