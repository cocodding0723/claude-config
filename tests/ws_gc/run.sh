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
export LRM_CLOSED_HUBS="$SB/closed_hubs.txt"
export WS_GC_HUB_REOPEN_GRACE_MIN=10
export WS_GC_PLAIN_ROOTS="$SB/plain"   # 실제 ~/orca/claude-config-wt 를 보지 않게 격리
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
# 다시 쓰이는 추적 파일(Unity·Firebase 리졸버): 내용 대조 시험용
mkdir -p "$SEED/Assets/Plugins/Android" "$SEED/Assets/X" "$SEED/ProjectSettings"
printf '<manifest>\n        <data android:scheme="${applicationId}" />\n  <uses-permission android:name="a" />\n</manifest>\n' > "$SEED/Assets/Plugins/Android/AndroidManifest.xml"
printf 'fileFormatVersion: 2\nguid: 174513c6b92d84e38b254f1f7d3c97ef\n  spriteMode: 2\n' > "$SEED/Assets/X/pic.png.meta"
printf 'PlayerSettings:\n  companyName: Basakansoft\n  productName: LRM\n' > "$SEED/ProjectSettings/ProjectSettings.asset"
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
  mkdir -p "$W/$n/TestResults" "$W/$n/docs/out" "$W/$n/Library/Bee" "$W/$n/.dart_tool"; echo d > "$W/$n/.dart_tool/x"
  echo r1 > "$W/$n/TestResults/a.xml"; echo r2 > "$W/$n/TestResults/b.xml"; echo h > "$W/$n/docs/out/x.html"; echo c > "$W/$n/Library/Bee/c.bin"
  reg "$n" "refs/heads/$b" "$([ "$when" = recent ] && echo $NOW_MS || echo $OLD_MS)"
}
reg() { printf '%s\t%s\t%s\t%s\n' "$W/$1" "$2" "$3" "${4:-false}" >> "$SB/reg.tsv"; }
pr() { printf '%s\n' "$2" > "$FAKE/pr/$(printf '%s' "$1" | tr / _).json"; }
detach() { git -C "$W/$1" checkout -q --detach; perl -pi -e 's#^(\Q'"$W/$1"'\E)\trefs/heads/[^\t]*#$1\t#' "$SB/reg.tsv"; }  # 등록부 branch 도 비움(빈 칸이 열을 밀지 않는지 확인)

mk merged feat/merged;          pr feat/merged '[{"number":11,"state":"MERGED"}]'
mk closed_local feat/closed local; pr feat/closed '[{"number":12,"state":"CLOSED"}]'
mk noise feat/noise;            pr feat/noise '[{"number":13,"state":"MERGED"}]'
  echo m > "$W/noise/Assets/Sound/s.meta"  # 추적 중인 폴더의 .meta(원본 추적 → 부산물)
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
  if ! [[ " $* " = *" --apply "* ]]; then
    cat "$SB/out" >> "$ARC/ws_gc.log"
  fi
}
has() { grep -q -- "$1" "$ARC/ws_gc.log"; }
rm_calls() { grep -c '^worktree rm' "$FAKE/orca.calls" || true; }
CAND="merged noise worker_idle nopr_pushed det_ok sub_clean closed_local nopr_local"  # 삭제 후보. 로컬 브랜치가 남는 미병합(closed_local·nopr_local)도 포함
SKIPS="dirty:dirty dirty_lic:dirty recent:idle coord-hub:hub live:live worker_active:live pr_open:pr_open det_local:unmerged prot:protected ghfail:gh_fail sub_dirty:submodule orphan_dir:not_orca"

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
chk "dry-run: 보관 폴더에 결과물 없음" '[ -z "$(find "$ARC" -type f ! -name ws_gc.log ! -name "*.flock" | head -1)" ]'
for n in $CAND; do chk "dry-run 후보: $n" 'has "CANDIDATE repoA/$n "'; done
for e in $SKIPS; do n="${e%%:*}" r="${e##*:}"; chk "dry-run 건너뜀: $n ($r)" 'has "SKIP $r .*$n"'; done
chk "dry-run: 메인 체크아웃은 평가 대상 아님" '! has "repoA/mainco"'
chk "dry-run: 부산물 근거 로그(.meta·줄바꿈만)" 'has "NOISE   부산물 Assets/Sound/s.meta" && has "NOISE   줄바꿈만 Assets/Sound/s/a.LICENSE.txt"'
chk "dry-run: 진짜 LICENSE 변경은 dirty" 'has "SKIP dirty repoA/dirty_lic"'
chk "dry-run: 요약 줄 기록" 'has "END 삭제 후보 8개"'
chk "dry-run: Slack 전송 없음" '[ ! -s "$FAKE/notify.calls" ]'
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
chk "apply: rm 호출 = 후보 수 + 서브모듈 강제 1" '[ "$(rm_calls)" = 9 ]' "calls=$(rm_calls)"
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
chk "apply: 요약·Slack(삭제 9개)" 'has "END 삭제 8개" && grep -q "자동 정리 완료" "$FAKE/notify.calls" && grep -q "삭제 8개" "$FAKE/notify.calls"'
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
echo m > "$W/lic_noise/Assets/Sound.meta"
mk sub_noise feat/sub_noise; pr feat/sub_noise '[{"number":51,"state":"MERGED"}]'
git -C "$W/sub_noise" -c protocol.file.allow=always submodule add -q "$O/sub.git" Assets/Sub 2>/dev/null
git -C "$W/sub_noise" add -A; oldc sub_noise sub; git -C "$W/sub_noise" push -q origin feat/sub_noise 2>/dev/null
echo m > "$W/sub_noise/Assets/Sound.meta"
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
gc --only old_pr_new_commit --apply; chk "[6] PR 병합 뒤 push 안 한 커밋: 로컬 브랜치가 남으므로 공간만 삭제·브랜치 유지" '[ ! -e "$W/old_pr_new_commit" ] && has "DELETE repoA/old_pr_new_commit .*미병합(브랜치 유지)" && ! grep -q -- "--delete-branch" "$FAKE/orca.calls"'
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
for n in cache_lib cache_idea cache_obj cache_kt cache_mp4 cache_gen tracked_sdf tracked_meta sub_ignored sub_untracked; do mk "$n" "feat/$n"; pr "feat/$n" '[{"number":70,"state":"MERGED"}]'; done
# [P1-1] 캐시 폴더(Library·.idea·obj) 안의 유일본 소스·자산
mkdir -p "$W/cache_lib/Library"; echo 'class O{}' > "$W/cache_lib/Library/OnlyCopy.cs"
mkdir -p "$W/cache_idea/.idea"; echo 'class O{}' > "$W/cache_idea/.idea/OnlyCopy.cs"
mkdir -p "$W/cache_obj/obj"; echo 'class O{}' > "$W/cache_obj/obj/OnlyCopy.cs"
# 3차: 보존 확장자 목록 밖(.kt·.mp4)이어도 생성 목록에 없으면 보존
mkdir -p "$W/cache_kt/.idea"; echo 'class K' > "$W/cache_kt/.idea/OnlyCopy.kt"
mkdir -p "$W/cache_mp4/obj"; echo v > "$W/cache_mp4/obj/review.mp4"
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
for n in cache_lib cache_idea cache_obj cache_kt cache_mp4; do
  FAKE_RM_CHECK_DIRTY=1 gc --only "$n" --apply
  chk "[P1-1] 캐시 폴더 안 소스(OnlyCopy.cs): $n 보존·삭제 시도 없음" 'untouched "$n" && has "SKIP dirty repoA/$n" && has "미확인 무시파일 .*OnlyCopy.cs" && [ "$RC" = 0 ]'
