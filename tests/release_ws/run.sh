#!/usr/bin/env bash
# release_ws.sh 시험. 임시 저장소·가짜 orca·gh(tests/ws_gc 것 재사용)만 씀. 실제 워크스페이스·Slack 은 건드리지 않음.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
GCT="$HERE/../ws_gc"
REL="$HERE/../../hubs/scripts/release_ws.sh"
export LRM_WS_GC="$HERE/../../hubs/scripts/ws_gc.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-rel.XXXXXX")
trap '[ -n "${KEEP:-}" ] || rm -rf "${SB:?}"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
FAKE="$SB/fake" ROOT="$SB/ws" ARC="$SB/archive" O="$SB/origin" BIN="$SB/bin" W="$ROOT/repoA"
mkdir -p "$FAKE/pr" "$ROOT" "$ARC" "$O" "$BIN" "$W"
ln -s "$GCT/fake_orca.sh" "$BIN/orca"; ln -s "$GCT/fake_gh.sh" "$BIN/gh"
export WS_GC_ROOT="$ROOT" WS_GC_ARCHIVE="$ARC" WS_GC_LOG="$ARC/ws_gc.log" WS_GC_INBOX="$SB/inbox" WS_GC_NOTIFY=/nonexistent
export PATH="$BIN:$PATH" FAKE_DIR="$FAKE" FAKE_ROOT="$ROOT"
OLD="2026-01-01T00:00:00" NOW_MS=$(($(date +%s) * 1000))
: > "$SB/reg.tsv"; : > "$FAKE/terminals.tsv"; : > "$FAKE/workers.tsv"

git init -q --bare -b main "$O/repoA.git"
SEED="$SB/seed"; git clone -q "$O/repoA.git" "$SEED" 2>/dev/null
printf 'TestResults/\nLibrary/\n' > "$SEED/.gitignore"; printf 'readme\n' > "$SEED/README.md"
git -C "$SEED" add -A; GIT_AUTHOR_DATE=$OLD GIT_COMMITTER_DATE=$OLD git -C "$SEED" commit -q -m seed; git -C "$SEED" push -q origin HEAD:main 2>/dev/null

# mk 이름 [허브 폴더 여부]: 브랜치·옛 커밋·origin push·결과물 폴더. PR 은 병합으로 둠
mk() {
  local n="$1" b="feat/$1"
  git clone -q "$O/repoA.git" "$W/$n" 2>/dev/null; git -C "$W/$n" checkout -q -b "$b"; echo "$n" > "$W/$n/f.txt"; git -C "$W/$n" add -A
  GIT_AUTHOR_DATE=$OLD GIT_COMMITTER_DATE=$OLD git -C "$W/$n" commit -q -m "$n"; git -C "$W/$n" push -q -u origin "$b" 2>/dev/null
  mkdir -p "$W/$n/TestResults"; echo r > "$W/$n/TestResults/a.xml"
  printf '%s\t%s\t%s\tfalse\n' "$W/$n" "refs/heads/$b" "$NOW_MS" >> "$SB/reg.tsv"
  printf '[{"number":1,"state":"MERGED"}]\n' > "$FAKE/pr/feat_$n.json"
}
worker() { printf '%s\t%s\tterm_%s\t%s\n' "$1" "${3:-reclaimable}" "$1" "$W/$2" >> "$FAKE/workers.tsv"; }  # dispatch 이름 [상태]

