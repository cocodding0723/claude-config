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

# ---- 샌드박스 준비 ---------------------------------------------------------
SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-sandbox.XXXXXX")
trap 'rm -rf "$SB"' EXIT
REAL_HOME="$HOME"
export HOME="$SB"
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
mk_current > "$SB/.claude/settings.json"
CUR_BYTES=$(shasum "$SB/.claude/settings.json" | cut -d' ' -f1)
cp "$SB/.claude/settings.json" "$SB/settings.original"
hooks_of() { jq '[.hooks[]?[]] | length' "$1"; }
[ "$(hooks_of "$SB/.claude/settings.json")" = 22 ] && ok "준비: 현재 설정 훅 22개" || ko "준비" "훅 수 $(hooks_of "$SB/.claude/settings.json")"
SKILLS_BEFORE=$(ls "$SB/.claude/skills" | wc -l | tr -d ' ')

# ---- 1. dry-run: 아무것도 바뀌지 않음 --------------------------------------
before=$(snapshot "$SB")
dry=$("$INSTALL" --dry-run 2>&1); rc=$?
after=$(snapshot "$SB")
[ "$rc" = 0 ] && [ "$before" = "$after" ] && ok "dry-run 무변경" || ko "dry-run 무변경" "rc=$rc 지문 불일치"
want=$(echo "$entries" | wc -l | tr -d ' ')
got=$(echo "$dry" | grep -cE '^\[dry-run\] (LINK|BACKUP\+LINK|MERGE|COPY|SEED|SKIP) ')
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
[ "$(cat "$SB/.config/lrm-hubs/roles/role-docs.txt")" = "local-role" ] && ok "seed: 로컬 role-docs 유지" || ko "seed" "덮어씀"
grep -q "$SB" "$SB/Library/LaunchAgents/ai.lrm.slackpoll.plist" && ! grep -q '__HOME__' "$SB/Library/LaunchAgents/ai.lrm.slackpoll.plist" && ok "plist __HOME__ 치환" || ko "plist 치환" ""
[ "$(git config --global core.hooksPath)" = "~/.config/git/hooks" ] && [ "$(git config --global pull.rebase)" = true ] && [ "$(git config --global merge.ff)" = false ] && ok "git 설정 4개 키" || ko "git 설정" ""
grep -q 'name = sandbox' "$SB/.gitconfig" && ok "git 사용자 값 보존" || ko "git 사용자 값" "사라짐"
BK=$(ls -d "$SB/.claude-config-backup"/*/ | head -1); BK=${BK%/}
[ "$(ls -d "$SB/.claude-config-backup"/*/ | wc -l | tr -d ' ')" = 1 ] && ok "백업 폴더 1개" || ko "백업 폴더 수" ""
[ "$(cat "$BK/files/"*/SKILL.md 2>/dev/null | head -1)" = "old-skill" ] && ok "기존 스킬 폴더 백업됨" || ko "스킬 백업" "내용 없음"

