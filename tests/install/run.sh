#!/usr/bin/env bash
# install.sh 샌드박스 검증. 임시 HOME 에서만 실행하며 실제 ~/.claude 등은 읽지도 바꾸지도 않음.
# 선택: SETTINGS_SAMPLE=<settings.json 사본> 을 주면 그 파일로 병합 검증을 추가 실행함.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
INSTALL="$REPO/install.sh"
unset GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM
PASS=0 FAIL=0

ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
check() { local name="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$name"; else ko "$name" "명령 실패: $*"; fi; }

# 폴더 상태 지문. 경로·링크 대상·파일 해시
snapshot() { (cd "$1" && find . -path ./.claude-config-backup -prune -o -print | LC_ALL=C sort | while read -r p; do
  if [ -L "$p" ]; then printf 'L %s -> %s\n' "$p" "$(readlink "$p")"; elif [ -f "$p" ]; then printf 'F %s %s\n' "$p" "$(shasum "$p" | cut -d' ' -f1)"; else printf 'D %s\n' "$p"; fi
done); }

# ---- 정적 검사 -------------------------------------------------------------
scripts=$(cd "$REPO" && find . -name '*.sh' -not -path './.git/*' | sort)
bad=0; for f in $scripts; do bash -n "$REPO/$f" 2>/dev/null || { bad=1; ko "bash -n" "$f"; }; done
[ "$bad" = 0 ] && ok "bash -n 전체 ($(echo "$scripts" | wc -l | tr -d ' ')개)"
if command -v shellcheck >/dev/null; then
  if (cd "$REPO" && shellcheck -S warning install.sh tests/install/run.sh >/dev/null 2>&1); then ok "shellcheck install.sh"; else ko "shellcheck" "install.sh 경고 있음"; fi
else
  printf 'SKIP  shellcheck — 미설치(brew install shellcheck)\n'
fi

# ---- links.tsv 정합 --------------------------------------------------------
entries=$(grep -v '^[[:space:]]*#' "$REPO/links.tsv" | grep -v '^[[:space:]]*$')
missing=0; while IFS=$'\t' read -r src dst mode; do [ -e "$REPO/$src" ] || { ko "links.tsv 원본" "$src 없음"; missing=1; }; done <<< "$entries"
[ "$missing" = 0 ] && ok "links.tsv 원본 전부 존재 ($(echo "$entries" | wc -l | tr -d ' ')줄)"
dups=$(echo "$entries" | cut -f2 | sort | uniq -d)
[ -z "$dups" ] && ok "links.tsv dst 중복 없음" || ko "links.tsv dst 중복" "$dups"

# bash 5.2+ patsub_replacement 방어(이 맥은 3.2 라 동작 검증은 불가, 구문 존재만 확인)
grep -q '^shopt -u patsub_replacement' "$REPO/install.sh" && ok "install.sh patsub_replacement 방어" || ko "patsub_replacement" "shopt 줄 없음"

# 역방향: 링크 대상 폴더의 모든 추적 파일이 links.tsv 에 직접 또는 상위 폴더로 있어야 함(파일 추가 시 누락 방지)
srcs=$(echo "$entries" | cut -f1)
uncovered=$(cd "$REPO" && git ls-files skills claude hubs git | grep -v -x -F 'hubs/slack-bot.env.example' | while read -r f; do
  d=$f; hit=0
  while [ "$d" != "." ] && [ "$d" != "/" ]; do if echo "$srcs" | grep -qx -F "$d"; then hit=1; break; fi; d=$(dirname "$d"); done
  [ "$hit" = 1 ] || echo "$f"
done)
[ -z "$uncovered" ] && ok "links.tsv 역방향: 추적 파일 전부 목록에 포함" || ko "links.tsv 누락" "$(echo "$uncovered" | head -3 | tr '\n' ' ')"