mk out_only; echo md > "$W/out_only/s.md"; echo png > "$W/out_only/a.png"; worker d_out out_only
mk clean_ws; worker d_clean clean_ws
mk code_untr; echo 'class A{}' > "$W/code_untr/Foo.cs"; worker d_code code_untr
mk tracked_mod; echo edit >> "$W/tracked_mod/README.md"; worker d_track tracked_mod
mk coord-q-hub; worker d_hub coord-q-hub
mk shared; worker d_s1 shared; worker d_s2 shared active
mk arcfail; echo md > "$W/arcfail/r.md"; worker d_arc arcfail; mkdir -p "$ARC/repoA"; : > "$ARC/repoA/arcfail"   # 보관 폴더 자리에 파일 → 복제 불가
mk dry_ws; echo md > "$W/dry_ws/r.md"; worker d_dry dry_ws active; printf 'term_d_dry\t%s\n' "$W/dry_ws" >> "$FAKE/terminals.tsv"
mk kid_parent; worker d_kp kid_parent
mk newcommit; echo more > "$W/newcommit/g.txt"; git -C "$W/newcommit" add -A; GIT_AUTHOR_DATE=$OLD GIT_COMMITTER_DATE=$OLD git -C "$W/newcommit" commit -q -m unpushed; worker d_nc newcommit
mk ignored_code; mkdir -p "$W/ignored_code/secret"; echo 'class S{}' > "$W/ignored_code/secret/Foo.cs"; printf 'secret/\n' >> "$W/ignored_code/.git/info/exclude"; worker d_ic ignored_code

jq -R -s '{ok:true,result:{worktrees:(split("\n")|map(select(length>0)|split("\t")|{id:("repo::"+.[0]),path:.[0],branch:.[1],lastActivityAt:(.[2]|tonumber),isMainWorktree:(.[3]=="true"),childWorktreeIds:[]}))}}' "$SB/reg.tsv" > "$FAKE/worktrees.json"
jq -R -s '{ok:true,result:{terminals:(split("\n")|map(select(length>0)|split("\t")|{handle:.[0],worktreePath:.[1],connected:true,orphaned:false}))}}' "$FAKE/terminals.tsv" > "$FAKE/terminals.json"
jq -R -s '{ok:true,result:{workers:(split("\n")|map(select(length>0)|split("\t")|{dispatchId:.[0],terminalState:.[1],agentTerminalHandle:.[2],resource:{worktreeId:("repo::"+.[3])}})),page:{hasMore:false}}}' "$FAKE/workers.tsv" > "$FAKE/workers.json"

rel() { : > "$FAKE/orca.calls"; FAKE_RM_CHECK_DIRTY=1 bash "$REL" "$@" > "$SB/out" 2>"$SB/err"; RC=$?; }
rm_calls() { grep -c '^worktree rm' "$FAKE/orca.calls" || true; }
released() { grep -q "^orchestration worker-release --dispatch $1" "$FAKE/orca.calls"; }

bash -n "$REL" && ok "bash -n 통과" || ko "bash -n"
rel --help; chk "--help 종료 0, 종료 코드·예 포함" '[ "$RC" = 0 ] && grep -q "^종료 코드:" "$SB/out" && grep -q "^예:" "$SB/out"'
rel; chk "--dispatch 없음: 종료 2·원인·예" '[ "$RC" = 2 ] && grep -q -- "--dispatch" "$SB/err"'
rel --bogus; chk "알 수 없는 인자: 종료 2" '[ "$RC" = 2 ]'
rel --dispatch nope; chk "알 수 없는 dispatch: 종료 2·삭제 없음" '[ "$RC" = 2 ] && [ "$(rm_calls)" = 0 ]'