# ---- 3. 설정 병합: 훅 보존·순서·바이트 동일 --------------------------------
merge_checks() {
  local label="$1" orig="$2" cur="$SB/.claude/settings.json"
  [ "$(hooks_of "$orig")" = "$(hooks_of "$cur")" ] && ok "$label 훅 수 보존 ($(hooks_of "$cur")개)" || ko "$label 훅 수" "$(hooks_of "$orig") → $(hooks_of "$cur")"
  # 이벤트별로 원본의 상대 순서가 유지되는지: 원본 순서에서 결과가 부분수열이 되는 경우만 통과(전체 집합 동일 + Orca 순서 유지)
  local o_orca c_orca
  o_orca=$(jq -c '[.hooks | to_entries[] | .key as $k | .value[] | select(tostring|contains(".orca/agent-hooks")) | $k] ' "$orig")
  c_orca=$(jq -c '[.hooks | to_entries[] | .key as $k | .value[] | select(tostring|contains(".orca/agent-hooks")) | $k]' "$cur")
  [ "$(echo "$o_orca" | jq -c 'sort')" = "$(echo "$c_orca" | jq -c 'sort')" ] && ok "$label Orca 훅 집합 동일" || ko "$label Orca 훅" "집합 다름"
  local o_mine c_mine
  o_mine=$(jq -c '[.hooks | to_entries[] | .key as $k | .value[] | select(tostring|contains(".orca/agent-hooks")|not) | [$k, .]]' "$orig")
  c_mine=$(jq -c '[.hooks | to_entries[] | .key as $k | .value[] | select(tostring|contains(".orca/agent-hooks")|not) | [$k, .]]' "$cur")
  [ "$(echo "$o_mine" | jq -c 'sort')" = "$(echo "$c_mine" | jq -c 'sort')" ] && ok "$label 내 훅 집합 동일" || ko "$label 내 훅" "집합 다름"
  [ "$(jq -c '.statusLine' "$orig")" = "$(jq -c '.statusLine' "$cur")" ] && ok "$label 상태줄 보존" || ko "$label 상태줄" "다름"
  [ "$(jq -c 'del(.hooks,.statusLine)' "$orig")" = "$(jq -c 'del(.hooks,.statusLine)' "$cur")" ] && ok "$label 나머지 설정 동일" || ko "$label 나머지 설정" "다름"
  # 이벤트별 내 훅 상대 순서: 결과의 내 훅 순서가 저장소 기본값 순서와 일치
  [ "$(jq -c '[.hooks|to_entries[]|.key as $k|.value[]|select(tostring|contains(".orca/agent-hooks")|not)|$k]' "$cur")" = "$(jq -c '[.hooks|to_entries[]|.key as $k|.value[]|$k]' "$REPO/claude/settings.base.json")" ] && ok "$label 내 훅 순서 = 기본값 순서" || ko "$label 내 훅 순서" "다름"
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
rm -rf "$GT"

# ---- 6. 되돌리기 -----------------------------------------------------------
"$INSTALL" --restore "$BK" >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && ok "복원 종료 코드 0" || ko "복원" "rc=$rc"
[ ! -L "$SB/.claude/skills/comment-lint" ] && [ "$(cat "$SB/.claude/skills/comment-lint/SKILL.md")" = "old-skill" ] && ok "복원: 스킬 폴더 실제 폴더로 복귀" || ko "복원 스킬" ""
[ "$(shasum "$SB/.claude/settings.json" | cut -d' ' -f1)" = "$CUR_BYTES" ] && ok "복원: settings.json 바이트 동일" || ko "복원 settings" "다름"
[ ! -L "$SB/.claude/hooks/lib" ] && [ "$(cat "$SB/.claude/hooks/lib/common.sh")" = "oldlib" ] && ok "복원: hooks/lib 복귀" || ko "복원 hooks/lib" ""
[ -z "$(find "$SB/.claude" "$SB/.config" -type l ! -name ext-link 2>/dev/null)" ] && ok "복원: 저장소를 가리키는 링크 0개" || ko "복원 링크 잔존" "$(find "$SB/.claude" "$SB/.config" -type l ! -name ext-link | head -3)"
[ -f "$SB/.claude/hooks/state/logs/hook.log" ] && [ -d "$SB/.claude/skills/synced" ] && [ -L "$SB/.claude/skills/ext-link" ] && ok "복원: state·synced·외부 링크 그대로" || ko "복원 부수 영향" ""
grep -q 'name = sandbox' "$SB/.gitconfig" && ! git config --global --get core.hooksPath >/dev/null 2>&1 && ok "복원: .gitconfig 원상" || ko "복원 gitconfig" ""

# ---- 6b. 백업 실패 시 중단 ---------------------------------------------------
SB2=$(mktemp -d "${TMPDIR:-/tmp}/cc-sandbox2.XXXXXX")
mkdir -p "$SB2/.claude/skills/comment-lint"; echo keep > "$SB2/.claude/skills/comment-lint/SKILL.md"; echo old > "$SB2/.claude/CLAUDE.md"
HOME="$SB2" CLAUDE_CONFIG_BACKUP_ROOT=/dev/null/blocked "$INSTALL" >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && [ "$(cat "$SB2/.claude/CLAUDE.md")" = old ] && [ ! -L "$SB2/.claude/CLAUDE.md" ] && [ -z "$(find "$SB2/.claude/skills/comment-lint" -type l)" ] && ok "백업 불가 시 링크 없이 중단(기존 폴더 안 링크 없음)" || ko "백업 실패 중단" "rc=$rc"
printf '{bad' > "$SB2/.claude/settings.json"; rm -f "$SB2/.claude/CLAUDE.md"
HOME="$SB2" "$INSTALL" --dry-run >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && ok "깨진 settings.json 이면 중단" || ko "깨진 settings" "rc=$rc"
rm -rf "$SB2"

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
abs=$(echo "$tracked" | sort -u | while read -r f; do [ -f "$f" ] && grep -In '/Users/' "$f" | cut -d: -f1 | sed "s#^#$f:#"; done | grep -v '^tests/' || true)
[ -z "$abs" ] && ok "하드코딩 홈 경로 0건(tests 제외)" || ko "하드코딩 경로" "$(echo "$abs" | head -5 | tr '\n' ' ')"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