# ---- 샌드박스 준비 ---------------------------------------------------------
SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-sandbox.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
REAL_HOME="$HOME"
export HOME="$SB"
# 기존 파일이 저장소와 다른 샌드박스 시나리오가 많아 기본은 허용. 라이브 우선 점검은 6e 에서 환경변수 없이 따로 검증
export CLAUDE_CONFIG_ALLOW_LIVE_DIFF=1
mkdir -p "$SB/.claude/skills/comment-lint" "$SB/.claude/skills/synced" "$SB/.claude/hooks/lib" "$SB/.claude/hooks/state/logs" "$SB/.config/lrm-hubs/roles" "$SB/external-skill"
echo old-skill > "$SB/.claude/skills/comment-lint/SKILL.md"
echo bundle > "$SB/.claude/skills/synced/bundle.txt"
ln -s "$SB/external-skill" "$SB/.claude/skills/ext-link"
echo oldlib > "$SB/.claude/hooks/lib/common.sh"
echo log-line > "$SB/.claude/hooks/state/logs/hook.log"
echo old-rules > "$SB/.claude/CLAUDE.md"
echo DUMMY=1 > "$SB/.config/lrm-hubs/slack-bot.env"
echo local-role > "$SB/.config/lrm-hubs/roles/role-docs.txt"
printf '[user]\n\tname = sandbox\n' > "$SB/.gitconfig"
mkdir -p "$SB/.config/claude-config"
printf 'OWNER_NAME=VAL_OWNER\nPERSON_FULL=VAL_PERSON_FULL\nPERSON_SHORT=VAL_PERSON_SHORT\nDM_CHANNEL=VAL&DM_CHANNEL\n' > "$SB/.config/claude-config/values.env"

