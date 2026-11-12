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
export WS_GC_INBOX="$SB/inbox"   # 허브 색인이 실제 ~/.config 로 새지 않게 격리
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
FAKE="$SB/fake" ROOT="$SB/ws" ARC="$SB/archive" O="$SB/origin" BIN="$SB/bin" W="$ROOT/repoA"
mkdir -p "$FAKE/pr" "$ROOT" "$ARC" "$O" "$BIN" "$W"
ln -s "$HERE/fake_orca.sh" "$BIN/orca"; ln -s "$HERE/fake_gh.sh" "$BIN/gh"
# 가짜 tar: 진짜 tar 로 풀고, FAKE_TAR_CORRUPT=1 이면 푼 파일(FAKE_TAR_CORRUPT_NAME 이름만, 기본 전부)의 첫 byte 를 바꿈(크기 불변 손상 흉내)
cat > "$BIN/tar" <<'EOS'
#!/bin/bash
/usr/bin/tar "$@"; r=$?
if [ "${FAKE_TAR_CORRUPT:-}" = 1 ]; then
  case " $* " in *" -xf "*) d=""; prev=""; for a in "$@"; do [ "$prev" = -C ] && d="$a"; prev="$a"; done
    [ -z "$d" ] || find "$d" -type f -name "${FAKE_TAR_CORRUPT_NAME:-*}" -exec sh -c 'printf Z | dd of="$1" bs=1 count=1 conv=notrunc 2>/dev/null' _ {} \; ;; esac
fi
exit $r
EOS
chmod +x "$BIN/tar"
OLD="2026-01-01T00:00:00" OLD_MS=1767225600000 NOW_MS=$(($(date +%s) * 1000))
: > "$SB/reg.tsv"; : > "$FAKE/terminals.tsv"; : > "$FAKE/workers.tsv"

