#!/usr/bin/env bash
# ws_gc.sh 시험. 임시 저장소·가짜 orca·gh(PATH 앞)·가짜 Slack 전송(WS_GC_NOTIFY)만 씀. 실제 워크스페이스·네트워크·Slack 은 건드리지 않음.
# GC_SCRIPT=<경로> 로 변형 사본을 시험할 수 있음(변이 확인용, tests/ws_gc/mutate.sh).
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
GC="${GC_SCRIPT:-$HERE/../../hubs/scripts/ws_gc.sh}"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-wsgc.XXXXXX")
trap '[ -n "${KEEP:-}" ] || rm -rf "${SB:?}"' EXIT
# 사용자 git 설정·훅이 시험에 끼지 않게 격리
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
FAKE="$SB/fake" ROOT="$SB/ws" ARC="$SB/archive" O="$SB/origin" BIN="$SB/bin" W="$ROOT/repoA"
mkdir -p "$FAKE/pr" "$ROOT" "$ARC" "$O" "$BIN" "$W"
ln -s "$HERE/fake_orca.sh" "$BIN/orca"; ln -s "$HERE/fake_gh.sh" "$BIN/gh"
OLD="2026-01-01T00:00:00" OLD_MS=1767225600000 NOW_MS=$(($(date +%s) * 1000))
: > "$SB/reg.tsv"; : > "$FAKE/terminals.tsv"; : > "$FAKE/workers.tsv"

# ---- 저장소 준비 ---------------------------------------------------------------------------
git init -q --bare -b main "$O/repoA.git"; git init -q -b main "$O/sub.git.work"
printf 'sub\n' > "$O/sub.git.work/s.txt"; git -C "$O/sub.git.work" add -A; git -C "$O/sub.git.work" commit -q -m sub
git clone -q --bare "$O/sub.git.work" "$O/sub.git"
SEED="$SB/seed"; git clone -q "$O/repoA.git" "$SEED" 2>/dev/null
mkdir -p "$SEED/Assets/Sound/s"
printf 'TestResults/\ndocs/out/\nLibrary/\n*.html\n*.mp4\n' > "$SEED/.gitignore"
printf 'readme\n' > "$SEED/README.md"; printf 'line1\nline2\n' > "$SEED/Assets/Sound/s/a.LICENSE.txt"
git -C "$SEED" add -A; GIT_AUTHOR_DATE=$OLD GIT_COMMITTER_DATE=$OLD git -C "$SEED" commit -q -m seed; git -C "$SEED" push -q origin HEAD:main 2>/dev/null

clone() { git clone -q "$O/repoA.git" "$W/$1" 2>/dev/null; }
oldc() { GIT_AUTHOR_DATE=$OLD GIT_COMMITTER_DATE=$OLD git -C "$W/$1" commit -q -m "$2"; }
# mk 이름 브랜치 [push|local] [recent] : 브랜치 만들고 옛 커밋 1개. push 면 origin 에도 올림
mk() {
  local n="$1" b="$2" mode="${3:-push}" when="${4:-old}"
  clone "$n"; git -C "$W/$n" checkout -q -b "$b"; echo "$n" > "$W/$n/f.txt"; git -C "$W/$n" add -A
  if [ "$when" = recent ]; then git -C "$W/$n" commit -q -m "$n"; else oldc "$n" "$n"; fi
  [ "$mode" = push ] && git -C "$W/$n" push -q -u origin "$b" 2>/dev/null
  # 결과물(gitignore): 보관 대상 TestResults·docs/out·html, 보관 제외 Library
  mkdir -p "$W/$n/TestResults" "$W/$n/docs/out" "$W/$n/Library/Cache"
  echo r1 > "$W/$n/TestResults/a.xml"; echo r2 > "$W/$n/TestResults/b.xml"; echo h > "$W/$n/docs/out/x.html"; echo c > "$W/$n/Library/Cache/c.bin"
  reg "$n" "refs/heads/$b" "$([ "$when" = recent ] && echo $NOW_MS || echo $OLD_MS)"
}
reg() { printf '%s\t%s\t%s\t%s\n' "$W/$1" "$2" "$3" "${4:-false}" >> "$SB/reg.tsv"; }
pr() { printf '%s\n' "$2" > "$FAKE/pr/$(printf '%s' "$1" | tr / _).json"; }
detach() { git -C "$W/$1" checkout -q --detach; perl -pi -e 's#^(\Q'"$W/$1"'\E)\trefs/heads/[^\t]*#$1\t#' "$SB/reg.tsv"; }  # 등록부 branch 도 비움(빈 칸이 열을 밀지 않는지 확인)