done
chk "[P1-1] 보존한 파일이 그대로 남음" '[ -f "$W/cache_lib/Library/OnlyCopy.cs" ] && [ -f "$W/cache_idea/.idea/OnlyCopy.cs" ] && [ -f "$W/cache_obj/obj/OnlyCopy.cs" ] && [ -f "$W/cache_kt/.idea/OnlyCopy.kt" ] && [ -f "$W/cache_mp4/obj/review.mp4" ]'
JS=$(jsonrun --only cache_kt); chk "[3차] .idea/OnlyCopy.kt: --json 후보 0·keep_code 에 파일" 'echo "$JS" | jq -e ".candidates == 0 and (.keep_code[0].files[0] | test(\"OnlyCopy.kt\"))" >/dev/null' "$JS"
JS=$(jsonrun --only cache_mp4); chk "[3차] obj/review.mp4: --json 후보 0·keep_code 에 파일" 'echo "$JS" | jq -e ".candidates == 0 and (.keep_code[0].files[0] | test(\"review.mp4\"))" >/dev/null' "$JS"
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

# [R] 다시 쓰이는 추적 파일: 바뀐 줄이 허용 형식이면 부산물(되돌린 뒤 삭제), 하나라도 밖이면 보존
MAN=Assets/Plugins/Android/AndroidManifest.xml
for n in regen_man regen_man_bad regen_meta regen_meta_bad regen_slnx regen_company regen_product; do mk "$n" "feat/$n"; pr "feat/$n" '[{"number":60,"state":"MERGED"}]'; done
perl -pi -e 's/\$\{applicationId\}/com.basakansoft.loverevolutionmaker/' "$W/regen_man/$MAN"
perl -pi -e 's/\$\{applicationId\}/com.x/; s#<uses-permission android:name="a" />#<uses-permission android:name="b" />#' "$W/regen_man_bad/$MAN"
perl -pi -e 's/^guid: .*/guid: c0ba01b6b63654dae912dd8773371ac5/' "$W/regen_meta/Assets/X/pic.png.meta"
perl -pi -e 's/spriteMode: 2/spriteMode: 1/' "$W/regen_meta_bad/Assets/X/pic.png.meta"
echo sln > "$W/regen_slnx/LoveRevolutionMaker.slnx"
perl -pi -e 's/companyName: Basakansoft/companyName: Basakansoft-smoke-stack/' "$W/regen_company/ProjectSettings/ProjectSettings.asset"
perl -pi -e 's/productName: LRM/productName: LRM2/' "$W/regen_product/ProjectSettings/ProjectSettings.asset"
jq -R -s '{ok:true,result:{worktrees:(split("\n")|map(select(length>0)|split("\t")|{id:("repo::"+.[0]),path:.[0],branch:.[1],lastActivityAt:(.[2]|tonumber),isMainWorktree:(.[3]=="true"),childWorktreeIds:[]}))}}' "$SB/reg.tsv" > "$FAKE/worktrees.json"
for n in regen_man regen_meta regen_slnx regen_company; do
  gc --only "$n"; chk "[R] $n: 다시 쓰인 줄만 → 후보" 'has "CANDIDATE repoA/'"$n"' "' "$(grep "$n" "$ARC/ws_gc.log" | tail -2)"
done
for n in regen_man_bad regen_meta_bad regen_product; do
  gc --only "$n"; chk "[R] $n: 허용 밖 줄 섞임 → 보존" 'has "SKIP dirty repoA/'"$n"'"' "$(grep "$n" "$ARC/ws_gc.log" | tail -2)"
done
chk "[R] 근거 로그: 다시쓰임 표기" 'has "다시쓰임 $MAN"'
# [R] 원본 없는 미추적 .meta(새 에셋의 .meta 일 수 있음)는 보존, CRLF 만 바뀐 일반 추적 파일은 부산물
mk regen_orphan_meta feat/rom; pr feat/rom '[{"number":63,"state":"MERGED"}]'; echo m > "$W/regen_orphan_meta/Assets/Sound/s/gone.cs.meta"
mk regen_crlf feat/rcrlf; pr feat/rcrlf '[{"number":64,"state":"MERGED"}]'; perl -pi -e 's/\n/\r\n/' "$W/regen_crlf/README.md"
jq -R -s '{ok:true,result:{worktrees:(split("\n")|map(select(length>0)|split("\t")|{id:("repo::"+.[0]),path:.[0],branch:.[1],lastActivityAt:(.[2]|tonumber),isMainWorktree:(.[3]=="true"),childWorktreeIds:[]}))}}' "$SB/reg.tsv" > "$FAKE/worktrees.json"
gc --only regen_orphan_meta; chk "[R] 원본 없는 미추적 .meta → 보존" 'has "SKIP dirty repoA/regen_orphan_meta"' "$(grep regen_orphan "$ARC/ws_gc.log" | tail -2)"
gc --only regen_crlf; chk "[R] CRLF 만 바뀐 추적 파일 → 후보" 'has "CANDIDATE repoA/regen_crlf " && has "줄바꿈만 README.md"' "$(grep regen_crlf "$ARC/ws_gc.log" | tail -2)"
# [R] 생성 캐시(루트 csproj·하위 __pycache__·node_modules)는 보존 이유가 아님
mk regen_cache feat/regen_cache; pr feat/regen_cache '[{"number":61,"state":"MERGED"}]'
printf 'Assembly-CSharp.csproj\n__pycache__/\nnode_modules/\n' >> "$W/regen_cache/.git/info/exclude"
echo p > "$W/regen_cache/Assembly-CSharp.csproj"; mkdir -p "$W/regen_cache/Tools/ev/__pycache__" "$W/regen_cache/fn/node_modules/x"; echo b > "$W/regen_cache/Tools/ev/__pycache__/a.pyc"; echo j > "$W/regen_cache/fn/node_modules/x/i.js"
# [R] 본체와 같은 무시 파일: 본체(SEED)의 작업 트리로 만든 곳에서 byte 비교
git -C "$SEED" worktree add -q -b feat/wt_same "$W/wt_same" 2>/dev/null; git -C "$SEED" worktree add -q -b feat/wt_diff "$W/wt_diff" 2>/dev/null
mkdir -p "$SEED/secret"; echo same > "$SEED/secret/auth.asset"
for n in wt_same wt_diff; do echo "$n" > "$W/$n/f.txt"; git -C "$W/$n" add -A; oldc "$n" "$n"; git -C "$W/$n" push -q -u origin "feat/$n" 2>/dev/null; mkdir -p "$W/$n/secret"; reg "$n" "refs/heads/feat/$n" "$OLD_MS"; pr "feat/$n" '[{"number":62,"state":"MERGED"}]'; done
echo same > "$W/wt_same/secret/auth.asset"; echo other > "$W/wt_diff/secret/auth.asset"
jq -R -s '{ok:true,result:{worktrees:(split("\n")|map(select(length>0)|split("\t")|{id:("repo::"+.[0]),path:.[0],branch:.[1],lastActivityAt:(.[2]|tonumber),isMainWorktree:(.[3]=="true"),childWorktreeIds:[]}))}}' "$SB/reg.tsv" > "$FAKE/worktrees.json"
gc --only regen_cache; chk "[R] 생성 캐시만 → 후보" 'has "CANDIDATE repoA/regen_cache "' "$(grep regen_cache "$ARC/ws_gc.log" | tail -2)"
gc --only wt_same; chk "[R] 본체와 같은 무시 파일 → 후보" 'has "CANDIDATE repoA/wt_same " && has "본체와같음 secret/auth.asset"' "$(grep wt_same "$ARC/ws_gc.log" | tail -2)"
gc --only wt_diff; chk "[R] 본체와 다른 무시 파일 → 보존" 'has "SKIP dirty repoA/wt_diff"' "$(grep wt_diff "$ARC/ws_gc.log" | tail -2)"
FAKE_RM_CHECK_DIRTY=1 gc --apply --only regen_man
chk "[R] regen_man: 되돌린 뒤 삭제" '[ "$RC" = 0 ] && [ ! -e "$W/regen_man" ]' "$(tail -3 "$ARC/ws_gc.log")"
FAKE_RM_CHECK_DIRTY=1 gc --apply --only regen_slnx
chk "[R] regen_slnx: 미추적 부산물만 → force 삭제" '[ "$RC" = 0 ] && [ ! -e "$W/regen_slnx" ]' "$(tail -3 "$ARC/ws_gc.log")"
gc --apply --only regen_meta_bad
chk "[R] regen_meta_bad: apply 에도 보존" '[ -e "$W/regen_meta_bad" ]'