# 현재 설정 = 기본값 + Orca 훅 15개(이벤트 앞·뒤 섞음) + 상태줄. 기본값 훅 7개와 합쳐 22개
ORCA_CMD='sh -c "$HOME/.orca/agent-hooks/claude-hook.sh"'
mk_current() {
  jq --arg c "$ORCA_CMD" '
    .statusLine = {type: "command", command: $c}
    | .hooks as $h
    | reduce ("PreToolUse PostToolUse UserPromptSubmit Stop SessionStart SessionEnd StopFailure SubagentStart SubagentStop TeammateIdle PostToolUseFailure PermissionRequest PostCompact Elicitation ElicitationResult" | split(" ")[]) as $e
        (.; .hooks[$e] = (if ($e|test("^(PreToolUse|Stop)$")) then [{hooks:[{type:"command",command:$c}]}] + ($h[$e] // []) else ($h[$e] // []) + [{hooks:[{type:"command",command:$c}]}] end))
  ' "$REPO/claude/settings.base.json" | sed "s#__HOME__#$SB#g"
}
# 같은 계층 안의 순서·중복 제거를 검출하도록 Orca 훅을 더하고 1개는 중복시킴. 내 훅은 저장소 기본값이 기준이라 더하지 않음
add_extras() {
  jq --arg c "$ORCA_CMD" '
    .hooks.PreToolUse += [{hooks:[{type:"command",command:($c + " # b")}]}]
    | .hooks.PostToolUse += [{hooks:[{type:"command",command:$c}]}, {hooks:[{type:"command",command:$c}]}, {hooks:[{type:"command",command:($c + " # b")}]}]'
}
mk_current | add_extras > "$SB/.claude/settings.json"
CUR_BYTES=$(shasum "$SB/.claude/settings.json" | cut -d' ' -f1)
cp "$SB/.claude/settings.json" "$SB/settings.original"
hooks_of() { jq '[.hooks[]?[]] | length' "$1"; }
[ "$(hooks_of "$SB/.claude/settings.json")" = 26 ] && ok "준비: 현재 설정 훅 26개(기본 7+Orca 15+추가 3+중복 1)" || ko "준비" "훅 수 $(hooks_of "$SB/.claude/settings.json")"
SKILLS_BEFORE=$(ls "$SB/.claude/skills" | wc -l | tr -d ' ')

# ---- 1. dry-run: 아무것도 바뀌지 않음 --------------------------------------
before=$(snapshot "$SB")
dry=$("$INSTALL" --dry-run 2>&1); rc=$?
after=$(snapshot "$SB")
[ "$rc" = 0 ] && [ "$before" = "$after" ] && ok "dry-run 무변경" || ko "dry-run 무변경" "rc=$rc 지문 불일치"
want=$(echo "$entries" | wc -l | tr -d ' ')
got=$(echo "$dry" | grep -cE '^\[dry-run\] (LINK|BACKUP\+LINK|MERGE|COPY|RENDER|SKIP) ')
[ "$got" = "$want" ] && ok "dry-run 항목 수 = links.tsv ($want)" || ko "dry-run 항목 수" "출력 $got, 목록 $want"
[ ! -d "$SB/.claude-config-backup" ] && ok "dry-run 백업 폴더 미생성" || ko "dry-run 백업 폴더" "생성됨"

# ---- 2. 설치 ---------------------------------------------------------------
"$INSTALL" >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && ok "설치 종료 코드 0" || ko "설치" "rc=$rc"
check "점검 모드 통과" "$INSTALL" --check
linkbad=0; while IFS=$'\t' read -r src dst mode; do
  [ "$mode" = link ] || continue
  [ -L "$SB/$dst" ] && [ -e "$SB/$dst" ] && [ "$(readlink "$SB/$dst")" = "$REPO/$src" ] || { linkbad=1; ko "링크" "$dst"; }
done <<< "$entries"
[ "$linkbad" = 0 ] && ok "링크 전부 정상(원본 가리킴)"
[ "$(ls "$SB/.claude/skills" | wc -l | tr -d ' ')" -ge "$SKILLS_BEFORE" ] && [ -d "$SB/.claude/skills/synced" ] && [ -L "$SB/.claude/skills/ext-link" ] && ok "synced 폴더·외부 링크 보존" || ko "synced·외부 링크" "사라짐"
[ -f "$SB/.claude/hooks/state/logs/hook.log" ] && ok "state 폴더 제자리(기존 로그 유지)" || ko "state" "로그 없음"
[ ! -e "$REPO/claude/hooks/state" ] && ok "저장소에 state 폴더 없음" || ko "저장소 state" "생김"
[ "$(cat "$SB/.config/lrm-hubs/slack-bot.env")" = "DUMMY=1" ] && [ ! -L "$SB/.config/lrm-hubs/slack-bot.env" ] && ok "비밀 파일 미접촉" || ko "비밀 파일" "바뀜"
rend_bad=0
while IFS=$'\t' read -r src dst mode; do
  [ "$mode" = render ] || continue
  if [ -L "$SB/$dst" ] || [ ! -f "$SB/$dst" ] || grep -qE '__(OWNER_NAME|PERSON_FULL|PERSON_SHORT|DM_CHANNEL)__' "$SB/$dst"; then rend_bad=1; ko "render" "$dst"; fi
done <<< "$entries"
[ "$rend_bad" = 0 ] && ok "render 항목: 실제 파일·자리표시 전부 치환"
grep -qF 'VAL&DM_CHANNEL' "$SB/.config/lrm-hubs/roles/role-docs.txt" && grep -q 'VAL_PERSON_FULL' "$SB/.config/lrm-hubs/roles/role-tutorial.txt" && ok "render: 값 파일 값이 들어감" || ko "render 값" "누락"
grep -q "$SB" "$SB/Library/LaunchAgents/ai.lrm.slackpoll.plist" && ! grep -q '__HOME__' "$SB/Library/LaunchAgents/ai.lrm.slackpoll.plist" && ok "plist __HOME__ 치환" || ko "plist 치환" ""
[ "$(git config --global core.hooksPath)" = "~/.config/git/hooks" ] && [ "$(git config --global pull.rebase)" = true ] && [ "$(git config --global merge.ff)" = false ] && ok "git 설정 4개 키" || ko "git 설정" ""
grep -q 'name = sandbox' "$SB/.gitconfig" && ok "git 사용자 값 보존" || ko "git 사용자 값" "사라짐"
BK=$(ls -d "$SB/.claude-config-backup"/*/ | head -1); BK=${BK%/}
[ "$(ls -d "$SB/.claude-config-backup"/*/ | wc -l | tr -d ' ')" = 1 ] && ok "백업 폴더 1개" || ko "백업 폴더 수" ""
[ -n "$(grep -rl '^old-skill$' "$BK/files" 2>/dev/null | head -1)" ] && ok "기존 스킬 폴더 백업됨" || ko "스킬 백업" "내용 없음"
[ -n "$(grep -rl '^local-role$' "$BK/files" 2>/dev/null | head -1)" ] && ok "render 대상의 기존 로컬 파일 백업됨" || ko "role 백업" "내용 없음"

# ---- 3. 설정 병합: 훅 보존·순서·바이트 동일 --------------------------------
merge_checks() {
  local label="$1" orig="$2" cur="$SB/.claude/settings.json" exp
  local def='def orca: tostring | contains(".orca/agent-hooks"); def dd: reduce .[] as $i ([]; if any(.[]; . == $i) then . else . + [$i] end);'
  # 기대값: 이벤트마다 [내 훅(순서 유지, 중복 제거)] + [Orca 훅(순서 유지, 중복 제거)]
  exp=$(jq -S -c "$def"' .hooks | map_values(([.[] | select(orca | not)] | dd) + ([.[] | select(orca)] | dd))' "$orig")
  [ "$exp" = "$(jq -S -c '.hooks' "$cur")" ] && ok "$label 훅: 이벤트별 [내 훅]+[Orca 훅] 순서 유지·중복 제거" || ko "$label 훅 순서·내용" "기대와 다름"
  [ "$(jq -c '.statusLine' "$orig")" = "$(jq -c '.statusLine' "$cur")" ] && ok "$label 상태줄 보존" || ko "$label 상태줄" "다름"
  [ "$(jq -S -c 'del(.hooks,.statusLine)' "$orig")" = "$(jq -S -c 'del(.hooks,.statusLine)' "$cur")" ] && ok "$label 나머지 설정 동일" || ko "$label 나머지 설정" "다름"
}
merge_checks "병합" "$SB/settings.original"
h1=$(shasum "$SB/.claude/settings.json" | cut -d' ' -f1)
"$INSTALL" >/dev/null 2>&1
h2=$(shasum "$SB/.claude/settings.json" | cut -d' ' -f1)
[ "$h1" = "$h2" ] && ok "재설치 시 settings.json 바이트 동일" || ko "재설치 바이트" "달라짐"

# ---- 4. 재설치 멱등 --------------------------------------------------------
before=$(snapshot "$SB")
out=$("$INSTALL" 2>&1); rc=$?
after=$(snapshot "$SB")
[ "$rc" = 0 ] && [ "$before" = "$after" ] && ok "재설치 무변경(멱등)" || ko "재설치 멱등" "rc=$rc 지문 불일치"
[ "$(ls -d "$SB/.claude-config-backup"/*/ | wc -l | tr -d ' ')" = 1 ] && ok "재설치 후에도 백업 폴더 1개" || ko "재설치 백업 폴더" "늘어남"
echo "$out" | grep -q '요약: 링크 0, 병합 0, 복사 0' && ok "재설치 요약 0건 변경" || ko "재설치 요약" "$(echo "$out" | tail -2 | head -1)"

# ---- 5. 훅 state 경로 -------------------------------------------------------
st=$(bash -c 'source "$HOME/.claude/hooks/lib/common.sh"; printf "%s" "$STATE_DIR"' 2>/dev/null)
[ "$st" = "$SB/.claude/hooks/state" ] && ok "링크 경로로 읽은 STATE_DIR = 홈 쪽" || ko "STATE_DIR" "$st"
[ ! -e "$REPO/claude/hooks/state" ] && ok "common.sh 실행 뒤에도 저장소에 state 없음" || ko "저장소 state" "생김"

# ---- 5b. git 훅이 링크 경로로 동작 -------------------------------------------
GT=$(mktemp -d "${TMPDIR:-/tmp}/cc-gittest.XXXXXX")
(cd "$GT" && git init -q -b main . && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m "초기" >/dev/null 2>&1)
git -C "$GT" rev-parse -q --verify HEAD >/dev/null 2>&1 && ok "git 훅 링크 상태에서 커밋 가능" || ko "git 커밋" "훅이 커밋을 막음"
(cd "$GT" && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m "둘째" >/dev/null 2>&1)
if (cd "$GT" && git rebase --force-rebase HEAD~1 >/dev/null 2>&1); then ko "pre-rebase 보호" "main 에서 rebase 가 통과함"; else
  if (cd "$GT" && ALLOW_SHARED_REBASE=1 git rebase --force-rebase HEAD~1 >/dev/null 2>&1); then ok "pre-rebase 가 공유 브랜치 rebase 차단(우회 변수로는 통과)"; else ko "pre-rebase" "훅이 아닌 다른 이유로 실패"; fi
fi
rm -rf "${GT:?}"

# ---- 6. 되돌리기 -----------------------------------------------------------
# 복원 전 지문: 복원 2회·부분 실패 재실행이 같은 최종 상태에 도달하는지 비교
cp -a "$BK" "$SB.bk-part"
head -8 "$BK/backup.tsv" > "$SB.bk-part/backup.tsv"
"$INSTALL" --restore "$BK" >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && ok "복원 종료 코드 0" || ko "복원" "rc=$rc"
[ ! -L "$SB/.claude/skills/comment-lint" ] && [ "$(cat "$SB/.claude/skills/comment-lint/SKILL.md")" = "old-skill" ] && ok "복원: 스킬 폴더 실제 폴더로 복귀" || ko "복원 스킬" ""
[ "$(shasum "$SB/.claude/settings.json" | cut -d' ' -f1)" = "$CUR_BYTES" ] && ok "복원: settings.json 바이트 동일" || ko "복원 settings" "다름"
[ ! -L "$SB/.claude/hooks/lib" ] && [ "$(cat "$SB/.claude/hooks/lib/common.sh")" = "oldlib" ] && ok "복원: hooks/lib 복귀" || ko "복원 hooks/lib" ""
[ "$(cat "$SB/.config/lrm-hubs/roles/role-docs.txt")" = "local-role" ] && ok "복원: render 대상 원래 로컬 파일로 복귀" || ko "복원 role-docs" ""
[ -z "$(find "$SB/.claude" "$SB/.config" -type l ! -name ext-link 2>/dev/null)" ] && ok "복원: 저장소를 가리키는 링크 0개" || ko "복원 링크 잔존" "$(find "$SB/.claude" "$SB/.config" -type l ! -name ext-link | head -3)"
[ -f "$SB/.claude/hooks/state/logs/hook.log" ] && [ -d "$SB/.claude/skills/synced" ] && [ -L "$SB/.claude/skills/ext-link" ] && ok "복원: state·synced·외부 링크 그대로" || ko "복원 부수 영향" ""
grep -q 'name = sandbox' "$SB/.gitconfig" && ! git config --global --get core.hooksPath >/dev/null 2>&1 && ok "복원: .gitconfig 원상" || ko "복원 gitconfig" ""
[ ! -e "$SB/.config/git" ] && [ ! -e "$SB/Library" ] && [ ! -e "$SB/.claude/skills/orca-hub-create" ] && ok "복원: 설치가 새로 만든 빈 폴더 제거" || ko "복원 빈 폴더" "$(ls -d "$SB/.config/git" "$SB/Library" "$SB/.claude/skills/orca-hub-create" 2>/dev/null | tr '\n' ' ')"
[ -d "$SB/.config/lrm-hubs/roles" ] && [ -d "$SB/.config/claude-config" ] && ok "복원: 원래 있던 폴더는 유지" || ko "복원 폴더 유지" ""

# 복원 2회 연속: 백업이 남아 있어 같은 결과여야 함(1회차가 백업을 소모하면 파일이 사라짐)
before=$(snapshot "$SB")
"$INSTALL" --restore "$BK" >/dev/null 2>&1; rc=$?
after=$(snapshot "$SB")
[ "$rc" = 0 ] && [ "$before" = "$after" ] && [ "$(cat "$SB/.claude/CLAUDE.md")" = "old-rules" ] && ok "복원 2회 연속: 무변경·CLAUDE.md 보존" || ko "복원 2회" "rc=$rc 지문 $([ "$before" = "$after" ] && echo 같음 || echo 다름)"
[ -f "$BK/backup.tsv" ] && [ -n "$(grep -rl '^old-rules$' "$BK/files" 2>/dev/null | head -1)" ] && ok "복원 뒤에도 백업 원본 보존" || ko "복원 백업 보존" "소모됨"

# 부분 실패 재실행: 앞 8줄만 복원한 상태에서 전체 복원을 다시 실행
"$INSTALL" >/dev/null 2>&1
"$INSTALL" --restore "$SB.bk-part" >/dev/null 2>&1
"$INSTALL" --restore "$BK" >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && [ "$(cat "$SB/.claude/CLAUDE.md")" = "old-rules" ] && [ "$(cat "$SB/.claude/skills/comment-lint/SKILL.md")" = "old-skill" ] && [ "$(shasum "$SB/.claude/settings.json" | cut -d' ' -f1)" = "$CUR_BYTES" ] && ok "부분 복원 뒤 전체 복원 재실행: 원상" || ko "부분 실패 재실행" "rc=$rc"
"$INSTALL" >/dev/null 2>&1

# 백업 원본이 사라진 manifest 는 아무것도 지우지 않고 중단
BK2=$(ls -d "$SB/.claude-config-backup"/*/ | tail -1); BK2=${BK2%/}
victim=$(awk -F'\t' '$1=="move" {v=$3} END {print v}' "$BK2/backup.tsv")
if [ -n "$victim" ]; then
  mv "$victim" "$victim.gone"
  before=$(snapshot "$SB")
  "$INSTALL" --restore "$BK2" >/dev/null 2>&1; rc=$?
  after=$(snapshot "$SB")
  [ "$rc" != 0 ] && [ "$before" = "$after" ] && ok "백업 원본 없으면 복원 중단·무변경" || ko "백업 누락 복원" "rc=$rc"
  mv "$victim.gone" "$victim"
else ko "백업 누락 복원" "move 항목 없음"; fi
"$INSTALL" --restore "$BK2" >/dev/null 2>&1
rm -rf "${SB:?}.bk-part"

# ---- 6b. 백업 실패 시 중단 ---------------------------------------------------
SB2=$(mktemp -d "${TMPDIR:-/tmp}/cc-sandbox2.XXXXXX")
mkdir -p "$SB2/.claude/skills/comment-lint"; echo keep > "$SB2/.claude/skills/comment-lint/SKILL.md"; echo old > "$SB2/.claude/CLAUDE.md"
HOME="$SB2" CLAUDE_CONFIG_BACKUP_ROOT=/dev/null/blocked "$INSTALL" >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && [ "$(cat "$SB2/.claude/CLAUDE.md")" = old ] && [ ! -L "$SB2/.claude/CLAUDE.md" ] && [ -z "$(find "$SB2/.claude/skills/comment-lint" -type l)" ] && ok "백업 불가 시 링크 없이 중단(기존 폴더 안 링크 없음)" || ko "백업 실패 중단" "rc=$rc"
printf '{bad' > "$SB2/.claude/settings.json"; rm -f "$SB2/.claude/CLAUDE.md"
HOME="$SB2" "$INSTALL" --dry-run >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && ok "깨진 settings.json 이면 중단" || ko "깨진 settings" "rc=$rc"
rm -rf "${SB2:?}"

# ---- 6d. 백업 이동(mv) 실패 시 링크 없이 중단 ---------------------------------
SB4=$(mktemp -d "${TMPDIR:-/tmp}/cc-sandbox4.XXXXXX")
mkdir -p "$SB4/.claude/skills/comment-lint" "$SB4/.config/claude-config"
echo keep > "$SB4/.claude/skills/comment-lint/SKILL.md"
cp "$SB/.config/claude-config/values.env" "$SB4/.config/claude-config/values.env"
# 디렉터리 이동은 그 디렉터리의 쓰기 권한이 필요해서, 읽기 전용이면 mv 만 실패함
chmod 555 "$SB4/.claude/skills/comment-lint"
out=$(HOME="$SB4" "$INSTALL" 2>&1); rc=$?
chmod 755 "$SB4/.claude/skills/comment-lint"
[ "$rc" != 0 ] && echo "$out" | grep -q '백업 이동 실패' && [ -z "$(find "$SB4/.claude/skills/comment-lint" -type l)" ] && [ "$(cat "$SB4/.claude/skills/comment-lint/SKILL.md")" = keep ] && ok "mv 실패 시 '백업 이동 실패'로 링크 없이 중단" || ko "mv 실패 중단" "rc=$rc"
rm -rf "${SB4:?}"

# ---- 6c. 값 파일 없으면 변경 없이 중단 ---------------------------------------
SB3=$(mktemp -d "${TMPDIR:-/tmp}/cc-sandbox3.XXXXXX")
echo keep > "$SB3/CLAUDE.keep"
before=$(snapshot "$SB3")
HOME="$SB3" "$INSTALL" >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && [ "$before" = "$(snapshot "$SB3")" ] && ok "값 파일 없으면 아무것도 바꾸지 않고 중단" || ko "값 파일 없음" "rc=$rc"
mkdir -p "$SB3/.config/claude-config"; printf 'OWNER_NAME=a\n' > "$SB3/.config/claude-config/values.env"
HOME="$SB3" "$INSTALL" --dry-run >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && ok "값 파일에 빈 키가 있으면 중단" || ko "값 파일 빈 키" "rc=$rc"
rm -rf "${SB3:?}"

# ---- 6e. 라이브가 저장소보다 앞서면 변경 전 중단 ------------------------------
SB5=$(mktemp -d "${TMPDIR:-/tmp}/cc-sandbox5.XXXXXX")
mkdir -p "$SB5/.config/lrm-hubs" "$SB5/.config/claude-config" "$SB5/.claude"
cp "$SB/.config/claude-config/values.env" "$SB5/.config/claude-config/values.env"
# 의도된 치환(restart_hub 경로)만 다른 파일은 통과해야 하고, 내용이 다른 slack_poll 은 걸려야 함
sed 's#\${HUB_ROOT:-\$HOME/orca/workspaces/LoveRevolutionMaker}#/Users/someone/orca/workspaces/LoveRevolutionMaker#' "$REPO/hubs/scripts/restart_hub.sh" > "$SB5/.config/lrm-hubs/restart_hub.sh"
echo "# newer live" > "$SB5/.config/lrm-hubs/slack_poll.sh"
before=$(snapshot "$SB5")
out=$(env -u CLAUDE_CONFIG_ALLOW_LIVE_DIFF HOME="$SB5" "$INSTALL" --diff-live 2>&1); rc=$?
[ "$rc" = 1 ] && echo "$out" | grep -q 'LIVE-NEWER .config/lrm-hubs/slack_poll.sh' && ! echo "$out" | grep -q 'restart_hub' && ok "--diff-live: 라이브가 앞선 파일만 지적(의도된 치환은 통과)" || ko "diff-live" "rc=$rc $(echo "$out" | head -3 | tr '\n' ' ')"
env -u CLAUDE_CONFIG_ALLOW_LIVE_DIFF HOME="$SB5" "$INSTALL" >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && [ "$before" = "$(snapshot "$SB5")" ] && ok "라이브가 앞서면 설치가 변경 없이 중단" || ko "라이브 우선 중단" "rc=$rc"
env -u CLAUDE_CONFIG_ALLOW_LIVE_DIFF HOME="$SB5" "$INSTALL" --dry-run >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && ok "dry-run 도 라이브 우선이면 중단" || ko "dry-run 라이브 우선" "rc=$rc"
env -u CLAUDE_CONFIG_ALLOW_LIVE_DIFF HOME="$SB5" "$INSTALL" --allow-live-diff >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && [ -L "$SB5/.config/lrm-hubs/slack_poll.sh" ] && ok "--allow-live-diff 로 진행하면 백업 후 링크" || ko "allow-live-diff" "rc=$rc"
rm -rf "${SB5:?}"

# ---- 6f. 값 파일 공백만 있는 값 ----------------------------------------------
SB6=$(mktemp -d "${TMPDIR:-/tmp}/cc-sandbox6.XXXXXX")
mkdir -p "$SB6/.config/claude-config"
printf 'OWNER_NAME=a\nPERSON_FULL=b\nPERSON_SHORT=c\nDM_CHANNEL=   \n' > "$SB6/.config/claude-config/values.env"
HOME="$SB6" "$INSTALL" --dry-run >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && ok "값이 공백뿐이면 중단" || ko "공백 값" "rc=$rc"
rm -rf "${SB6:?}"

# ---- 7. 임의 설정 사본 검증(선택) -------------------------------------------
if [ -n "${SETTINGS_SAMPLE:-}" ] && [ -f "$SETTINGS_SAMPLE" ]; then
  sed "s#$REAL_HOME#$SB#g" "$SETTINGS_SAMPLE" > "$SB/.claude/settings.json"; cp "$SB/.claude/settings.json" "$SB/settings.sample"
  "$INSTALL" >/dev/null 2>&1
  merge_checks "사본" "$SB/settings.sample"
  s1=$(shasum "$SB/.claude/settings.json" | cut -d' ' -f1); "$INSTALL" >/dev/null 2>&1
  [ "$s1" = "$(shasum "$SB/.claude/settings.json" | cut -d' ' -f1)" ] && ok "사본 재설치 바이트 동일" || ko "사본 재설치" "달라짐"
fi

# ---- 8. 비밀 패턴·제외 대상 -------------------------------------------------
cd "$REPO" || exit 1
tracked=$(git ls-files; git ls-files --others --exclude-standard)
hits=$(echo "$tracked" | sort -u | while read -r f; do [ -f "$f" ] && grep -InE '(xox[a-z]-[A-Za-z0-9]|ghp_[A-Za-z0-9]{10}|sk-[A-Za-z0-9]{20}|Bearer [A-Za-z0-9]{20}|AKIA[0-9A-Z]{12}|-----BEGIN [A-Z ]*PRIVATE KEY)' "$f" | cut -d: -f1 | sed "s#^#$f:#"; done)
[ -z "$hits" ] && ok "비밀 패턴 일치 0건" || ko "비밀 패턴" "파일:줄 → $(echo "$hits" | head -5 | tr '\n' ' ')"
forbidden=$(echo "$tracked" | grep -E '(\.env$|\.bak|__pycache__|/state/|\.pem$|\.p8$|\.key$|\.keystore$|\.jks$|credentials|slack-(inbox|sent|poll-state)|HANDOFF|decisions\.tsv|skills/synced/)' || true)
[ -z "$forbidden" ] && ok "제외 대상 파일 0개" || ko "제외 대상" "$(echo "$forbidden" | head -5 | tr '\n' ' ')"
abs=$(echo "$tracked" | sort -u | while read -r f; do [ -f "$f" ] && grep -In '/Users/' "$f" | cut -d: -f1 | sed "s#^#$f:#"; done | grep -vE '^(tests/|live-diff.tsv)' || true)
[ -z "$abs" ] && ok "하드코딩 홈 경로 0건(tests·live-diff.tsv 제외)" || ko "하드코딩 경로" "$(echo "$abs" | head -5 | tr '\n' ' ')"

# 실명·Slack ID: 원문이 이 스캔에 걸리지 않도록 문자열을 쪼개 둠. 값은 출력하지 않고 파일:줄만 표시
n1='정''오'; n2='강찬''형'; n3='D0C14''GXPU4F'
pii=$(echo "$tracked" | sort -u | while read -r f; do [ -f "$f" ] && grep -InE "$n1|$n2|$n3|\b[UDCW]0[A-Z0-9]{8,10}\b" "$f" | cut -d: -f1 | sed "s#^#$f:#"; done)
[ -z "$pii" ] && ok "실명·Slack ID·채널 패턴 0건(전 파일)" || ko "실명·ID 패턴" "파일:줄 → $(echo "$pii" | head -5 | tr '\n' ' ')"
# 템플릿의 자리표시는 values.env.example 의 키로만 구성되어야 함
keys=$(grep -vE '^(#|$)' "$REPO/values.env.example" | cut -d= -f1 | sort -u)
used=$(echo "$tracked" | grep '\.tmpl$' | while read -r f; do grep -oE '__[A-Z_]+__' "$f"; done | grep -vx '__HOME__' | sed 's/^__//; s/__$//' | sort -u)
[ -z "$(comm -13 <(echo "$keys") <(echo "$used"))" ] && ok "템플릿 자리표시 ⊆ values.env.example 키" || ko "템플릿 키" "예시에 없는 키 사용"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