mk merged feat/merged;          pr feat/merged '[{"number":11,"state":"MERGED"}]'
mk closed_local feat/closed local; pr feat/closed '[{"number":12,"state":"CLOSED"}]'
mk noise feat/noise;            pr feat/noise '[{"number":13,"state":"MERGED"}]'
  mkdir -p "$W/noise/Assets/X"; echo m > "$W/noise/Assets/X/new.cs.meta"
  printf 'line1\r\nline2\r\n' > "$W/noise/Assets/Sound/s/a.LICENSE.txt"
mk dirty feat/dirty;            pr feat/dirty '[{"number":14,"state":"MERGED"}]'; echo edit >> "$W/dirty/README.md"
mk dirty_lic feat/dlic;         pr feat/dlic '[{"number":15,"state":"MERGED"}]'; echo 'line3' >> "$W/dirty_lic/Assets/Sound/s/a.LICENSE.txt"
mk recent feat/recent push recent; pr feat/recent '[{"number":16,"state":"MERGED"}]'
mk coord-hub feat/hub;          pr feat/hub '[{"number":17,"state":"MERGED"}]'
mk live feat/live;              pr feat/live '[{"number":18,"state":"MERGED"}]'
  printf 'term_1\t%s\n' "$W/live" >> "$FAKE/terminals.tsv"
mk worker_active feat/wa;       pr feat/wa '[{"number":19,"state":"MERGED"}]'
  printf 'd_active\tactive\tterm_9\t%s\n' "$W/worker_active" >> "$FAKE/workers.tsv"
mk worker_idle feat/wi;         pr feat/wi '[{"number":20,"state":"MERGED"}]'
  printf 'd_idle\treclaimable\tterm_8\t%s\n' "$W/worker_idle" >> "$FAKE/workers.tsv"; printf 'term_8\t%s\n' "$W/worker_idle" >> "$FAKE/terminals.tsv"
mk pr_open feat/open;           pr feat/open '[{"number":21,"state":"MERGED"},{"number":22,"state":"OPEN"}]'
mk nopr_pushed feat/nopr
mk nopr_local feat/nopl local
mk det_ok feat/d1;              detach det_ok
mk det_local feat/d2 local;     detach det_local
mk prot dev;                    pr dev '[{"number":23,"state":"MERGED"}]'
mk ghfail feat/ghf;             pr feat/ghf '[{"number":24,"state":"MERGED"}]'
for s in sub_clean sub_dirty; do
  mk "$s" "feat/$s"; pr "feat/$s" '[{"number":30,"state":"MERGED"}]'
  git -C "$W/$s" -c protocol.file.allow=always submodule add -q "$O/sub.git" Assets/Sub 2>/dev/null
  git -C "$W/$s" add -A; oldc "$s" sub; git -C "$W/$s" push -q origin "feat/$s" 2>/dev/null
done
echo wip > "$W/sub_dirty/Assets/Sub/wip.txt"
mk mainco feat/mc;              pr feat/mc '[{"number":25,"state":"MERGED"}]'; sed -i '' '$ s/false$/true/' "$SB/reg.tsv"
mkdir -p "$W/orphan_dir"; git init -q "$W/orphan_dir"       # Orca 에 등록되지 않은 폴더

jq -R -s '{ok:true,result:{worktrees:(split("\n")|map(select(length>0)|split("\t")|{id:("repo::"+.[0]),path:.[0],branch:.[1],lastActivityAt:(.[2]|tonumber),isMainWorktree:(.[3]=="true"),childWorktreeIds:[]}))}}' "$SB/reg.tsv" > "$FAKE/worktrees.json"
jq -R -s '{ok:true,result:{terminals:(split("\n")|map(select(length>0)|split("\t")|{handle:.[0],worktreePath:.[1],connected:true,orphaned:false}))}}' "$FAKE/terminals.tsv" > "$FAKE/terminals.json"
jq -R -s '{ok:true,result:{workers:(split("\n")|map(select(length>0)|split("\t")|{dispatchId:.[0],terminalState:.[1],agentTerminalHandle:.[2],resource:{worktreeId:("repo::"+.[3])}})),page:{hasMore:false}}}' "$FAKE/workers.tsv" > "$FAKE/workers.json"
chk "준비: detached 등록부 branch 가 비어 있음" 'jq -e "[.result.worktrees[]|select(.path|test(\"det_ok\"))|.branch]==[\"\"]" "$FAKE/worktrees.json" >/dev/null'
dirs_before=$(cd "$ROOT" && find . -mindepth 2 -maxdepth 2 -type d | sort)