rel --dispatch d_out
chk "결과물만: release → 삭제, 종료 0" '[ "$RC" = 0 ] && released d_out && [ ! -e "$W/out_only" ] && grep -q "^삭제함" "$SB/out"' "rc=$RC $(cat "$SB/out" "$SB/err")"
chk "결과물만: 보관·색인 남음" '[ "$(cat "$ARC/repoA/out_only/s.md")" = md ] && [ -f "$ARC/repoA/out_only/TestResults/a.xml" ] && grep -q "repoA/out_only" "$SB/inbox/_none.tsv"'
chk "release 가 삭제보다 먼저" '[ "$(grep -n "worker-release --dispatch d_out" "$FAKE/orca.calls" | head -1 | cut -d: -f1)" -lt "$(grep -n "^worktree rm" "$FAKE/orca.calls" | head -1 | cut -d: -f1)" ]'
rel --dispatch d_clean; chk "깨끗한 곳: 삭제, 종료 0" '[ "$RC" = 0 ] && [ ! -e "$W/clean_ws" ]'
rel --dispatch d_clean; chk "이미 지운 뒤 재실행: 삭제 시도 없음(종료 4, 안내)" '[ "$RC" = 4 ] && [ "$(rm_calls)" = 0 ]' "rc=$RC $(cat "$SB/out")"
rel --dispatch d_code; chk "코드 미추적(.cs): 남김 종료 4·이유·파일 출력" '[ "$RC" = 4 ] && [ -e "$W/code_untr" ] && [ "$(rm_calls)" = 0 ] && grep -q "남김(dirty)" "$SB/out" && grep -q "Foo.cs" "$SB/out" && grep -q "조치" "$SB/out"' "$(cat "$SB/out")"
rel --dispatch d_track; chk "추적 파일 수정: 남김 종료 4" '[ "$RC" = 4 ] && [ -e "$W/tracked_mod" ] && [ "$(rm_calls)" = 0 ]'
rel --dispatch d_hub; chk "허브 폴더: 거부 종료 5·삭제 안 함" '[ "$RC" = 5 ] && [ -e "$W/coord-q-hub" ] && [ "$(rm_calls)" = 0 ] && grep -q "거부(hub)" "$SB/out"' "$(cat "$SB/out")"
rel --dispatch d_s1; chk "다른 워커가 쓰는 곳: 거부 종료 5·삭제 안 함" '[ "$RC" = 5 ] && [ -e "$W/shared" ] && [ "$(rm_calls)" = 0 ] && grep -q "거부(live)" "$SB/out"' "$(cat "$SB/out")"
rel --dispatch d_arc; chk "보관 불일치(복제 불가): 삭제 안 함, 종료 1" '[ "$RC" = 1 ] && [ -e "$W/arcfail" ] && [ "$(rm_calls)" = 0 ] && grep -q "삭제 실패" "$SB/out"' "rc=$RC $(cat "$SB/out")"
rel --dispatch d_dry --dry-run; chk "--dry-run: release·삭제 없음, 이 워커 터미널은 쉬는 중으로 침 → 삭제 가능 종료 0" '[ "$RC" = 0 ] && ! grep -q "worker-release" "$FAKE/orca.calls" && [ "$(rm_calls)" = 0 ] && [ -e "$W/dry_ws" ] && grep -q "삭제 가능" "$SB/out"' "rc=$RC $(cat "$SB/out")"
rel --dispatch d_dry --dry-run --json; chk "--json: status·workspace 한 줄" 'jq -e ".status == \"candidate\" and (.workspace | test(\"dry_ws\"))" "$SB/out" >/dev/null' "$(cat "$SB/out")"
rel --dispatch d_nc; chk "병합된 PR 뒤 push 안 한 커밋: 남김 종료 4·삭제 안 함" '[ "$RC" = 4 ] && [ -e "$W/newcommit" ] && [ "$(rm_calls)" = 0 ] && grep -q "남김(unmerged)" "$SB/out"' "rc=$RC $(cat "$SB/out")"
rel --dispatch d_ic; chk "gitignore 코드 파일(secret/Foo.cs): 남김 종료 4·삭제 안 함" '[ "$RC" = 4 ] && [ -e "$W/ignored_code/secret/Foo.cs" ] && [ "$(rm_calls)" = 0 ] && grep -q "secret/Foo.cs" "$SB/out"' "rc=$RC $(cat "$SB/out")"
rel --dispatch d_kp --dry-run; chk "--dry-run(깨끗): 삭제 가능" '[ "$RC" = 0 ] && [ -e "$W/kid_parent" ]'

echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