# ---- 저장소 준비 ---------------------------------------------------------------------------
git init -q --bare -b main "$O/repoA.git"; git init -q -b main "$O/sub.git.work"
printf 'sub\n' > "$O/sub.git.work/s.txt"; git -C "$O/sub.git.work" add -A; git -C "$O/sub.git.work" commit -q -m sub
git clone -q --bare "$O/sub.git.work" "$O/sub.git"
SEED="$SB/seed"; git clone -q "$O/repoA.git" "$SEED" 2>/dev/null
mkdir -p "$SEED/Assets/Sound/s"
printf 'TestResults/\ndocs/out/\nLibrary/\n*.html\n*.mp4\nsecret/\n.dart_tool/\n.idea/\nobj/\n' > "$SEED/.gitignore"
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
  mkdir -p "$W/$n/TestResults" "$W/$n/docs/out" "$W/$n/Library/Cache" "$W/$n/.dart_tool"; echo d > "$W/$n/.dart_tool/x"
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
  env PATH="$BIN:$PATH" FAKE_DIR="${GC_FAKE:-$FAKE}" FAKE_ROOT="$ROOT" FAKE_RM_REFUSE="sub_clean sub_noise" FAKE_GH_FAIL="feat/ghf" WS_GC_NOTIFY="$HERE/fake_send.sh" \
    bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$ARC/ws_gc.log" "$@" > "$SB/out" 2>&1
  RC=$?
}
has() { grep -q -- "$1" "$ARC/ws_gc.log"; }
rm_calls() { grep -c '^worktree rm' "$FAKE/orca.calls" || true; }
CAND="merged noise worker_idle nopr_pushed det_ok sub_clean"       # 삭제 후보(최근·허브 등은 아님)
SKIPS="closed_local:unmerged dirty:dirty dirty_lic:dirty recent:idle coord-hub:hub live:live worker_active:live pr_open:pr_open nopr_local:unmerged det_local:unmerged prot:protected ghfail:gh_fail sub_dirty:submodule orphan_dir:not_orca"

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
chk "dry-run: 보관 폴더에 결과물 없음" '[ -z "$(find "$ARC" -type f ! -name ws_gc.log ! -name .ws_gc.flock | head -1)" ]'
for n in $CAND; do chk "dry-run 후보: $n" 'has "CANDIDATE repoA/$n "'; done
for e in $SKIPS; do n="${e%%:*}" r="${e##*:}"; chk "dry-run 건너뜀: $n ($r)" 'has "SKIP $r .*$n"'; done
chk "dry-run: 메인 체크아웃은 평가 대상 아님" '! has "repoA/mainco"'
chk "dry-run: 부산물 근거 로그(.meta·줄바꿈만)" 'has "NOISE   부산물 Assets/X/new.cs.meta" && has "NOISE   줄바꿈만 Assets/Sound/s/a.LICENSE.txt"'
chk "dry-run: 진짜 LICENSE 변경은 dirty" 'has "SKIP dirty repoA/dirty_lic"'
chk "dry-run: 요약 줄 기록" 'has "END 삭제 후보 6개"'
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
chk "apply: rm 호출 = 후보 수 + 서브모듈 강제 1" '[ "$(rm_calls)" = 7 ]' "calls=$(rm_calls)"
chk "apply: 서브모듈 거부 → --force 재시도" 'grep -q "^worktree rm --worktree path:$W/sub_clean --force" "$FAKE/orca.calls" && has "RM_RETRY_FORCE"'
chk "apply: 서브모듈 거부된 sub_dirty 는 rm 시도 자체 없음" '! grep -q "sub_dirty" "$FAKE/orca.calls"'
chk "apply: 브랜치 삭제 옵션 없음" '! grep -q -- "--delete-branch" "$FAKE/orca.calls"'
chk "apply: TestResults 보관(파일 수 일치)" '[ "$(find "$ARC/repoA/merged/TestResults" -type f | wc -l | tr -d " ")" = 2 ]'
chk "apply: docs/out·html 보관" '[ -f "$ARC/repoA/merged/docs/out/x.html" ]'
chk "apply: gitignore 캐시(Library·.dart_tool)는 보관 안 함" '[ ! -e "$ARC/repoA/merged/Library" ] && [ ! -e "$ARC/repoA/merged/.dart_tool" ]'
chk "apply: 보관 로그에 파일 수 대조" 'has "ARCHIVE TestResults 파일 2"'
chk "apply: 쉬는 워커 worker-release" 'grep -q "^orchestration worker-release --dispatch d_idle" "$FAKE/orca.calls"'
chk "apply: 활동 중 워커는 release 안 함" '! grep -q "d_active" "$FAKE/orca.calls"'
chk "apply: 삭제 로그에 브랜치 유지 표기" 'has "DELETE repoA/merged .*브랜치는 유지"'
chk "apply: 요약·Slack(삭제 7개)" 'has "END 삭제 6개" && grep -q "자동 정리 완료" "$FAKE/notify.calls" && grep -q "삭제 6개" "$FAKE/notify.calls"'
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
for n in eol_only eol_other eol_content eol_late eol_arcfail eol_noise_late eol_archive_late lic_checkout lic_untracked eol_reason eol_real eol_lock_path lic_noise; do
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
  git -C "$W/$n" push -q origin "feat/$n" 2>/dev/null
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
# 실측 문구(경로 뒤 git status 짧은 형식 목록)도 미커밋 변경 계열로 인정, 같은 형식이 아닌 사유는 force 금지
REAL_MSG='{"ok":false,"error":{"message":"Failed to delete worktree at @P@. M Assets/RawAssets/Local/Sound/Sfx/VendingInterface.LICENSE.txt"}}'
FAKE_RM_REASON="$REAL_MSG" gc --apply --only eol_real
chk "실측 거부 문구(경로 뒤 'M 경로'): force 재시도로 삭제" '[ "$RC" = 0 ] && [ ! -e "$W/eol_real" ] && [ "$(rm_calls)" = 2 ] && grep -q -- "--force" "$FAKE/orca.calls" && has "RM_RETRY_FORCE"'
FAKE_RM_REASON='{"ok":false,"error":{"message":"Failed to delete worktree at @P@. a b worktree is locked"}}' gc --apply --only eol_lock_path
chk "경로 뒤 소문자 문장(잠금): force 금지" '[ "$RC" = 1 ] && [ -e "$W/eol_lock_path" ] && [ "$(rm_calls)" = 1 ] && ! grep -q -- "--force" "$FAKE/orca.calls"'
FAKE_RM_CHECK_DIRTY=1 gc --apply --only lic_noise
chk "LICENSE 줄바꿈 + 기존 부산물: force 로 삭제" '[ "$RC" = 0 ] && [ ! -e "$W/lic_noise" ] && [ "$(rm_calls)" = 2 ] && grep -q -- "--force" "$FAKE/orca.calls"'
FAKE_RM_CHECK_DIRTY=1 gc --apply --only sub_noise
chk "서브모듈 + 기존 부산물: 이전처럼 force 삭제" '[ "$RC" = 0 ] && [ ! -e "$W/sub_noise" ] && grep -q -- "--force" "$FAKE/orca.calls"'