gc() { # 인자를 ws_gc.sh 에 전달. 결과: RC, $SB/out
  : > "$FAKE/orca.calls"; : > "$FAKE/notify.calls"
  env PATH="$BIN:$PATH" FAKE_DIR="$FAKE" FAKE_ROOT="$ROOT" FAKE_RM_REFUSE="sub_clean sub_noise" FAKE_GH_FAIL="feat/ghf" WS_GC_NOTIFY="$HERE/fake_send.sh" \
    bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$ARC/ws_gc.log" "$@" > "$SB/out" 2>&1
  RC=$?
}
has() { grep -q -- "$1" "$ARC/ws_gc.log"; }
rm_calls() { grep -c '^worktree rm' "$FAKE/orca.calls" || true; }
CAND="merged closed_local noise worker_idle nopr_pushed det_ok sub_clean"       # 삭제 후보(최근·허브 등은 아님)
SKIPS="dirty:dirty dirty_lic:dirty recent:idle coord-hub:hub live:live worker_active:live pr_open:pr_open nopr_local:unmerged det_local:unmerged prot:protected ghfail:gh_fail sub_dirty:submodule orphan_dir:not_orca"

# ---- 0. 문법·인자 --------------------------------------------------------------------------
bash -n "$GC" && ok "bash -n 통과" || ko "bash -n" ""
gc --help; chk "--help 종료 0, 사용·예 포함" '[ "$RC" = 0 ] && grep -q -- "--apply" "$SB/out" && grep -q "^예:" "$SB/out"' "rc=$RC"
gc --bogus; chk "알 수 없는 인자 종료 2, 원인·조치 포함" '[ "$RC" = 2 ] && grep -q -- "--help" "$SB/out"' "rc=$RC"
gc --days abc; chk "--days 숫자 아님 종료 2" '[ "$RC" = 2 ]' "rc=$RC"
rm -f "$ARC/ws_gc.log"

