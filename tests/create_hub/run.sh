#!/usr/bin/env bash
# create_hub.sh 시험. 실제 Orca·게임 저장소는 쓰지 않음(임시 git 저장소, 가짜 orca).
# 사용: tests/create_hub/run.sh   종료 코드: 0 전부 통과, 1 실패
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPT="$HERE/../../skills/orca-hub-create/scripts/create_hub.sh"
SB=$(mktemp -d); trap 'rm -rf -- "${SB:?}"' EXIT
PASS=0; FAIL=0
ok() { PASS=$((PASS + 1)); echo "PASS  $1"; }
ko() { FAIL=$((FAIL + 1)); echo "FAIL  $1 — $2"; }

# 가짜 orca: 살아 있는 터미널 없음, 기동하면 진행 표시가 보이는 터미널 하나
mkdir -p "$SB/bin" "$SB/home" "$SB/ws"
cat > "$SB/bin/orca" <<'ORCA'
#!/bin/bash
[ ! -f "$HOME/fail-orca" ] || exit 17
case "$1 $2" in
  "terminal list") echo '{"result":{"terminals":[]}}' ;;
  "terminal read") echo '{"result":{"terminal":{"tail":["esc to interrupt"]}}}' ;;
  "agent launch") echo '{"handle":"term_test"}' ;;
  *) echo '{}' ;;
esac
ORCA
chmod +x "$SB/bin/orca"
printf '#!/bin/bash\nexit 0\n' > "$SB/bin/sleep"
chmod +x "$SB/bin/sleep"
git init -q -b main "$SB/repo"
mkdir -p "$SB/repo/docs" "$SB/repo/Assets"
echo d > "$SB/repo/docs/a.md"; echo c > "$SB/repo/Assets/a.cs"; echo r > "$SB/repo/README.md"
git -C "$SB/repo" -c user.email=t@t -c user.name=t add -A >/dev/null
git -C "$SB/repo" -c user.email=t@t -c user.name=t commit -q -m init
echo "역할" > "$SB/role.txt"; echo "공통 규칙" > "$SB/common.txt"

run() { HOME="$SB/home" PATH="$SB/bin:$PATH" REPO="$SB/repo" WS_ROOT="$SB/ws" bash "$SCRIPT" --name 시험 --role-file "$SB/role.txt" --base main "$@" 2>&1; }

# 1) 공통 규칙이 어디에도 없으면: 작업 공간을 만들기 전에 종료 4, 원인·조치 문구
out=$(COMMON_FILE="" run --slug none); rc=$?
[ "$rc" = 4 ] && echo "$out" | grep -q "install.sh" && ok "공통 규칙 없음: 종료 4 + 조치 안내" || ko "공통 규칙 없음" "rc=$rc $out"
[ ! -d "$SB/ws/coord-none-hub" ] && ok "공통 규칙 없음: 작업 공간을 만들지 않음" || ko "반쯤 만들어진 허브" "$SB/ws/coord-none-hub 가 남음"
out=$(COMMON_FILE="" run --slug none --dry-run); rc=$?
[ "$rc" = 4 ] && ok "미리 보기 실행도 같은 원인으로 멈춤" || ko "미리 보기 실행" "rc=$rc"

# 잘못 지정한 값은 다른 후보로 넘어가지 않고 멈춤. 빈 --paths 도 작업 공간을 만들기 전에 멈춤
out=$(COMMON_FILE="$SB/no-such.txt" run --slug typo); rc=$?
[ "$rc" = 4 ] && [ ! -d "$SB/ws/coord-typo-hub" ] && ok "없는 COMMON_FILE 지정: 종료 4, 작업 공간 없음" || ko "없는 COMMON_FILE" "rc=$rc $out"
out=$(COMMON_FILE="$SB/common.txt" run --slug empty --paths " "); rc=$?
[ "$rc" = 2 ] && [ ! -d "$SB/ws/coord-empty-hub" ] && ok "빈 --paths: 종료 2, 작업 공간 없음" || ko "빈 --paths" "rc=$rc $out"

