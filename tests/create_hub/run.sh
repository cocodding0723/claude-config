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
case "$1 $2" in
  "terminal list") echo '{"result":{"terminals":[]}}' ;;
  "terminal read") echo '{"result":{"terminal":{"tail":["esc to interrupt"]}}}' ;;
  "agent launch") echo '{"handle":"term_test"}' ;;
  *) echo '{}' ;;
esac
ORCA
chmod +x "$SB/bin/orca"
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

# 2) 저장소 사본으로 실행해도 설치본(~/.claude/…/common-rules.txt)을 읽음
mkdir -p "$SB/home/.claude/skills/orca-hub-create/references"
cp "$SB/common.txt" "$SB/home/.claude/skills/orca-hub-create/references/common-rules.txt"
out=$(run --slug inst); rc=$?
[ "$rc" = 0 ] && [ -f "$SB/ws/coord-inst-hub/docs/a.md" ] && ok "설치본 공통 규칙으로 허브 생성" || ko "설치본 공통 규칙" "rc=$rc $out"
[ ! -e "$SB/ws/coord-inst-hub/Assets" ] && ok "기본 경로는 문서·설정만 받음(코드 없음)" || ko "기본 sparse" "Assets 가 받아짐"

# 3) COMMON_FILE 지정이 먼저. --paths "/*" 는 글자 그대로 들어감(컴퓨터 최상위 폴더 목록으로 풀리지 않음)
out=$(COMMON_FILE="$SB/common.txt" run --slug all --paths "/*"); rc=$?
pat=$(git -C "$SB/ws/coord-all-hub" sparse-checkout list 2>/dev/null | tr '\n' ' ')
[ "$rc" = 0 ] && [ "$pat" = "/* " ] && ok "--paths \"/*\" 가 그대로 들어감" || ko "경로 값 풀림" "rc=$rc 패턴='$pat'"
[ -f "$SB/ws/coord-all-hub/Assets/a.cs" ] && [ -f "$SB/ws/coord-all-hub/README.md" ] && ok "\"/*\" 로 전체 파일을 받음" || ko "전체 받기" "$(ls "$SB/ws/coord-all-hub" 2>/dev/null | tr '\n' ' ')"

echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