# [5] 잠금: 동시에 4개를 띄우면 하나만 실행(나머지는 건너뜀), 끝난 뒤에는 다시 실행됨
mkdir -p "$SB/par"
for i in 1 2 3 4; do (FAKE_SLEEP=2 env PATH="$BIN:$PATH" FAKE_DIR="$FAKE" FAKE_ROOT="$ROOT" WS_GC_NOTIFY="$HERE/fake_send.sh" bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$SB/par/log" --apply --only nothing_matches > "$SB/par/out$i" 2>&1 &); done
sleep 5
chk "[5] 동시 실행 4개: 하나만 실행" '[ "$(grep -c "START" "$SB/par/log")" = 1 ] && [ "$(cat "$SB"/par/out* | grep -c "이미 실행 중")" = 3 ]' "start=$(grep -c START "$SB/par/log") skipped=$(cat "$SB"/par/out* | grep -c "이미 실행 중")"
gc --only nothing_matches; chk "[5] 끝난 뒤 다시 실행 가능(잠금이 남지 않음)" '[ "$RC" = 0 ] && has "START"'
chk "[5] 잠금은 pid 파일·잠금 폴더가 아님" '[ ! -d "$ARC/.ws_gc.lock" ] && [ -f "$ARC/.ws_gc.flock" ]'

# ---- 9. 곳 단위 잠금·Flutter 생성물·보관 대조(11-16) ----
lock_env() { env PATH="$BIN:$PATH" FAKE_DIR="$FAKE" FAKE_ROOT="$ROOT" WS_GC_NOTIFY="$HERE/fake_send.sh" "$@"; }
excl() { printf '%s\n' "$2" >> "$W/$1/.git/info/exclude"; }
for n in zlk_a zlk_b zlk_c zlk2_a zlk2_b; do mk "$n" "feat/$n"; pr "feat/$n" '[{"number":80,"state":"MERGED"}]'; done
regen
# [L1] 전체 실행 도중 --path 는 기다리지 않고 진행(전체 실행이 잠금을 오래 쥐지 않음), 먼저 지운 곳은 전체 실행이 건너뜀(중복 삭제 없음)
: > "$FAKE/orca.calls"
(FAKE_GH_SLEEP=3 lock_env bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$SB/lk.log" --apply --no-slack --only zlk_ > "$SB/lk.out" 2>&1 &)
sleep 1.5; T0=$(date +%s)
lock_env bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$SB/lk2.log" --apply --no-slack --path "$W/zlk_c" > "$SB/lk2.out" 2>&1; RC2=$?; T1=$(date +%s)
chk "[L1] 전체 실행 도중 --path: 대기 없이 그 곳 삭제" '[ "$RC2" = 0 ] && [ $((T1 - T0)) -le 2 ] && [ ! -e "$W/zlk_c" ] && ! grep -q "이미 실행 중" "$SB/lk2.out" && grep -q "DELETE repoA/zlk_c" "$SB/lk2.log"' "rc=$RC2 초=$((T1 - T0)) $(tail -2 "$SB/lk2.log")"
for _ in $(seq 60); do pgrep -f "log $SB/lk.log" > /dev/null || break; sleep 0.5; done
chk "[L1] 전체 실행은 나머지를 삭제하고 먼저 지워진 곳은 missing 으로 건너뜀" '[ ! -e "$W/zlk_a" ] && [ ! -e "$W/zlk_b" ] && grep -q "SKIP missing repoA/zlk_c" "$SB/lk.log" && [ "$(grep -c "worktree rm --worktree path:$W/zlk_c" "$FAKE/orca.calls")" = 1 ]' "$(tail -4 "$SB/lk.log")"
chk "[L1] 곳별 잠금 파일은 남고 전체 실행 잠금과 별개" '[ -d "$ARC/.ws_gc.locks" ] && [ -n "$(ls "$ARC/.ws_gc.locks")" ]'
# [L2] 같은 곳을 전체 실행이 처리 중이면 --path 가 그 곳만 기다렸다 이어받음(두 번 지우지 않음)
: > "$FAKE/orca.calls"
(FAKE_GH_SLEEP=3 lock_env bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$SB/lk3.log" --apply --no-slack --only zlk2_ > "$SB/lk3.out" 2>&1 &)
sleep 1.5
lock_env bash "$GC" --root "$ROOT" --archive-dir "$ARC" --log "$SB/lk4.log" --apply --no-slack --path "$W/zlk2_a" > "$SB/lk4.out" 2>&1
for _ in $(seq 60); do pgrep -f "log $SB/lk3.log" > /dev/null || break; sleep 0.5; done
chk "[L2] 같은 곳 동시 처리: 삭제 1번, 나중 실행은 missing" '[ ! -e "$W/zlk2_a" ] && [ "$(grep -c "worktree rm --worktree path:$W/zlk2_a" "$FAKE/orca.calls")" = 1 ] && [ "$(grep -c "DELETE repoA/zlk2_a" "$SB/lk3.log" "$SB/lk4.log" | awk -F: "{s+=\$2} END{print s}")" = 1 ]' "$(tail -3 "$SB/lk4.log")"
# [L3] 전체 실행끼리는 여전히 하나만(위 [5]) — --path 는 전체 실행 잠금을 안 씀
# [F] Flutter·Unity 생성물만 든 곳은 후보, 캐시 폴더 밖 소스는 보존
for n in fl_gen fl_kt fl_ign_kt; do
  mk "$n" "feat/$n"; pr "feat/$n" '[{"number":81,"state":"MERGED"}]'
  mkdir -p "$W/$n/android/.gradle/8.12" "$W/$n/android/app/build/outputs" "$W/$n/build/app" "$W/$n/ios/Flutter/ephemeral" "$W/$n/ios/Pods/P" "$W/$n/Library/BurstCache"
  echo g > "$W/$n/android/.gradle/8.12/a.bin"; echo a > "$W/$n/android/app/build/outputs/a.apk"; echo b > "$W/$n/build/app/x.txt"
  echo e > "$W/$n/ios/Flutter/ephemeral/e.txt"; echo p > "$W/$n/ios/Pods/P/p.h"; echo l > "$W/$n/android/local.properties"
  echo x > "$W/$n/ios/Flutter/Generated.xcconfig"; echo s > "$W/$n/ios/Flutter/flutter_export_environment.sh"; echo z > "$W/$n/Library/BurstCache/b.bin"
  for x in android/.gradle/ android/app/build/ /build/ ios/Flutter/ephemeral/ ios/Pods/ android/local.properties ios/Flutter/Generated.xcconfig ios/Flutter/flutter_export_environment.sh; do excl "$n" "$x"; done
done
mkdir -p "$W/fl_kt/android/app/src/main/kotlin" "$W/fl_ign_kt/android/app/src/main/kotlin"
echo 'class Only' > "$W/fl_kt/android/app/src/main/kotlin/Only.kt"
echo 'class Gen' > "$W/fl_ign_kt/android/app/src/main/kotlin/Gen.kt"; excl fl_ign_kt 'Gen.kt'
regen
gc --only fl_gen; chk "[F] Flutter·BurstCache 생성물만: 후보(미확인 0)" 'has "CANDIDATE repoA/fl_gen " && ! has "SKIP dirty repoA/fl_gen"' "$(grep fl_gen "$ARC/ws_gc.log" | tail -2)"
gc --only fl_kt; chk "[F] android 아래 소스 .kt(미추적): 보존" 'has "SKIP dirty repoA/fl_kt" && has "미확인 미추적 android/app/src/main/kotlin/Only.kt"' "$(grep fl_kt "$ARC/ws_gc.log" | tail -2)"
gc --only fl_ign_kt; chk "[F] android 아래 소스 .kt(gitignore): 보존" 'has "SKIP dirty repoA/fl_ign_kt" && has "미확인 무시파일 android/app/src/main/kotlin/Gen.kt"' "$(grep fl_ign_kt "$ARC/ws_gc.log" | tail -2)"
FAKE_RM_CHECK_DIRTY=1 gc --only fl_kt --apply; chk "[F] 보존한 .kt 가 그대로 남음" '[ -f "$W/fl_kt/android/app/src/main/kotlin/Only.kt" ] && [ "$(rm_calls)" = 0 ]'
# [N] 한글 파일명(NFC)을 tar 가 NFD 로 풀어도 보관 대조가 통과(11-16 TestResults 한글 파일 2곳 archive_fail)
mk nfd_out feat/nfd; pr feat/nfd '[{"number":82,"state":"MERGED"}]'; regen
mkdir -p "$W/nfd_out/TestResults/parts"; printf 'p' > "$W/nfd_out/TestResults/parts/$(printf '\xeb\x88\x88')_L.png"; printf 'q' > "$W/nfd_out/TestResults/parts/$(printf '\xeb\xa8\xb8\xeb\xa6\xac')_\xec\x95\x9e.png"
MM0=$(grep -c ARCHIVE_MISMATCH "$ARC/ws_gc.log")
FAKE_RM_CHECK_DIRTY=1 gc --only nfd_out --apply
chk "[N] 한글 파일명 보관: 삭제 성공·불일치 없음" '[ ! -e "$W/nfd_out" ] && [ "$(grep -c ARCHIVE_MISMATCH "$ARC/ws_gc.log")" = "$MM0" ] && [ "$RC" = 0 ]' "$(grep nfd_out "$ARC/ws_gc.log" | tail -3)"
chk "[N] 보관본에 한글 파일 2개" '[ "$(find "$ARC/repoA/nfd_out/TestResults/parts" -type f | wc -l | tr -d " ")" = 2 ]'

# [Q] 끝난 공간 자동 정리(lrm-qpy): 로컬 브랜치가 남는 미병합·오늘 활동한 곳, LFS·실행 비트 부산물
setms() { perl -pi -e 's#^(\Q'"$W/$1"'\E\t[^\t]*\t)\d+#${1}'"$2"'#' "$SB/reg.tsv"; }
recentc() { echo "$1" > "$W/$1/g.txt"; git -C "$W/$1" add -A; GIT_AUTHOR_DATE="@$2" GIT_COMMITTER_DATE="@$2" git -C "$W/$1" commit -q -m "$1"; }
NOWS=$(date +%s)
for n in q_today q_fresh q_det q_lfs q_lfs_bad q_mode; do mk "$n" "feat/$n" local; done
recentc q_today $((NOWS - 7200)); setms q_today $(( (NOWS - 7200) * 1000 ))   # 2시간 전 끝남·미push
recentc q_fresh $((NOWS - 600)); setms q_fresh $(( (NOWS - 600) * 1000 ))    # 10분 전: 유예 안
git -C "$W/q_det" checkout -q --detach; detach q_det
for n in q_lfs q_lfs_bad; do
  printf 'real-gif-bytes\n' > "$SB/blob.$n"; oid=$(shasum -a 256 "$SB/blob.$n" | cut -d' ' -f1)
  printf '*.gif filter=lfs diff=lfs merge=lfs -text\n' > "$W/$n/.gitattributes"
  printf 'version https://git-lfs.github.com/spec/v1\noid sha256:%s\nsize 15\n' "$oid" > "$W/$n/pic.gif"
  git -C "$W/$n" add -A; oldc "$n" lfs
  cp "$SB/blob.$n" "$W/$n/pic.gif"   # 내려받은 실파일(포인터 oid 와 같음)
done
printf 'other-bytes\n' > "$W/q_lfs_bad/pic.gif"
chmod +x "$W/q_mode/f.txt"
regen
gc --only q_; chk "[Q] 2시간 전 끝난 미병합(로컬 브랜치): 후보" 'has "CANDIDATE repoA/q_today .*미병합(브랜치 유지)"' "$(grep q_today "$ARC/ws_gc.log" | tail -2)"
chk "[Q] 10분 전 활동: 유예로 보존(idle)" 'has "SKIP idle repoA/q_fresh"'
chk "[Q] detached·미push: 보존(unmerged)" 'has "SKIP unmerged repoA/q_det"'
chk "[Q] LFS 포인터와 같은 실파일: 부산물로 후보" 'has "CANDIDATE repoA/q_lfs " && has "LFS동일 pic.gif"' "$(grep q_lfs "$ARC/ws_gc.log" | tail -3)"
chk "[Q] LFS 내용이 다름: 보존(dirty)" 'has "SKIP dirty repoA/q_lfs_bad"'
chk "[Q] 실행 비트만 바뀜: 부산물로 후보" 'has "CANDIDATE repoA/q_mode "' "$(grep q_mode "$ARC/ws_gc.log" | tail -2)"
FAKE_RM_CHECK_DIRTY=1 gc --only q_ --apply
chk "[Q] apply: 부산물 되돌린 뒤 삭제(q_today·q_lfs·q_mode)" '[ ! -e "$W/q_today" ] && [ ! -e "$W/q_lfs" ] && [ ! -e "$W/q_mode" ]' "$(grep -E "q_(lfs|mode|today)" "$ARC/ws_gc.log" | tail -4)"
chk "[Q] apply: 보존 대상은 그대로(q_fresh·q_det·q_lfs_bad)" '[ -e "$W/q_fresh" ] && [ -e "$W/q_det" ] && [ -e "$W/q_lfs_bad" ]'
chk "[Q] apply: 브랜치 삭제 옵션 없음" '! grep -q -- "--delete-branch" "$FAKE/orca.calls"'

# ---- 10. PR 닫힘 즉시 정리(11-17): 활동 일수·미커밋을 기다리지 않고 전부 보관 → 대조 → 삭제 ----
# mkpd 이름 브랜치 활동분전 : 옛 커밋 1개(origin 에 올림) + Orca 활동 시각만 N분 전. PR 은 따로(headRefOid = 지금 HEAD)
mkpd() { mk "$1" "$2"; perl -pi -e 's#^(\Q'"$W/$1"'\E\t[^\t]*\t)\d+#${1}'"$((NOW_MS - $3 * 60000))"'#' "$SB/reg.tsv"; }
prh() { pr "$1" "[{\"number\":$2,\"state\":\"$3\",\"headRefOid\":\"$(git -C "$W/$4" rev-parse HEAD)\"}]"; }
dirty_all() { # 추적 수정·스테이징·미추적 코드·Assets 새 파일·gitignore 코드(보관 대상), Library 캐시(보관 제외)는 mk 가 만듦
  echo pd-edit >> "$W/$1/README.md"
  echo staged > "$W/$1/staged.txt"; git -C "$W/$1" add staged.txt
  mkdir -p "$W/$1/src" "$W/$1/Assets/New" "$W/$1/secret"; echo code > "$W/$1/src/Foo.cs"; echo asset > "$W/$1/Assets/New/a.png"; echo s > "$W/$1/secret/Foo.cs"
}
mkpd pd_dirty feat/pd_dirty 20;   prh feat/pd_dirty 90 MERGED pd_dirty; dirty_all pd_dirty
mkpd pd_closed feat/pd_closed 20; prh feat/pd_closed 91 CLOSED pd_closed; echo late > "$W/pd_closed/notes.cs"
mkpd pd_open feat/pd_open 20;     pr feat/pd_open "[{\"number\":92,\"state\":\"MERGED\",\"headRefOid\":\"$(git -C "$W/pd_open" rev-parse HEAD)\"},{\"number\":93,\"state\":\"OPEN\"}]"; dirty_all pd_open
mkpd pd_newc feat/pd_newc 20;     prh feat/pd_newc 94 MERGED pd_newc; echo n > "$W/pd_newc/n.txt"; git -C "$W/pd_newc" add n.txt; oldc pd_newc after-pr; dirty_all pd_newc
mkpd pd_fresh feat/pd_fresh 3;    prh feat/pd_fresh 95 MERGED pd_fresh; dirty_all pd_fresh
mkpd pd_live feat/pd_live 20;     prh feat/pd_live 96 MERGED pd_live; dirty_all pd_live; printf 'term_pd\t%s\n' "$W/pd_live" >> "$FAKE/terminals.tsv"
mkpd pd_nopr feat/pd_nopr 20;     dirty_all pd_nopr
mkpd pd_sub feat/pd_sub 20
  git -C "$W/pd_sub" -c protocol.file.allow=always submodule add -q "$O/sub.git" Assets/Sub 2>/dev/null; git -C "$W/pd_sub" add -A; oldc pd_sub sub; git -C "$W/pd_sub" push -q origin feat/pd_sub 2>/dev/null
  prh feat/pd_sub 97 MERGED pd_sub; echo wip > "$W/pd_sub/Assets/Sub/wip.txt"
mkpd pd_big feat/pd_big 20;       prh feat/pd_big 98 MERGED pd_big; dirty_all pd_big
mkpd pd_late feat/pd_late 20;     prh feat/pd_late 99 MERGED pd_late; dirty_all pd_late
mkpd pd_tar feat/pd_tar 20;       prh feat/pd_tar 100 MERGED pd_tar; dirty_all pd_tar
mk dev_pd dev; perl -pi -e 's#^(\Q'"$W/dev_pd"'\E\t[^\t]*\t)\d+#${1}'"$((NOW_MS - 20 * 60000))"'#' "$SB/reg.tsv"; prh dev 101 MERGED dev_pd
regen
jq -R -s '{ok:true,result:{terminals:(split("\n")|map(select(length>0)|split("\t")|{handle:.[0],worktreePath:.[1],connected:true,orphaned:false}))}}' "$FAKE/terminals.tsv" > "$FAKE/terminals.json"
pdlog() { grep "repoA/$1 " "$ARC/ws_gc.log" | tail -2; }
gc --only pd_
chk "[PD] dry-run: 병합 PR·미커밋 있어도 활동 대기 없이 후보(pr_done)" 'has "CANDIDATE repoA/pd_dirty pr_done PR #90 MERGED"' "$(pdlog pd_dirty)"
chk "[PD] dry-run: 보관 예정 파일 수·추적 변경 수·크기 기록" 'has "CANDIDATE repoA/pd_dirty .*보관예정 파일 [1-9][0-9]*개·추적 변경 2건"' "$(pdlog pd_dirty)"
chk "[PD] dry-run: 닫힌(CLOSED) PR 도 후보" 'has "CANDIDATE repoA/pd_closed pr_done PR #91 CLOSED"' "$(pdlog pd_closed)"
chk "[PD] OPEN PR 이 하나라도 있으면 기존 규칙(후보 아님)" '! has "CANDIDATE repoA/pd_open " && has "SKIP idle repoA/pd_open"' "$(pdlog pd_open)"
chk "[PD] PR 뒤에 새 커밋이 있으면 기존 규칙" '! has "CANDIDATE repoA/pd_newc " && has "SKIP idle repoA/pd_newc"' "$(pdlog pd_newc)"
chk "[PD] 마지막 활동 10분 안이면 기존 규칙(유예)" '! has "CANDIDATE repoA/pd_fresh " && has "SKIP idle repoA/pd_fresh"' "$(pdlog pd_fresh)"
chk "[PD] 터미널이 붙은 곳은 PR 이 닫혀도 보존(live)" 'has "SKIP live repoA/pd_live"'
chk "[PD] PR 없는 곳은 기존 규칙(미커밋이면 보존)" '! has "CANDIDATE repoA/pd_nopr " && has "SKIP idle repoA/pd_nopr"' "$(pdlog pd_nopr)"
chk "[PD] 서브모듈 안 변경은 보존(submodule)" 'has "SKIP submodule repoA/pd_sub — PR 닫힘"' "$(pdlog pd_sub)"
gc --only dev_pd; chk "[PD] 보호 브랜치는 병합 PR 이 있어도 보존" 'has "SKIP protected repoA/dev_pd"'
chk "[PD] dry-run: 폴더·보관 폴더 그대로" '[ -e "$W/pd_dirty" ] && [ -z "$(ls -d "$ARC"/pd_* 2>/dev/null)" ] && [ "$(rm_calls)" = 0 ]'
WS_GC_PR_DONE_MAX_MB=0 gc --only pd_big --apply
chk "[PD] 보관 상한 초과 → archive_big 보존" '[ -e "$W/pd_big" ] && has "SKIP archive_big repoA/pd_big" && [ "$(rm_calls)" = 0 ]' "$(pdlog pd_big)"
FAKE_RM_CHECK_DIRTY=1 gc --only pd_dirty --apply
D=$(ls -d "$ARC"/pd_dirty-* 2>/dev/null | head -1)
chk "[PD] apply: 보관 폴더 이름 <폴더>-YYYYMMDD-HHMM" '[ -n "$D" ] && basename "$D" | grep -Eq "^pd_dirty-[0-9]{8}-[0-9]{4}$"' "$D"
chk "[PD] apply: 공간 삭제·미커밋 거부 뒤 재대조 → --force" '[ ! -e "$W/pd_dirty" ] && grep -q "^worktree rm --worktree path:$W/pd_dirty --force" "$FAKE/orca.calls" && has "RM_RETRY_FORCE PR 닫힘·보관본 대조 일치"' "$(pdlog pd_dirty)"
chk "[PD] apply: tracked.patch 에 추적 수정·스테이징, staged.patch 에 스테이징만" 'grep -q "^+pd-edit" "$D/tracked.patch" && grep -q "^+staged" "$D/tracked.patch" && grep -q "^+staged" "$D/staged.patch" && ! grep -q "pd-edit" "$D/staged.patch"'
chk "[PD] apply: 미추적·gitignore 코드·Assets 새 파일·결과물을 경로 그대로 보관" '[ -f "$D/files/src/Foo.cs" ] && [ -f "$D/files/Assets/New/a.png" ] && [ -f "$D/files/secret/Foo.cs" ] && [ -f "$D/files/TestResults/a.xml" ] && [ -f "$D/files/docs/out/x.html" ]'
chk "[PD] apply: 캐시(Library·.dart_tool)는 보관 안 함" '[ ! -e "$D/files/Library" ] && [ ! -e "$D/files/.dart_tool" ]'
chk "[PD] apply: 상태·메타(경로·브랜치·HEAD·PR) 기록" 'grep -q "^branch	feat/pd_dirty$" "$D/meta.txt" && grep -q "^pr	#90 MERGED$" "$D/meta.txt" && grep -q "src/Foo.cs" "$D/status.txt"'
chk "[PD] apply: 파일 수·바이트 대조 로그·삭제 로그에 보관 경로" 'has "ARCHIVE_OK 파일 수·바이트 [1-9][0-9]* [1-9][0-9]* 일치" && has "DELETE repoA/pd_dirty pr_done .*보관=$D (브랜치는 유지)"'
chk "[PD] apply: 허브 색인에 보관 경로 기록" 'grep -q "repoA/pd_dirty	$D	" "$WS_GC_INBOX/_none.tsv"'
git clone -q "$O/repoA.git" "$SB/restore" 2>/dev/null; git -C "$SB/restore" checkout -q feat/pd_dirty
chk "[PD] 보관한 tracked.patch 가 브랜치에 그대로 적용됨(되살리기 가능)" 'git -C "$SB/restore" apply --binary "$D/tracked.patch" && grep -q pd-edit "$SB/restore/README.md" && [ -f "$SB/restore/staged.txt" ]'
FAKE_RM_CHECK_DIRTY=1 gc --only pd_closed --apply
chk "[PD] apply: 닫힌 PR 도 보관 뒤 삭제" '[ ! -e "$W/pd_closed" ] && [ -f "$(ls -d "$ARC"/pd_closed-* | head -1)/files/notes.cs" ]' "$(pdlog pd_closed)"
FAKE_RM_CHECK_DIRTY=1 FAKE_RM_ADD_EDIT=1 gc --only pd_late --apply
chk "[PD] 보관 뒤 새 변경이 생기면 강제 삭제 안 함(rm_fail·보관본 남음·종료 1)" '[ -e "$W/pd_late" ] && ! grep -q -- "pd_late --force" "$FAKE/orca.calls" && has "ARCHIVE_MISMATCH tracked.patch" && has "SKIP rm_fail repoA/pd_late" && [ -n "$(ls -d "$ARC"/pd_late-* 2>/dev/null)" ] && [ "$RC" = 1 ]' "rc=$RC $(pdlog pd_late)"
FAKE_TAR_CORRUPT=1 FAKE_RM_CHECK_DIRTY=1 gc --only pd_tar --apply
chk "[PD] 보관본 내용이 다르면 삭제 안 함(archive_fail)" '[ -e "$W/pd_tar" ] && has "SKIP archive_fail repoA/pd_tar" && [ "$(rm_calls)" = 0 ]' "$(pdlog pd_tar)"
gc --only pd_ --apply --json --no-slack 2>/dev/null
chk "[PD] 요약: PR 닫힘 즉시 정리 개수" 'has "END .*PR 닫힘 즉시 정리"'
chk "[PD] 보존 대상은 끝까지 그대로" '[ -e "$W/pd_open" ] && [ -e "$W/pd_newc" ] && [ -e "$W/pd_fresh" ] && [ -e "$W/pd_live" ] && [ -e "$W/pd_nopr" ] && [ -e "$W/pd_sub" ] && [ -e "$W/dev_pd" ]'

# ---- 11. 병합된 PR 에 창이 붙은 곳(11-19): 이번 회차에 표시하고 다음 회차에 창을 닫고 정리(수거 방식) ----
regen_live() {
  regen
  jq -R -s '{ok:true,result:{terminals:(split("\n")|map(select(length>0)|split("\t")|{handle:.[0],worktreePath:.[1],connected:true,orphaned:false}))}}' "$FAKE/terminals.tsv" > "$FAKE/terminals.json"
  jq -R -s '{ok:true,result:{workers:(split("\n")|map(select(length>0)|split("\t")|{dispatchId:.[0],terminalState:.[1],agentTerminalHandle:.[2],resource:{worktreeId:("repo::"+.[3])}})),page:{hasMore:false}}}' "$FAKE/workers.tsv" > "$FAKE/workers.json"
}
term() { printf 'term_%s\t%s\n' "$1" "$W/$1" >> "$FAKE/terminals.tsv"; }
active_min() { perl -pi -e 's#^(\Q'"$W/$1"'\E\t[^\t]*\t)\d+#${1}'"$((NOW_MS - $2 * 60000))"'#' "$SB/reg.tsv"; }
MARKS="$ARC/ws_gc.marks"
mark_old() { printf '%s\t%s\n' "$W/$1" "$(( $(date +%s) - 1800 ))" >> "$MARKS"; }
mkpd ml_idle feat/ml_idle 40;     prh feat/ml_idle 110 MERGED ml_idle; dirty_all ml_idle; term ml_idle
mkpd ml_busy feat/ml_busy 20;     prh feat/ml_busy 111 MERGED ml_busy; term ml_busy
mkpd ml_closed feat/ml_closed 40; prh feat/ml_closed 112 CLOSED ml_closed; term ml_closed
mkpd ml_worker feat/ml_worker 40; prh feat/ml_worker 113 MERGED ml_worker; term ml_worker
  printf 'd_ml\tactive\tterm_ml_worker\t%s\n' "$W/ml_worker" >> "$FAKE/workers.tsv"
mkpd ml_back feat/ml_back 40;     prh feat/ml_back 114 MERGED ml_back; term ml_back
mkpd ml_fail feat/ml_fail 40;     prh feat/ml_fail 115 MERGED ml_fail; term ml_fail
mkpd ml_dry feat/ml_dry 40;       prh feat/ml_dry 116 MERGED ml_dry; term ml_dry
regen_live
gc --only ml_dry
chk "[ML] dry-run: 표시 예정만 기록하고 표시 파일은 쓰지 않음" 'has "SKIP live_marked repoA/ml_dry" && [ ! -s "$MARKS" ]' "$(pdlog ml_dry)"
gc --only ml_ --apply
chk "[ML] 첫 회차: 표시만 하고 지우지 않음(창도 그대로)" '[ -e "$W/ml_idle" ] && has "SKIP live_marked repoA/ml_idle — PR #110 MERGED" && grep -q "^$W/ml_idle	" "$MARKS" && ! grep -q "^terminal close" "$FAKE/orca.calls"' "$(pdlog ml_idle)"
chk "[ML] 30분 안에 활동한 곳은 표시하지 않음(사용 중)" 'has "SKIP live repoA/ml_busy" && ! grep -q "ml_busy" "$MARKS"' "$(pdlog ml_busy)"
chk "[ML] 병합이 아니라 닫히기만 한 PR 은 창이 있으면 보존" 'has "SKIP live repoA/ml_closed" && ! grep -q "ml_closed" "$MARKS"' "$(pdlog ml_closed)"
chk "[ML] 일하는 워커가 붙은 곳은 보존" 'has "SKIP live repoA/ml_worker" && ! grep -q "ml_worker" "$MARKS"' "$(pdlog ml_worker)"
gc --only ml_idle --apply
chk "[ML] 표시한 직후 회차에는 아직 지우지 않음" '[ -e "$W/ml_idle" ] && has "SKIP live_marked repoA/ml_idle — 표시 후 0분" && [ "$(grep -c "ml_idle" "$MARKS")" = 1 ]' "$(pdlog ml_idle)"
perl -pi -e 's#^(\Q'"$W/ml_idle"'\E\t)\d+#${1}'"$(( $(date +%s) - 1800 ))"'#' "$MARKS"
FAKE_RM_CHECK_DIRTY=1 gc --only ml_idle --apply
D=$(ls -d "$ARC"/ml_idle-* 2>/dev/null | head -1)
chk "[ML] 다음 회차: 창을 닫고 미커밋을 보관한 뒤 삭제" '[ ! -e "$W/ml_idle" ] && grep -q "^terminal close --worktree path:$W/ml_idle --all --json" "$FAKE/orca.calls" && has "DELETE repoA/ml_idle pr_done" && [ -f "$D/files/src/Foo.cs" ]' "$(pdlog ml_idle)"
chk "[ML] 창 닫기가 삭제보다 먼저" '[ "$(grep -n "^terminal close" "$FAKE/orca.calls" | head -1 | cut -d: -f1)" -lt "$(grep -n "^worktree rm" "$FAKE/orca.calls" | head -1 | cut -d: -f1)" ]'
chk "[ML] 치운 곳의 표시는 지움, 다른 곳의 표시는 그대로" '! grep -q "ml_idle" "$MARKS" && grep -q "ml_back" "$MARKS"'
active_min ml_back 5; regen_live
gc --only ml_back --apply
chk "[ML] 표시 뒤에 활동이 생기면 표시를 풀고 보존" '[ -e "$W/ml_back" ] && has "SKIP live repoA/ml_back" && ! grep -q "ml_back" "$MARKS"' "$(pdlog ml_back)"
perl -pi -e 's#^(\Q'"$W/ml_fail"'\E\t)\d+#${1}'"$(( $(date +%s) - 1800 ))"'#' "$MARKS"
FAKE_TERM_CLOSE_FAIL=1 gc --only ml_fail --apply
chk "[ML] 창 닫기가 실패하면 지우지 않음(term_close_fail)" '[ -e "$W/ml_fail" ] && has "SKIP term_close_fail repoA/ml_fail" && [ "$(rm_calls)" = 0 ]' "$(pdlog ml_fail)"
WS_GC_MERGED_LIVE=0 gc --only ml_fail --apply
chk "[ML] 끄면(WS_GC_MERGED_LIVE=0) 창이 붙은 곳은 예전처럼 보존" '[ -e "$W/ml_fail" ] && has "SKIP live repoA/ml_fail" && ! grep -q "^terminal close" "$FAKE/orca.calls"' "$(pdlog ml_fail)"

# ---- LM. PR 없이 로컬에서 main 에 병합한 브랜치 ------------------------------------------------
lmerge() { git -C "$W/$1" checkout -q main && GIT_AUTHOR_DATE=$OLD GIT_COMMITTER_DATE=$OLD git -C "$W/$1" merge -q --no-ff -m "merge $2" "$2" && git -C "$W/$1" checkout -q "$2"; }
mkpd lm_done feat/lm_done 40;  lmerge lm_done feat/lm_done; dirty_all lm_done
mkpd lm_term feat/lm_term 40;  lmerge lm_term feat/lm_term; term lm_term
mkpd lm_unm feat/lm_unm 40;    dirty_all lm_unm; term lm_unm                       # 병합 안 됨
mkpd lm_after feat/lm_after 40; lmerge lm_after feat/lm_after; echo more > "$W/lm_after/m.txt"; git -C "$W/lm_after" add -A; oldc lm_after after; dirty_all lm_after  # 병합 뒤 새 커밋
mkpd lm_ff feat/lm_ff 40; git -C "$W/lm_ff" checkout -q main; git -C "$W/lm_ff" merge -q --ff-only feat/lm_ff; git -C "$W/lm_ff" checkout -q feat/lm_ff; dirty_all lm_ff  # 빨리 감기(빈 브랜치와 구분 불가)
regen_live
gc --only lm_ --apply
chk "[LM] 로컬 병합(--no-ff)된 곳은 PR 없어도 미커밋 보관 뒤 삭제" '[ ! -e "$W/lm_done" ] && has "DELETE repoA/lm_done pr_done PR 로컬병합(main)" && [ -f "$(ls -d "$ARC"/lm_done-* | head -1)/files/src/Foo.cs" ]' "$(pdlog lm_done)"
chk "[LM] 창이 붙은 로컬 병합은 표시만(다음 회차 정리)" '[ -e "$W/lm_term" ] && has "SKIP live_marked repoA/lm_term — PR 로컬병합(main)"' "$(pdlog lm_term)"
chk "[LM] 병합 안 된 곳은 창이 붙어 있으면 보존" '[ -e "$W/lm_unm" ] && has "SKIP live repoA/lm_unm"' "$(pdlog lm_unm)"
chk "[LM] 병합 뒤 새 커밋·빨리 감기 병합은 로컬 병합으로 안 침(미커밋 보존)" '[ -e "$W/lm_after" ] && [ -e "$W/lm_ff" ] && ! has "repoA/lm_after pr_done" && ! has "repoA/lm_ff pr_done"' "$(pdlog lm_after; pdlog lm_ff)"

# ---- PL. Orca 밖 수동 작업 공간(git worktree) ------------------------------------------------
mkdir -p "$SB/plain"
plwt() { # 이름 [merge] [recent]
  git -C "$SEED" worktree add -q -b "feat/$1" "$SB/plain/$1" main; echo "$1" > "$SB/plain/$1/p.txt"; git -C "$SB/plain/$1" add -A
  if [ "${3:-}" = recent ]; then git -C "$SB/plain/$1" commit -q -m "$1"; else GIT_AUTHOR_DATE=$OLD GIT_COMMITTER_DATE=$OLD git -C "$SB/plain/$1" commit -q -m "$1"; touch -t 202601010000 "$SB/plain/$1/p.txt"; fi
  [ "${2:-}" = merge ] && GIT_AUTHOR_DATE=$OLD GIT_COMMITTER_DATE=$OLD git -C "$SEED" merge -q --no-ff -m "merge $1" "feat/$1"
  find "$SB/plain/$1" -path "$SB/plain/$1/.git" -prune -o -exec touch -h -t 202601010000 {} + 2>/dev/null
}
plwt pl_done merge; plwt pl_dirty merge; plwt pl_unm; plwt pl_recent merge recent
echo wip > "$SB/plain/pl_dirty/wip.txt"; touch -t 202601010000 "$SB/plain/pl_dirty/wip.txt"
gc --only pl_
chk "[PL] dry-run: 후보만 기록하고 지우지 않음" '[ -d "$SB/plain/pl_done" ] && has "CANDIDATE plain:"' "$(grep pl_ "$ARC/ws_gc.log" | tail -4)"
gc --only pl_ --apply
chk "[PL] 로컬 병합·깨끗·유휴: git worktree remove, 브랜치 유지" '[ ! -e "$SB/plain/pl_done" ] && git -C "$SEED" show-ref -q refs/heads/feat/pl_done && has "DELETE plain:.*pl_done 로컬병합(main)"' "$(grep pl_ "$ARC/ws_gc.log" | tail -4)"
chk "[PL] 미추적 있음·미병합·최근 커밋은 보존" '[ -e "$SB/plain/pl_dirty" ] && [ -e "$SB/plain/pl_unm" ] && [ -e "$SB/plain/pl_recent" ] && has "SKIP dirty plain:.*pl_dirty" && has "SKIP unmerged plain:.*pl_unm" && has "SKIP idle plain:.*pl_recent"' "$(grep pl_ "$ARC/ws_gc.log" | tail -6)"
chk "[PL] 메인 체크아웃(.git 폴더)은 대상 아님" '[ -d "$SEED" ]'

for n in live children detached protected big corrupt changed refused; do
  mk "coord-gc-$n" "feat/gc-$n" local recent
  printf '%s\t%s\n' "$W/coord-gc-$n" "$(date +%s)" >> "$LRM_CLOSED_HUBS"
done
git -C "$SEED" worktree add -q -b feat/gc-ready "$W/coord-gc-ready" HEAD
reg coord-gc-ready refs/heads/feat/gc-ready "$NOW_MS"
detach coord-gc-detached
git -C "$W/coord-gc-protected" branch -m dev
git -C "$W/coord-gc-ready" sparse-checkout init --no-cone
printf '/docs/\n/.claude/\n/CLAUDE.md\n/AGENTS.md\n' | git -C "$W/coord-gc-ready" sparse-checkout set --no-cone --stdin
mkdir -p "$W/coord-gc-ready/docs/out/evidence"
printf 'unique html\n' > "$W/coord-gc-ready/docs/out/report.html"
printf 'unique evidence\n' > "$W/coord-gc-ready/docs/out/evidence/check.txt"
git -C "$W/coord-gc-ready" show HEAD:.gitignore > "$W/coord-gc-ready/.gitignore"
printf '!docs/out/\n!docs/out/**\n' >> "$W/coord-gc-ready/.gitignore"
ln -s "$W/coord-gc-ready" "$SB/hub-alias"
printf '%s\t%s\n' "$SB/hub-alias" "$(date +%s)" >> "$LRM_CLOSED_HUBS"
term coord-gc-live
regen; regen_live
jq --arg h "repo::$W/coord-gc-children" --arg c "$W/coord-hub" \
  '.result.worktrees |= map(if .path == $c then .parentWorktreeId = $h else . end)' "$FAKE/worktrees.json" > "$SB/hubs.json"
mv "$SB/hubs.json" "$FAKE/worktrees.json"
cp "$LRM_CLOSED_HUBS" "$SB/closed.before"
cp "$W/coord-gc-ready/docs/out/report.html" "$SB/report.before"
cp "$W/coord-gc-ready/docs/out/evidence/check.txt" "$SB/check.before"
chk "[HUB] HTML·증거가 미추적 파일로 조회됨" 'git -C "$W/coord-gc-ready" ls-files -o --exclude-standard | grep -Fxq docs/out/report.html && git -C "$W/coord-gc-ready" ls-files -o --exclude-standard | grep -Fxq docs/out/evidence/check.txt'
JS=$(jsonrun --only coord-gc-)
chk "[HUB] 미리보기: 최근·PR 없는 sparse 허브 후보" 'echo "$JS" | jq -e '\''.items[] | select(.name == "repoA/coord-gc-ready") | .reason == "hub_closed" and .verdict == "candidate"'\'' >/dev/null'
chk "[HUB] 미리보기: 목록·결과물·보관 불변" 'cmp -s "$LRM_CLOSED_HUBS" "$SB/closed.before" && cmp -s "$W/coord-gc-ready/docs/out/report.html" "$SB/report.before" && [ -z "$(ls -d "$ARC"/coord-gc-* 2>/dev/null)" ]'
for e in live:live children:children detached:unmerged protected:protected; do
  n=${e%:*}; r=${e#*:}
  chk "[HUB] 보호: $n → $r" 'echo "$JS" | jq -e --arg n "repoA/coord-gc-$n" --arg r "$r" '\''.items[] | select(.name == $n) | .reason == $r and .verdict == "skip"'\'' >/dev/null'
done
gc --only coord-hub
chk "[HUB] 목록 밖 허브 보존" 'has "SKIP hub repoA/coord-hub" && [ -e "$W/coord-hub" ]'
WS_GC_PR_DONE_MAX_MB=0 gc --apply --only coord-gc-big
chk "[HUB] 보관 상한 초과 보존·목록 유지" '[ "$RC" = 0 ] && has "SKIP archive_big repoA/coord-gc-big" && ! has "PR 닫힘()" && grep -Fq "$W/coord-gc-big" "$LRM_CLOSED_HUBS"'
FAKE_TAR_CORRUPT=1 gc --apply --only coord-gc-corrupt
chk "[HUB] 보관 손상 시 삭제 차단·목록 유지" '[ "$RC" = 1 ] && [ -d "$W/coord-gc-corrupt" ] && grep -Fq "$W/coord-gc-corrupt" "$LRM_CLOSED_HUBS"'
FAKE_RM_CHECK_DIRTY=1 FAKE_RM_ADD_EDIT=1 gc --apply --only coord-gc-changed
chk "[HUB] 보관 뒤 변경 시 강제 삭제 차단" '[ "$RC" = 1 ] && [ -d "$W/coord-gc-changed" ] && ! grep -q -- "--force" "$FAKE/orca.calls"'
FAKE_RM_REASON=locked gc --apply --only coord-gc-refused
chk "[HUB] 삭제 거부 시 목록 유지" '[ "$RC" = 1 ] && grep -Fq "$W/coord-gc-refused" "$LRM_CLOSED_HUBS"'
FAKE_RM_CHECK_DIRTY=1 gc --apply --only coord-gc-ready --json --no-slack
D=$(ls -d "$ARC"/coord-gc-ready-* 2>/dev/null | head -1)
chk "[HUB] 보관 뒤 삭제·정규화한 목록 항목 제거" '[ "$RC" = 0 ] && [ ! -e "$W/coord-gc-ready" ] && ! grep -q "coord-gc-ready\|hub-alias" "$LRM_CLOSED_HUBS" && grep -Fq "$W/coord-gc-live" "$LRM_CLOSED_HUBS"'
chk "[HUB] 미추적 docs/out HTML·증거 byte 일치" 'cmp -s "$D/files/docs/out/report.html" "$SB/report.before" && cmp -s "$D/files/docs/out/evidence/check.txt" "$SB/check.before"'
chk "[HUB] 로그·JSON 정리 사유 hub_closed" 'has "DELETE repoA/coord-gc-ready hub_closed" && sed -n '\''/^{/,$p'\'' "$SB/out" | jq -e '\''.items[] | .reason == "hub_closed" and .verdict == "deleted"'\'' >/dev/null'
chk "[HUB] 브랜치 유지·창 닫기 없음" 'git -C "$SEED" show-ref --verify --quiet refs/heads/feat/gc-ready && ! grep -q "terminal close\|--delete-branch" "$FAKE/orca.calls"'
mkdir -p "$SB/dry-check"
env PATH="$BIN:$PATH" FAKE_DIR="$FAKE" FAKE_ROOT="$ROOT" bash "$GC" --root "$ROOT" --archive-dir "$SB/dry-check/archive" --log "$SB/dry-check/log" --only coord-gc- --dry-run --no-slack > "$SB/dry-check.out" 2>&1
RC=$?
chk "[HUB] 미리보기: 로그·잠금·보관 파일 생성 없음" '[ "$RC" = 0 ] && [ -z "$(ls -A "$SB/dry-check")" ]'

for n in activity commit legacy grace; do
  mk "coord-reopen-$n" "feat/reopen-$n" local recent
done
printf '%s\t%s\n' "$W/coord-reopen-activity" "$((NOW_MS / 1000 - WS_GC_HUB_REOPEN_GRACE_MIN * 60 - 1))" >> "$LRM_CLOSED_HUBS"
printf '%s\t%s\n' "$W/coord-reopen-commit" "$((NOW_MS / 1000 - WS_GC_HUB_REOPEN_GRACE_MIN * 60 - 1))" >> "$LRM_CLOSED_HUBS"
printf '%s\t%s\n' "$W/coord-reopen-grace" "$((NOW_MS / 1000 - 1))" >> "$LRM_CLOSED_HUBS"
printf '%s\n' "$W/coord-reopen-legacy" >> "$LRM_CLOSED_HUBS"
regen
jq --arg p "$W/coord-reopen-commit" '.result.worktrees |= map(if .path == $p then .lastActivityAt = 0 else . end)' "$FAKE/worktrees.json" > "$SB/reopen.json"
mv "$SB/reopen.json" "$FAKE/worktrees.json"
cp "$LRM_CLOSED_HUBS" "$SB/reopen.before"
gc --only coord-reopen-
chk "[HUB] 재개 허브 미리보기: 활동·커밋 감지·목록 불변" 'has "SKIP hub_reopened repoA/coord-reopen-activity" && has "SKIP hub_reopened repoA/coord-reopen-commit" && cmp -s "$LRM_CLOSED_HUBS" "$SB/reopen.before"'
chk "[HUB] 여유 안 활동·커밋: 삭제 후보" 'has "CANDIDATE repoA/coord-reopen-grace hub_closed"'
gc --only coord-reopen- --apply
chk "[HUB] 여유 뒤 재개 허브 보존·목록 제거" '[ "$RC" = 0 ] && [ -d "$W/coord-reopen-activity" ] && [ -d "$W/coord-reopen-commit" ] && ! grep -q "coord-reopen-activity\|coord-reopen-commit" "$LRM_CLOSED_HUBS" && [ "$(rm_calls)" = 1 ]'
chk "[HUB] 여유 안 활동·커밋: 보관 뒤 삭제" '[ ! -e "$W/coord-reopen-grace" ] && ! grep -q "coord-reopen-grace" "$LRM_CLOSED_HUBS" && [ -d "$(ls -d "$ARC"/coord-reopen-grace-* | head -1)" ]'
chk "[HUB] 옛 목록 경고·보존·목록 유지" 'has "CLOSED_HUBS_WARN" && has "SKIP hub repoA/coord-reopen-legacy" && [ -d "$W/coord-reopen-legacy" ] && grep -Fxq "$W/coord-reopen-legacy" "$LRM_CLOSED_HUBS"'

bash "$HERE/adopt.sh" || FAIL=$((FAIL + 1))
echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