# 2) 저장소 사본으로 실행해도 설치본(~/.claude/…/common-rules.txt)을 읽음
mkdir -p "$SB/home/.claude/skills/orca-hub-create/references"
cp "$SB/common.txt" "$SB/home/.claude/skills/orca-hub-create/references/common-rules.txt"
out=$(run --slug inst); rc=$?
[ "$rc" = 0 ] && [ -f "$SB/ws/coord-inst-hub/docs/a.md" ] && ok "설치본 공통 규칙으로 허브 생성" || ko "설치본 공통 규칙" "rc=$rc $out"
if echo "$out" | grep -q '허브 생성·기동 실패'; then ko "정상 종료 안내 없음" "$out"; else ok "정상 종료 안내 없음"; fi
[ ! -e "$SB/ws/coord-inst-hub/Assets" ] && ok "기본 경로는 문서·설정만 받음(코드 없음)" || ko "기본 sparse" "Assets 가 받아짐"

# 3) COMMON_FILE 지정이 먼저. --paths "/*" 는 글자 그대로 들어감(컴퓨터 최상위 폴더 목록으로 풀리지 않음)
out=$(COMMON_FILE="$SB/common.txt" run --slug all --paths "/*"); rc=$?
pat=$(git -C "$SB/ws/coord-all-hub" sparse-checkout list 2>/dev/null | tr '\n' ' ')
[ "$rc" = 0 ] && [ "$pat" = "/* " ] && ok "--paths \"/*\" 가 그대로 들어감" || ko "경로 값 풀림" "rc=$rc 패턴='$pat'"
[ -f "$SB/ws/coord-all-hub/Assets/a.cs" ] && [ -f "$SB/ws/coord-all-hub/README.md" ] && ok "\"/*\" 로 전체 파일을 받음" || ko "전체 받기" "$(ls "$SB/ws/coord-all-hub" 2>/dev/null | tr '\n' ' ')"

# 4) 자동 압축 기준: 허브 설정에 autoCompactWindow, 기존 키 보존, 재실행 동일, off 면 키 없음
S="$SB/ws/coord-ac-hub/.claude/settings.local.json"
mkdir -p "$SB/ws/coord-ac-hub/.claude"; echo '{"foo":{"bar":1},"autoMemoryEnabled":true,"enableArtifact":true,"disableClaudeAiConnectors":false}' > "$S"
# 이미 폴더가 있으면 생성을 건너뛰므로 기존 설정 파일이 남은 채 실행됨
out=$(COMMON_FILE="$SB/common.txt" run --slug ac); rc=$?
v=$(jq -r '[.autoCompactWindow, .enableArtifact, .disableClaudeAiConnectors, .foo.bar, .autoMemoryEnabled, .enabledPlugins["openviking-memory@openviking"]]|@tsv' "$S" 2>/dev/null)
[ "$rc" = 0 ] && [ "$v" = $'400000\tfalse\ttrue\t1\ttrue\tfalse' ] && ok "자동 압축 400000 + 고정 설정 + 기존 키·자동 메모리 보존" || ko "자동 압축 기본값" "rc=$rc v='$v' $out"
jq -e '.autoCompactWindow == 400000 and .enableArtifact == false and .disableClaudeAiConnectors == true and (has("autoMemoryEnabled") | not)' "$SB/ws/coord-inst-hub/.claude/settings.local.json" >/dev/null && ok "설정 없는 허브도 새 기본값 적용, 자동 메모리 키 미추가" || ko "새 설정 파일" "$out"
h1=$(shasum "$S" | cut -c1-12)
out=$(COMMON_FILE="$SB/common.txt" run --slug ac --force); h2=$(shasum "$S" | cut -c1-12)
[ "$h1" = "$h2" ] && ok "다시 실행해도 설정 파일이 같음" || ko "재실행" "$h1 != $h2 $out"
out=$(COMMON_FILE="$SB/common.txt" LRM_HUB_AUTOCOMPACT=off run --slug ac --force)
[ "$(jq 'has("autoCompactWindow")' "$S")" = false ] && [ "$(jq -r .foo.bar "$S")" = 1 ] && ok "LRM_HUB_AUTOCOMPACT=off: 키 제거, 나머지 보존" || ko "off" "$(cat "$S")"
jq -e '.enableArtifact == false and .disableClaudeAiConnectors == true and .autoMemoryEnabled == true' "$S" >/dev/null && ok "압축 off도 고정 설정·자동 메모리 보존" || ko "off 고정 설정" "$(cat "$S")"
out=$(COMMON_FILE="$SB/common.txt" LRM_HUB_AUTOCOMPACT=0 run --slug ac --force); rc=$?
[ "$rc" = 0 ] && [ "$(jq 'has("autoCompactWindow")' "$S")" = false ] && ok "LRM_HUB_AUTOCOMPACT=0: 키 제거" || ko "0" "rc=$rc $out"
out=$(COMMON_FILE="$SB/common.txt" LRM_HUB_AUTOCOMPACT=150000 run --slug ac --force)
[ "$(jq -r .autoCompactWindow "$S")" = 150000 ] && ok "환경 변수로 값 변경" || ko "값 변경" "$(cat "$S")"
out=$(COMMON_FILE="$SB/common.txt" LRM_HUB_AUTOCOMPACT=abc run --slug ac --force); rc=$?
[ "$rc" = 2 ] && ok "잘못된 값은 종료 2" || ko "잘못된 값" "rc=$rc $out"

