#!/usr/bin/env bash
# ws_gc.sh 의 "부모 없는 게임 저장소 작업 공간 자동 연결" 시험. 가짜 orca 만 씀(실제 작업 공간·Slack 무관). run.sh 가 이어서 실행함.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
GC="${GC_SCRIPT:-$HERE/../../hubs/scripts/ws_gc.sh}"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-adopt.XXXXXX")
trap '[ -n "${KEEP:-}" ] || rm -rf "${SB:?}"' EXIT
export WS_GC_INBOX="$SB/inbox"
FAKE="$SB/fake" ROOT="$SB/ws" ARC="$SB/archive" BIN="$SB/bin" G="$ROOT/LoveRevolutionMaker"
mkdir -p "$FAKE" "$ROOT/repoA" "$G" "$ARC" "$BIN"
ln -s "$HERE/fake_orca.sh" "$BIN/orca"; ln -s "$HERE/fake_gh.sh" "$BIN/gh"
echo '{"ok":true,"result":{"terminals":[]}}' > "$FAKE/terminals.json"
echo '{"ok":true,"result":{"workers":[],"page":{"hasMore":false}}}' > "$FAKE/workers.json"

H1="$G/coord-a-hub" H2="$G/coord-b-hub"
# 작업 공간 목록: 허브 둘(자식은 아래 K1·K2 로 연결), 같은 티켓 형제, 단독 티켓, 갈리는 티켓, 다른 저장소·허브 자체의 부모 없음
mk() { jq -nc --arg p "$1" --arg n "$2" --arg b "$3" --argjson k "${4:-[]}" '{id:("repo::"+$p),path:$p,displayName:$n,branch:$b,lastActivityAt:0,isMainWorktree:false,childWorktreeIds:$k}'; }
cid() { printf 'repo::%s' "$1"; }
jq -s '{ok:true,result:{worktrees:.}}' \
  <(mk "$H1" coord-a-hub refs/heads/main "[\"$(cid "$G/LRM-100-sib")\",\"$(cid "$G/LRM-300-x")\"]") \
  <(mk "$H2" coord-b-hub refs/heads/main "[\"$(cid "$G/LRM-300-y")\"]") \
  <(mk "$G/LRM-100-sib" "[LRM-100] 형제" refs/heads/feat/LRM-100-sib) \
  <(mk "$G/orphan-100" "[LRM-100] 고아" refs/heads/feat/LRM-100-orphan) \
  <(mk "$G/orphan-200" "[LRM-200] 혼자" refs/heads/feat/LRM-200-solo) \
  <(mk "$G/LRM-300-x" "[LRM-300] 갈림 1" refs/heads/feat/LRM-300-x) \
  <(mk "$G/LRM-300-y" "[LRM-300] 갈림 2" refs/heads/feat/LRM-300-y) \
  <(mk "$G/orphan-300" "[LRM-300] 갈림 고아" refs/heads/feat/LRM-300-o) \
  <(mk "$G/untitled" "이름 없음" refs/heads/feat/x) \
  <(mk "$ROOT/repoA/LRM-100-other" "[LRM-100] 다른 저장소" refs/heads/feat/LRM-100-other) \
  <(mk "$G/coord-c-hub" coord-c-hub refs/heads/main) > "$FAKE/worktrees.json"

gc() { # 인자 그대로. 결과: RC, $SB/out(stdout+stderr), $ARC 로그
  : > "$FAKE/orca.calls"; : > "$ARC/log"
  env PATH="$BIN:$PATH" FAKE_DIR="$FAKE" FAKE_ROOT="$ROOT" WS_GC_NOTIFY="$HERE/fake_send.sh" "${ENVX[@]}" bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$ARC/log" --no-slack "$@" > "$SB/out" 2>&1
  RC=$?
}
sets() { grep -c '^worktree set' "$FAKE/orca.calls" || true; }
ENVX=(X=1)

gc --apply
chk "근거 있음(같은 티켓 형제의 허브 1개): 그 허브에 연결" 'grep -qF -- "worktree set --worktree path:$G/orphan-100 --parent-worktree path:$H1 --json" "$FAKE/orca.calls" && grep -q "ADOPT LoveRevolutionMaker/orphan-100 → LoveRevolutionMaker/coord-a-hub" "$ARC/log"' "$(cat "$ARC/log")"
chk "근거 있음: 경고 한 줄(stderr)" 'grep -q "부모 없던 작업 공간을 같은 티켓(LRM-100) 허브에 연결함: $G/orphan-100 → $H1" "$SB/out"'
chk "근거 없음(단독 티켓): 연결 없이 WARN_NO_PARENT + 수동 명령" '! grep -q "path:$G/orphan-200" "$FAKE/orca.calls" && grep -q "WARN_NO_PARENT LoveRevolutionMaker/orphan-200" "$ARC/log" && grep -qF "orca worktree set --worktree path:$G/orphan-200 --parent-worktree" "$SB/out"'
chk "티켓 번호 없는 이름: 연결 안 함·경고 없음" '! grep -q "untitled" "$FAKE/orca.calls" && ! grep -E "ADOPT|WARN_" "$ARC/log" | grep -q untitled'
chk "허브가 갈림(coord-a·coord-b): 연결 없이 WARN_PARENT_SPLIT" '! grep -q "path:$G/orphan-300" "$FAKE/orca.calls" && grep -q "WARN_PARENT_SPLIT LoveRevolutionMaker/orphan-300" "$ARC/log"'
chk "다른 저장소의 부모 없음·허브 자신은 건드리지 않음(연결 호출 정확히 1회)" '[ "$(sets)" = 1 ] && ! grep -E "ADOPT|WARN_" "$ARC/log" | grep -q "LRM-100-other\|coord-c-hub"' "sets=$(sets)"
chk "연결 뒤에도 종료 0" '[ "$RC" = 0 ]'

gc --dry-run
chk "--dry-run: ADOPT_CANDIDATE 기록만, 연결 호출 없음" '[ "$(sets)" = 0 ] && grep -q "ADOPT_CANDIDATE LoveRevolutionMaker/orphan-100" "$ARC/log"'
gc --apply --only orphan-100
chk "--only: 자동 연결 생략" '[ "$(sets)" = 0 ] && ! grep -q "ADOPT" "$ARC/log"'
gc --apply --parent "$H1"
chk "--parent: 자동 연결 생략" '[ "$(sets)" = 0 ] && ! grep -q "ADOPT" "$ARC/log"'
ENVX=(FAKE_SET_FAIL=1); gc --apply
chk "set 실패: ADOPT_FAIL·수동 명령 경고, 종료 0" '[ "$RC" = 0 ] && grep -q "ADOPT_FAIL LoveRevolutionMaker/orphan-100" "$ARC/log" && grep -q "부모 연결 실패: orca worktree set" "$SB/out"'

echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