# ---- 7. 결과물 허용 목록: 목록 안(미추적·gitignore)만 보관 뒤 삭제, 그 밖은 "미확인"이라 보존 ---------------
regen() { # reg.tsv → worktrees.json. 허브 하위 연결은 HUB_KIDS="이름 …" 로 coord-q-hub 에 붙임
  jq -R -s '{ok:true,result:{worktrees:(split("\n")|map(select(length>0)|split("\t")|{id:("repo::"+.[0]),path:.[0],branch:.[1],lastActivityAt:(.[2]|tonumber),isMainWorktree:(.[3]=="true"),childWorktreeIds:[]}))}}' "$SB/reg.tsv" \
    | jq --arg h "$W/coord-q-hub" --arg kids "${HUB_KIDS:-}" --arg w "$W" '.result.worktrees |= map(if .path == $h then .childWorktreeIds = ($kids | split(" ") | map(select(length > 0) | "repo::" + $w + "/" + .)) else . end)' > "$FAKE/worktrees.json"
}
addtracked() { # 이름 경로 내용: 추적 파일을 옛 커밋으로 만들어 origin 에 올림
  mkdir -p "$W/$1/$(dirname "$2")"; printf '%s\n' "$3" > "$W/$1/$2"; git -C "$W/$1" add -- "$2"; oldc "$1" "add $2"; git -C "$W/$1" push -q origin "feat/$1" 2>/dev/null
}
mk coord-q-hub feat/qhub; pr feat/qhub '[{"number":60,"state":"MERGED"}]'
NEW="out_only out_arcfail out_json out_subdir code_untracked tracked_png assets_png ignored_code ignored_deep tracked_noise assets_tr root_cs spaces corrupt old_pr_new_commit merged_gone live_nw"
for n in $NEW; do mk "$n" "feat/$n"; pr "feat/$n" '[{"number":61,"state":"MERGED"}]'; done
for n in out_only out_arcfail; do
  mkdir -p "$W/$n/evidence"; echo md > "$W/$n/summary.md"; echo png > "$W/$n/a.png"; echo log > "$W/$n/run.log"; echo r > "$W/$n/evidence/r.png"