touch "$SB/home/fail-orca"
COMMON_FILE="$SB/common.txt" HOME="$SB/home" PATH="$SB/bin:$PATH" REPO="$SB/repo" WS_ROOT="$SB/ws" bash "$SCRIPT" --slug ac --name 시험 --role-file "$SB/role.txt" --base main --force >"$SB/failure.out" 2>"$SB/failure.err"
rc=$?
[ "$rc" = 17 ] && grep -Eq '허브 생성·기동 실패: 줄 [0-9]+, 종료 17, 명령: orca worktree set' "$SB/failure.err" && ok "실패 줄·명령·종료 코드 stderr 출력" || ko "실패 안내" "rc=$rc $(cat "$SB/failure.err")"
if grep -q '허브 생성·기동 실패' "$SB/failure.out"; then ko "실패 안내 출력 위치" "stdout에 오류 출력"; else ok "실패 안내 stdout 미출력"; fi
out=$(COMMON_FILE="$SB/common.txt" run --slug ac); rc=$?
[ "$rc" = 17 ] && echo "$out" | grep -Eq '줄 [0-9]+, 종료 17, 명령: LIVE=\$\(orca terminal list' && ok "명령 치환 실패 위치·명령 출력" || ko "명령 치환 실패 안내" "rc=$rc $out"
[ "$(echo "$out" | grep -c '허브 생성·기동 실패')" = 1 ] && ok "명령 치환 실패 안내 한 번" || ko "명령 치환 안내 중복" "$out"
command_line=${out##*명령: }
[ "${#command_line}" -le 120 ] && ok "실패 명령 최대 120자" || ko "실패 명령 길이" "${#command_line}"
rm "$SB/home/fail-orca"

echo '{invalid' > "$S"
out=$(COMMON_FILE="$SB/common.txt" run --slug ac --force); rc=$?
tail3=$(printf '%s\n' "$out" | tail -3)
[ "$rc" != 0 ] && echo "$tail3" | grep -q 'json.decoder.JSONDecodeError' && ok "잘못된 JSON: tail -3에 원인 보존" || ko "JSON 원인 누락" "rc=$rc $tail3"
last=$(printf '%s\n' "$out" | tail -1)
[[ "$last" == '허브 생성·기동 실패: '*', 명령: python3 '* ]] && ok "heredoc 실패 안내는 마지막 한 줄" || ko "heredoc 안내 위치" "$last"
[ "$(echo "$out" | grep -c '허브 생성·기동 실패')" = 1 ] && echo "$out" | grep -q 'Traceback (most recent call last)' && ok "원래 traceback 보존, 안내 한 번" || ko "traceback·안내" "$out"

echo '{}' > "$S"
printf '#!/bin/bash\necho "du: test failure" >&2\nexit 19\n' > "$SB/bin/du"
chmod +x "$SB/bin/du"
out=$(COMMON_FILE="$SB/common.txt" run --slug ac --force); rc=$?
[ "$rc" = 0 ] && echo "$out" | grep -q 'du: test failure' && ! echo "$out" | grep -q '허브 생성·기동 실패' && ok "du 실패: 정상 종료, 원래 stderr만 보존" || ko "du 실패 오탐" "rc=$rc $out"

echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