# ---- 1. dry-run(기본): 판정만, 삭제·보관·rm 호출 없음 ----------------------------------------
gc
chk "dry-run 종료 0" '[ "$RC" = 0 ]' "rc=$RC"
chk "dry-run: 폴더 목록 불변" '[ "$(cd "$ROOT" && find . -mindepth 2 -maxdepth 2 -type d | sort)" = "$dirs_before" ]'
chk "dry-run: orca worktree rm 호출 0" '[ "$(rm_calls)" = 0 ]' "calls=$(rm_calls)"
chk "dry-run: 보관 폴더에 결과물 없음" '[ -z "$(find "$ARC" -type f ! -name ws_gc.log | head -1)" ]'
for n in $CAND; do chk "dry-run 후보: $n" 'has "CANDIDATE repoA/$n "'; done
for e in $SKIPS; do n="${e%%:*}" r="${e##*:}"; chk "dry-run 건너뜀: $n ($r)" 'has "SKIP $r .*$n"'; done
chk "dry-run: 메인 체크아웃은 평가 대상 아님" '! has "repoA/mainco"'
chk "dry-run: 부산물 근거 로그(.meta·줄바꿈만)" 'has "NOISE   부산물 Assets/X/new.cs.meta" && has "NOISE   줄바꿈만 Assets/Sound/s/a.LICENSE.txt"'
chk "dry-run: 진짜 LICENSE 변경은 dirty" 'has "SKIP dirty repoA/dirty_lic"'
chk "dry-run: 요약 줄 기록" 'has "END 삭제 후보 7개"'
chk "dry-run: Slack progress 1회" '[ "$(wc -l < "$FAKE/notify.calls" | tr -d " ")" = 1 ] && grep -q -- "--kind progress" "$FAKE/notify.calls" && grep -q "시험 점검" "$FAKE/notify.calls"'
chk "dry-run: Slack 본문에 양식 A 골격" 'grep -q "시험 점검⏎⏎• 결과:" "$FAKE/notify.calls"'
gc --no-slack; chk "--no-slack 은 전송 안 함" '[ ! -s "$FAKE/notify.calls" ]'
gc --days 0 --only recent; chk "--days 0 --only: 최근 활동도 후보로" 'has "CANDIDATE repoA/recent "'
gc --only merged --json 2>/dev/null; JS=$(env PATH="$BIN:$PATH" FAKE_DIR="$FAKE" FAKE_ROOT="$ROOT" WS_GC_NOTIFY="$HERE/fake_send.sh" bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$ARC/ws_gc.log" --only merged --json --no-slack 2>/dev/null)
chk "--json: stdout 은 요약 JSON 만" 'echo "$JS" | jq -e ".mode == \"DRY\" and .candidates == 1 and .deleted == 0" >/dev/null' "$JS"

# ---- 2. 환경 오류: 판정 불가면 아무것도 안 지움 ------------------------------------------------
FAKE_ORCA_FAIL=1 gc --apply; chk "orca 실패 → 종료 3, 삭제 없음" '[ "$RC" = 3 ] && [ "$(cd "$ROOT" && find . -mindepth 2 -maxdepth 2 -type d | sort)" = "$dirs_before" ]' "rc=$RC"
chk "orca 실패 오류 메시지에 조치 포함" 'grep -q "orca status" "$SB/out"'

# ---- 3. --apply ---------------------------------------------------------------------------
gc --apply
chk "apply 종료 0" '[ "$RC" = 0 ]' "rc=$RC $(tail -3 "$SB/out")"
for n in $CAND; do chk "apply 삭제됨: $n" '[ ! -e "$W/$n" ]'; done
for e in $SKIPS; do n="${e%%:*}"; chk "apply 유지: $n" '[ -e "$W/$n" ]'; done
chk "apply 유지: 메인 체크아웃" '[ -e "$W/mainco" ]'
chk "apply: rm 호출 = 후보 수 + 서브모듈 강제 1" '[ "$(rm_calls)" = 8 ]' "calls=$(rm_calls)"
chk "apply: 서브모듈 거부 → --force 재시도" 'grep -q "^worktree rm --worktree path:$W/sub_clean --force" "$FAKE/orca.calls" && has "RM_RETRY_FORCE"'
chk "apply: 서브모듈 거부된 sub_dirty 는 rm 시도 자체 없음" '! grep -q "sub_dirty" "$FAKE/orca.calls"'
chk "apply: 브랜치 삭제 옵션 없음" '! grep -q -- "--delete-branch" "$FAKE/orca.calls"'
chk "apply: TestResults 보관(파일 수 일치)" '[ "$(find "$ARC/repoA/merged/TestResults" -type f | wc -l | tr -d " ")" = 2 ]'
chk "apply: docs/out·html 보관" '[ -f "$ARC/repoA/merged/docs/out/x.html" ]'
chk "apply: gitignore 캐시(Library)는 보관 안 함" '[ ! -e "$ARC/repoA/merged/Library" ]'
chk "apply: 보관 로그에 파일 수 대조" 'has "ARCHIVE TestResults 파일 2"'
chk "apply: 쉬는 워커 worker-release" 'grep -q "^orchestration worker-release --dispatch d_idle" "$FAKE/orca.calls"'
chk "apply: 활동 중 워커는 release 안 함" '! grep -q "d_active" "$FAKE/orca.calls"'
chk "apply: 삭제 로그에 브랜치 유지 표기" 'has "DELETE repoA/merged .*브랜치는 유지"'
chk "apply: 요약·Slack(삭제 7개)" 'has "END 삭제 7개" && grep -q "자동 정리 완료" "$FAKE/notify.calls" && grep -q "삭제 7개" "$FAKE/notify.calls"'
chk "apply: 브랜치는 저장소에 그대로" 'git -C "$O/repoA.git" rev-parse --verify -q refs/heads/feat/merged >/dev/null'

# ---- 4. 반복 실행 안전 ------------------------------------------------------------------------
gc --apply
chk "재실행: 종료 0, rm 호출 0, 새 삭제 없음" '[ "$RC" = 0 ] && [ "$(rm_calls)" = 0 ] && ! grep -q "^DELETE" "$SB/out"' "rc=$RC calls=$(rm_calls)"
chk "재실행: 후보 없으면 Slack 생략" '[ ! -s "$FAKE/notify.calls" ]'

# ---- 5. 보관 실패 시 삭제하지 않음(보관 폴더가 파일이라 복제 불가) ----------------------------------
mk arcfail feat/arcfail; pr feat/arcfail '[{"number":40,"state":"MERGED"}]'; mkdir -p "$ARC/repoA"; : > "$ARC/repoA/arcfail"
jq -R -s '{ok:true,result:{worktrees:(split("\n")|map(select(length>0)|split("\t")|{id:("repo::"+.[0]),path:.[0],branch:.[1],lastActivityAt:(.[2]|tonumber),isMainWorktree:(.[3]=="true"),childWorktreeIds:[]}))}}' "$SB/reg.tsv" > "$FAKE/worktrees.json"
gc --apply --only arcfail
chk "보관 실패 → 폴더 유지·archive_fail·종료 1" '[ -e "$W/arcfail" ] && has "SKIP archive_fail" && [ "$RC" = 1 ] && [ "$(rm_calls)" = 0 ]' "rc=$RC"

# ---- 6. eol=lf 속성과 혼합 줄바꿈 index 충돌 ------------------------------------------------
LIC=Assets/Sound/s/a.LICENSE.txt
for n in eol_only eol_other eol_content eol_late eol_arcfail eol_noise_late eol_archive_late lic_checkout lic_untracked eol_reason lic_noise; do
  mk "$n" "feat/$n"; pr "feat/$n" '[{"number":50,"state":"MERGED"}]'
  if [ "$n" = lic_checkout ]; then
    printf 'line1\r\nline2\r\n' > "$W/$n/$LIC"
    continue
  fi
  if [ "$n" = lic_untracked ]; then
    : > "$W/$n/Assets/Sound/s/new.LICENSE.txt"
    continue
  fi
  # 속성 적용 전 혼합 줄바꿈을 커밋해야 checkout 후에도 변경이 남는 실제 원인을 재현함.
  printf 'line1\r\nline2\n' > "$W/$n/$LIC"
  git -C "$W/$n" -c core.autocrlf=false add -- "$LIC"; oldc "$n" mixed
  printf '%s text eol=lf\n' "$LIC" > "$W/$n/.gitattributes"
  git -C "$W/$n" add -- .gitattributes; oldc "$n" attributes
  git -C "$W/$n" -c core.autocrlf=false checkout -- "$LIC"
done
echo m > "$W/lic_noise/Assets/Sound/s/new.cs.meta"
mk sub_noise feat/sub_noise; pr feat/sub_noise '[{"number":51,"state":"MERGED"}]'
git -C "$W/sub_noise" -c protocol.file.allow=always submodule add -q "$O/sub.git" Assets/Sub 2>/dev/null
git -C "$W/sub_noise" add -A; oldc sub_noise sub; git -C "$W/sub_noise" push -q origin feat/sub_noise 2>/dev/null
echo m > "$W/sub_noise/Assets/new.cs.meta"
echo edit >> "$W/eol_other/README.md"
echo content >> "$W/eol_content/$LIC"
: > "$ARC/repoA/eol_arcfail"
jq -R -s '{ok:true,result:{worktrees:(split("\n")|map(select(length>0)|split("\t")|{id:("repo::"+.[0]),path:.[0],branch:.[1],lastActivityAt:(.[2]|tonumber),isMainWorktree:(.[3]=="true"),childWorktreeIds:[]}))}}' "$SB/reg.tsv" > "$FAKE/worktrees.json"
chk "재현: core.autocrlf=false checkout 뒤에도 LICENSE 변경 잔존" '[ -n "$(git -C "$W/eol_only" status --porcelain)" ] && git -C "$W/eol_only" ls-files --eol -- "$LIC" | grep -q "i/mixed"'
before=$(git -C "$W/eol_only" hash-object --no-filters -- "$LIC")
gc --only eol_only
chk "줄바꿈 dry-run: 후보이며 원본 불변" 'has "CANDIDATE repoA/eol_only " && [ "$before" = "$(git -C "$W/eol_only" hash-object --no-filters -- "$LIC")" ] && [ "$(rm_calls)" = 0 ]'
FAKE_RM_CHECK_DIRTY=1 gc --apply --only eol_only
chk "줄바꿈만: 일반 rm 거부 뒤 force 삭제 성공" '[ "$RC" = 0 ] && [ ! -e "$W/eol_only" ] && [ "$(rm_calls)" = 2 ] && grep -q -- "--force" "$FAKE/orca.calls"'
chk "줄바꿈만: 삭제 전 결과물 보관" '[ "$(find "$ARC/repoA/eol_only/TestResults" -type f | wc -l | tr -d " ")" = 2 ] && [ -f "$ARC/repoA/eol_only/docs/out/x.html" ]'
FAKE_RM_CHECK_DIRTY=1 gc --apply --only lic_checkout
chk "일반 줄바꿈: checkout 후 force 없이 삭제" '[ "$RC" = 0 ] && [ ! -e "$W/lic_checkout" ] && [ "$(rm_calls)" = 1 ] && ! grep -q -- "--force" "$FAKE/orca.calls"'
for n in eol_other eol_content lic_untracked; do
  before=$(git -C "$W/$n" hash-object --no-filters -- "$LIC")
  FAKE_RM_CHECK_DIRTY=1 gc --apply --only "$n"
  chk "실변경 유지: $n, checkout·삭제 없음" '[ "$RC" = 0 ] && [ -e "$W/$n" ] && [ "$(rm_calls)" = 0 ] && [ "$before" = "$(git -C "$W/$n" hash-object --no-filters -- "$LIC")" ]'
done
FAKE_RM_CHECK_DIRTY=1 FAKE_RM_ADD_EDIT=1 gc --apply --only eol_late
chk "rm 직전 다른 변경 발생: force 금지·보관본 유지" '[ "$RC" = 1 ] && [ -e "$W/eol_late" ] && [ "$(rm_calls)" = 1 ] && ! grep -q -- "--force" "$FAKE/orca.calls" && [ -f "$ARC/repoA/eol_late/TestResults/a.xml" ]'
FAKE_RM_CHECK_DIRTY=1 gc --apply --only eol_arcfail
chk "줄바꿈만·보관 실패: 삭제 시도 없음" '[ "$RC" = 1 ] && [ -e "$W/eol_arcfail" ] && [ "$(rm_calls)" = 0 ]'
FAKE_RM_CHECK_DIRTY=1 FAKE_RM_ADD_NOISE=1 gc --apply --only eol_noise_late
chk "rm 직전 일반 부산물 발생: LICENSE 전용 force 금지" '[ "$RC" = 1 ] && [ -e "$W/eol_noise_late" ] && [ "$(rm_calls)" = 1 ] && ! grep -q -- "--force" "$FAKE/orca.calls"'
FAKE_RM_CHECK_DIRTY=1 FAKE_RM_BREAK_ARCHIVE="$ARC/repoA/eol_archive_late" gc --apply --only eol_archive_late
chk "rm 거부 뒤 보관 파일 수 불일치: force 금지" '[ "$RC" = 1 ] && [ -e "$W/eol_archive_late" ] && [ "$(rm_calls)" = 1 ] && ! grep -q -- "--force" "$FAKE/orca.calls" && has "ARCHIVE_MISMATCH"'

FAKE_RM_CHECK_DIRTY=1 FAKE_RM_REASON="error: worktree is locked" gc --apply --only eol_reason
chk "다른 거부 사유(잠금): force 안 함·rm_fail" '[ "$RC" = 1 ] && [ -e "$W/eol_reason" ] && [ "$(rm_calls)" = 1 ] && ! grep -q -- "--force" "$FAKE/orca.calls" && has "SKIP rm_fail"'
FAKE_RM_CHECK_DIRTY=1 gc --apply --only lic_noise
chk "LICENSE 줄바꿈 + 기존 부산물: force 로 삭제" '[ "$RC" = 0 ] && [ ! -e "$W/lic_noise" ] && [ "$(rm_calls)" = 2 ] && grep -q -- "--force" "$FAKE/orca.calls"'
FAKE_RM_CHECK_DIRTY=1 gc --apply --only sub_noise
chk "서브모듈 + 기존 부산물: 이전처럼 force 삭제" '[ "$RC" = 0 ] && [ ! -e "$W/sub_noise" ] && grep -q -- "--force" "$FAKE/orca.calls"'

echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