done
echo '{}' > "$W/out_json/result.json"; echo md > "$W/out_json/summary.md"                  # json 은 허용 목록 밖
mkdir -p "$W/out_subdir/reports"; echo md > "$W/out_subdir/reports/s.md"                  # 루트·결과물 폴더 밖의 md
mkdir -p "$W/code_untracked/src"; echo 'class A{}' > "$W/code_untracked/src/Foo.cs"; echo md > "$W/code_untracked/report.md"
echo edit >> "$W/tracked_png/README.md"; echo png > "$W/tracked_png/a.png"
mkdir -p "$W/assets_png/Assets/Art"; echo png > "$W/assets_png/Assets/Art/new.png"
# [1] gitignore 코드 파일(secret/ 는 .gitignore 대상)
mkdir -p "$W/ignored_code/secret"; echo 'class S{}' > "$W/ignored_code/secret/Foo.cs"
mkdir -p "$W/ignored_deep/pages"; echo h > "$W/ignored_deep/pages/deep.html"              # *.html 은 ignore 지만 루트가 아님
# [2] 추적 파일 수정: 폴더 이름이 TextMesh Pro 여도 부산물이 아님
addtracked tracked_noise "TextMesh Pro/Logic.cs" "class L{}"; echo 'class L{ int x; }' > "$W/tracked_noise/TextMesh Pro/Logic.cs"
# [7] Assets 아래 TestResults 의 .CS·png / 루트 .CS
mkdir -p "$W/assets_tr/Assets/TestResults"; echo c > "$W/assets_tr/Assets/TestResults/Foo.CS"; echo p > "$W/assets_tr/Assets/TestResults/texture.png"
echo c > "$W/root_cs/Foo.CS"
# 공백·따옴표 경로
mkdir -p "$W/spaces/evidence/my reports"; echo md > "$W/spaces/evidence/my reports/a'b.md"
# [3] 보관 손상(크기 불변): 미추적 파일 + gitignore 폴더(docs/out)
echo md > "$W/corrupt/r.md"
# [6] PR 병합 뒤 push 안 한 커밋 / 원격 브랜치가 지워졌지만 HEAD 가 병합된 PR 의 마지막 커밋
echo more > "$W/old_pr_new_commit/g.txt"; git -C "$W/old_pr_new_commit" add -A; oldc old_pr_new_commit unpushed
git -C "$W/merged_gone" update-ref -d refs/remotes/origin/feat/merged_gone; pr feat/merged_gone "[{\"number\":62,\"state\":\"MERGED\",\"headRefOid\":\"$(git -C "$W/merged_gone" rev-parse HEAD)\"}]"
HUB_KIDS="out_only out_arcfail"; regen
mkdir -p "$ARC/repoA"; : > "$ARC/repoA/out_arcfail"          # 보관 폴더 자리에 파일이 있어 복제 불가
jsonrun() { env PATH="$BIN:$PATH" FAKE_DIR="$FAKE" FAKE_ROOT="$ROOT" bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$ARC/ws_gc.log" --json --no-slack "$@" 2>/dev/null; }
untouched() { for n in "$@"; do [ -e "$W/$n" ] || return 1; done; [ "$(rm_calls)" = 0 ]; }

gc --only out_only; chk "결과물만: dry-run 후보·결과물 수 표시" 'has "CANDIDATE repoA/out_only .*결과물=4" && has "summary.md"'
chk "결과물만: dry-run 은 삭제·색인 없음" '[ -e "$W/out_only" ] && [ ! -e "$SB/inbox/q.tsv" ]'
JS=$(jsonrun --only out_only)
chk "--json: 결과물만 후보 수·items" 'echo "$JS" | jq -e ".candidates_outputs_only == 1 and (.items | length) == 1 and .items[0].verdict == \"candidate\"" >/dev/null' "$JS"
gc --only code_untracked --apply; chk "허용 목록 밖 미추적(src/Foo.cs): 보존·KEEP_CODE 기록" '[ -e "$W/code_untracked" ] && has "SKIP dirty repoA/code_untracked" && has "KEEP_CODE repoA/code_untracked 추적 수정 0·미확인 1: 미확인 미추적 src/Foo.cs" && [ "$(rm_calls)" = 0 ]'
JS=$(jsonrun --only code_untracked)
chk "--json: keep_code 에 이름·파일" 'echo "$JS" | jq -e ".keep_code[0].name == \"repoA/code_untracked\" and .keep_code[0].unverified == 1 and (.keep_code[0].files[0] | test(\"Foo.cs\"))" >/dev/null' "$JS"
gc --only tracked_png --apply; chk "추적 파일 수정 + 결과물: 보존" '[ -e "$W/tracked_png" ] && has "KEEP_CODE repoA/tracked_png 추적 수정 1·미확인 0" && [ "$(rm_calls)" = 0 ]'
gc --only assets_png --apply; chk "Assets 아래 미추적 png(새 에셋 가능): 보존" 'untouched assets_png && has "SKIP dirty repoA/assets_png"'
gc --only out_json --apply; chk "루트 json 은 허용 목록 밖 → 보존" 'untouched out_json && has "미확인 미추적 result.json"'
gc --only out_subdir --apply; chk "결과물 폴더 밖 하위 폴더의 md → 보존" 'untouched out_subdir && has "미확인 미추적 reports/s.md"'

# [1] gitignore 된 코드 파일
gc --only ignored_code --apply; chk "[1] gitignore 코드(secret/Foo.cs): 보존·삭제 시도 없음" 'untouched ignored_code && [ -e "$W/ignored_code/secret/Foo.cs" ] && has "미확인 무시파일 secret/Foo.cs" && [ "$RC" = 0 ]'
gc --only ignored_deep --apply; chk "[1] gitignore *.html 이어도 루트가 아니면 보존" 'untouched ignored_deep && has "미확인 무시파일 pages/deep.html"'
# [2] 추적 파일 수정
FAKE_RM_CHECK_DIRTY=1 gc --only tracked_noise --apply
chk "[2] 추적 파일 수정(TextMesh Pro/Logic.cs): 보존, force 삭제 없음" 'untouched tracked_noise && has "추적 수정 TextMesh Pro/Logic.cs" && ! grep -q -- "--force" "$FAKE/orca.calls"'
# [7] 대소문자·소스 폴더
gc --only assets_tr --apply; chk "[7] Assets/TestResults/Foo.CS·png: 소스 폴더라 허용 목록과 겹쳐도 보존" 'untouched assets_tr && has "미확인 무시파일 Assets/TestResults/"'
gc --only root_cs --apply; chk "[7] 루트 Foo.CS: 허용 확장자가 아니라 보존" 'untouched root_cs && has "미확인 미추적 Foo.CS"'
# [6] HEAD 가 원격에 있어야 함
gc --only old_pr_new_commit --apply; chk "[6] PR 병합 뒤 push 안 한 커밋: 보존(unmerged)" 'untouched old_pr_new_commit && has "SKIP unmerged repoA/old_pr_new_commit — PR #61 MERGED 이후 origin 에 없는 커밋 있음"'
gc --only merged_gone --apply; chk "[6] 원격 브랜치가 지워져도 HEAD 가 병합된 PR 의 마지막 커밋이면 삭제" '[ ! -e "$W/merged_gone" ] && [ "$RC" = 0 ]'
# 공백·따옴표 경로
FAKE_RM_CHECK_DIRTY=1 gc --only spaces --apply
chk "공백·따옴표 경로(evidence/my reports/a'b.md): 보관 대조 뒤 삭제" '[ "$RC" = 0 ] && [ ! -e "$W/spaces" ] && [ "$(cat "$ARC/repoA/spaces/evidence/my reports/a'"'"'b.md")" = md ]'
# [3] 보관 내용 손상(파일 수·크기 같음)
FAKE_TAR_CORRUPT=1 FAKE_TAR_CORRUPT_NAME=x.html FAKE_RM_CHECK_DIRTY=1 gc --only corrupt --apply
chk "[3] gitignore 결과물 폴더(docs/out) 손상(크기 불변): 삭제 중단·종료 1" '[ "$RC" = 1 ] && untouched corrupt && has "ARCHIVE_MISMATCH docs/out" && has "SKIP archive_fail repoA/corrupt" && ! grep -q "corrupt" "$SB/inbox/_none.tsv"'
FAKE_TAR_CORRUPT=1 FAKE_TAR_CORRUPT_NAME=r.md FAKE_RM_CHECK_DIRTY=1 gc --only corrupt --apply
chk "[3] 미추적 파일(r.md) 손상(크기 불변): 삭제 중단·종료 1" '[ "$RC" = 1 ] && untouched corrupt && has "ARCHIVE_MISMATCH r.md 원본과 보관본의 내용이 다름"'
FAKE_RM_CHECK_DIRTY=1 gc --only corrupt --apply
chk "[3] 정상 복제로 재시도하면 삭제" '[ "$RC" = 0 ] && [ ! -e "$W/corrupt" ] && [ "$(cat "$ARC/repoA/corrupt/r.md")" = md ] && [ -f "$ARC/repoA/corrupt/docs/out/x.html" ]'

# 결과물만: 보관·색인·삭제
FAKE_RM_CHECK_DIRTY=1 gc --only out_only --apply
chk "결과물만: 삭제 성공(일반 rm 거부 → 보관 확인 뒤 force)" '[ "$RC" = 0 ] && [ ! -e "$W/out_only" ] && grep -q "^worktree rm --worktree path:$W/out_only --force" "$FAKE/orca.calls" && has "RM_RETRY_FORCE"'
chk "결과물만: 미추적 결과물 보관(내용 일치)" '[ "$(cat "$ARC/repoA/out_only/summary.md")" = md ] && [ "$(cat "$ARC/repoA/out_only/a.png")" = png ] && [ -f "$ARC/repoA/out_only/run.log" ] && [ -f "$ARC/repoA/out_only/evidence/r.png" ]'
chk "결과물만: gitignore 결과물도 함께 보관" '[ "$(find "$ARC/repoA/out_only/TestResults" -type f | wc -l | tr -d " ")" = 2 ] && [ -f "$ARC/repoA/out_only/docs/out/x.html" ] && has "ARCHIVE summary.md 파일 1" && [ ! -e "$ARC/repoA/out_only/Library" ]'
chk "결과물만: 허브 색인 한 줄(시각·이름·보관 경로·파일 수)" '[ "$(wc -l < "$SB/inbox/q.tsv" | tr -d " ")" = 1 ] && awk -F"\t" "NF==4 && \$1 ~ /^20[0-9-]+T/ && \$2 == \"repoA/out_only\" && \$3 == \"$ARC/repoA/out_only\" && \$4 == 7" "$SB/inbox/q.tsv" | grep -q .' "$(cat "$SB/inbox/q.tsv" 2>&1)"
chk "부모 없는 작업 공간은 _none 색인" 'grep -q "repoA/merged" "$SB/inbox/_none.tsv"'
chk "결과물만: 요약에 결과물만 개수" 'has "END 삭제 1개(결과물만 1개)"'
FAKE_RM_CHECK_DIRTY=1 gc --only out_arcfail --apply
chk "보관 불일치(복제 불가): 삭제 안 함·색인 없음·종료 1" '[ "$RC" = 1 ] && untouched out_arcfail && has "ARCHIVE_MISMATCH" && has "SKIP archive_fail repoA/out_arcfail" && [ "$(wc -l < "$SB/inbox/q.tsv" | tr -d " ")" = 1 ]'
chk "실패 Slack: 처음엔 전송" '[ -s "$FAKE/notify.calls" ] && grep -q "삭제 못 한 곳 1곳" "$FAKE/notify.calls"'
FAKE_RM_CHECK_DIRTY=1 gc --only out_arcfail --apply
chk "같은 실패 반복: Slack 다시 안 보냄" '[ "$RC" = 1 ] && [ ! -s "$FAKE/notify.calls" ]'
gc --parent "$W/coord-q-hub" --only out_arcfail --dry-run; chk "--parent: 그 허브 하위만 평가" 'has "parent=$W/coord-q-hub" && has "repoA/out_arcfail"'
JS=$(jsonrun --parent "$W/coord-q-hub"); chk "--parent: 허브 하위 2곳만 items(다른 허브·무소속 제외)" 'echo "$JS" | jq -e "[.items[].name] | sort == [\"repoA/out_arcfail\", \"repoA/out_only\"]" >/dev/null' "$JS"

# ---- 8. 재리뷰: 캐시 폴더 이름만 믿지 않음 / 추적 수정은 부산물 예외보다 우선 / 서브모듈 안 gitignore 코드 ----
for n in cache_lib cache_idea cache_obj cache_gen tracked_sdf tracked_meta sub_ignored sub_untracked; do mk "$n" "feat/$n"; pr "feat/$n" '[{"number":70,"state":"MERGED"}]'; done
# [P1-1] 캐시 폴더(Library·.idea·obj) 안의 유일본 소스·자산
mkdir -p "$W/cache_lib/Library"; echo 'class O{}' > "$W/cache_lib/Library/OnlyCopy.cs"
mkdir -p "$W/cache_idea/.idea"; echo 'class O{}' > "$W/cache_idea/.idea/OnlyCopy.cs"
mkdir -p "$W/cache_obj/obj"; echo 'class O{}' > "$W/cache_obj/obj/OnlyCopy.cs"
# 도구가 만든 하위 경로(PackageCache·Library 바로 아래 .asset·obj AssemblyInfo)만 있으면 캐시로 보고 삭제
mkdir -p "$W/cache_gen/Library/PackageCache/pkg" "$W/cache_gen/obj/Debug"; echo 'class P{}' > "$W/cache_gen/Library/PackageCache/pkg/Foo.cs"; echo a > "$W/cache_gen/Library/EditorUserBuildSettings.asset"; echo 'x' > "$W/cache_gen/obj/Debug/App.AssemblyInfo.cs"
# [P1-2] 추적 파일 수정은 .meta·SDF 부산물 규칙보다 우선
addtracked tracked_sdf "Assets/Fonts/GameSDFState.asset" "state: 1"; echo "state: 2" > "$W/tracked_sdf/Assets/Fonts/GameSDFState.asset"
addtracked tracked_meta "Assets/Foo.cs.meta" "guid: aaaa"; echo "guid: bbbb" > "$W/tracked_meta/Assets/Foo.cs.meta"
# [P1-3] 서브모듈 안 gitignore·미추적 코드
for n in sub_ignored sub_untracked; do
  git -C "$W/$n" -c protocol.file.allow=always submodule add -q "$O/sub.git" Assets/Sub 2>/dev/null; git -C "$W/$n" add -A; oldc "$n" sub; git -C "$W/$n" push -q origin "feat/$n" 2>/dev/null
done
mkdir -p "$W/sub_ignored/Assets/Sub/secret"; echo 'class O{}' > "$W/sub_ignored/Assets/Sub/secret/OnlyCopy.cs"
printf 'secret/\n' >> "$(git -C "$W/sub_ignored/Assets/Sub" rev-parse --git-path info/exclude)"
mkdir -p "$W/sub_untracked/Assets/Sub/src"; echo 'class O{}' > "$W/sub_untracked/Assets/Sub/src/OnlyCopy.cs"
regen
for n in cache_lib cache_idea cache_obj; do
  FAKE_RM_CHECK_DIRTY=1 gc --only "$n" --apply
  chk "[P1-1] 캐시 폴더 안 소스(OnlyCopy.cs): $n 보존·삭제 시도 없음" 'untouched "$n" && has "SKIP dirty repoA/$n" && has "미확인 무시파일 .*OnlyCopy.cs" && [ "$RC" = 0 ]'
done
chk "[P1-1] 보존한 파일이 그대로 남음" '[ -f "$W/cache_lib/Library/OnlyCopy.cs" ] && [ -f "$W/cache_idea/.idea/OnlyCopy.cs" ] && [ -f "$W/cache_obj/obj/OnlyCopy.cs" ]'
gc --only cache_gen; chk "[P1-1] 도구 생성 하위 경로만 든 캐시: 후보(과잉 보존 방지)" 'has "CANDIDATE repoA/cache_gen "'
for n in tracked_sdf tracked_meta; do
  FAKE_RM_CHECK_DIRTY=1 gc --only "$n" --apply
  chk "[P1-2] 추적 파일 수정($n): 보존, force·rm 없음" 'untouched "$n" && has "SKIP dirty repoA/$n" && has "추적 수정 1" && ! grep -q -- "--force" "$FAKE/orca.calls"'
done
for n in sub_ignored sub_untracked; do
  FAKE_RM_CHECK_DIRTY=1 gc --only "$n" --apply
  chk "[P1-3] 서브모듈 안 코드($n): 보존, rm·force 없음" 'untouched "$n" && has "SKIP submodule repoA/$n" && ! grep -q -- "--force" "$FAKE/orca.calls"'
done
chk "[P1-3] 서브모듈 안 파일이 그대로 남음" '[ -f "$W/sub_ignored/Assets/Sub/secret/OnlyCopy.cs" ] && [ -f "$W/sub_untracked/Assets/Sub/src/OnlyCopy.cs" ]'

# [4] 워커 목록이 비어도 연결된 터미널이 있는 곳은 살아 있음
GC_FAKE="$SB/fake2"; mkdir -p "$GC_FAKE/pr"; cp "$FAKE"/*.json "$GC_FAKE/"; cp "$FAKE/pr"/* "$GC_FAKE/pr/"
jq -n --arg p "$W/live_nw" '{ok:true,result:{terminals:[{handle:"term_nw",worktreePath:$p,connected:true,orphaned:false}]}}' > "$GC_FAKE/terminals.json"
jq -n '{ok:true,result:{workers:[],page:{hasMore:false}}}' > "$GC_FAKE/workers.json"
gc --only live_nw --apply; chk "[4] 워커 목록이 비었고 터미널이 붙은 곳: 보존(live)" '[ -e "$W/live_nw" ] && has "SKIP live repoA/live_nw" && ! grep -q "^worktree rm" "$GC_FAKE/orca.calls"' "$(tail -3 "$ARC/ws_gc.log")"
unset GC_FAKE

# [5] 잠금: 동시에 4개를 띄우면 하나만 실행(나머지는 건너뜀), 끝난 뒤에는 다시 실행됨
mkdir -p "$SB/par"
for i in 1 2 3 4; do (FAKE_SLEEP=2 env PATH="$BIN:$PATH" FAKE_DIR="$FAKE" FAKE_ROOT="$ROOT" WS_GC_NOTIFY="$HERE/fake_send.sh" bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$SB/par/log" --dry-run --only nothing_matches > "$SB/par/out$i" 2>&1 &); done
sleep 5
chk "[5] 동시 실행 4개: 하나만 실행" '[ "$(grep -c "START" "$SB/par/log")" = 1 ] && [ "$(cat "$SB"/par/out* | grep -c "이미 실행 중")" = 3 ]' "start=$(grep -c START "$SB/par/log") skipped=$(cat "$SB"/par/out* | grep -c "이미 실행 중")"
gc --only nothing_matches; chk "[5] 끝난 뒤 다시 실행 가능(잠금이 남지 않음)" '[ "$RC" = 0 ] && has "START"'
chk "[5] 잠금은 pid 파일·잠금 폴더가 아님" '[ ! -d "$ARC/.ws_gc.lock" ] && [ -f "$ARC/.ws_gc.flock" ]'

echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
