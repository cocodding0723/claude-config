#!/usr/bin/env bash
# pick_host.sh 시험: Unity 개수는 실행 파일 기준, 동시 2개까지 로컬. ps·orca 등은 가짜로 바꿔 실제 환경에 손대지 않음
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
PH="$HERE/../../skills/orca-hub-create/scripts/pick_host.sh.tmpl"
SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-pick-host.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
PASS=0 FAIL=0
mkdir "$SB/bin"
cat > "$SB/bin/ps" <<'S'
#!/bin/sh
cat "$FAKE_PS"
S
cat > "$SB/bin/sysctl" <<'S'
#!/bin/sh
case "$2" in hw.ncpu) echo 18;; *) echo "{ 40.00 40.00 40.00 }";; esac
S
cat > "$SB/bin/memory_pressure" <<'S'
#!/bin/sh
echo "System-wide memory free percentage: ${FAKE_FREE}%"
S
cat > "$SB/bin/orca" <<'S'
#!/bin/sh
echo '{"reachable": true}'
S
chmod +x "$SB"/bin/*
U=/Applications/Unity/Hub/Editor/6000.3.16f1/Unity.app/Contents/MacOS/Unity
check() { # 이름 기대호스트 기대근거 여유 인자
  got=$(PATH="$SB/bin:$PATH" FAKE_PS="$SB/ps.txt" FAKE_FREE="$4" bash "$PH" ${5:-} 2>"$SB/err")
  if [ "$got" = "$2" ] && grep -q "$3" "$SB/err"; then PASS=$((PASS+1)); echo "PASS  $1"; else FAIL=$((FAIL+1)); echo "FAIL  $1 — $got $(cat "$SB/err")"; fi
}
unset LRM_UNITY_SLOTS
printf '%s\n' "$U -batchmode" "/bin/bash unity-run --name a -- $U -batchmode" "/bin/zsh -c $U" "$U -name AssetImportWorker0" > "$SB/ps.txt"
check "래퍼·임포트 보조 제외하고 1개, 로컬" local 'unity=1 ' 50 unity
printf '%s\n' "$U -batchmode" "$U -projectPath x" > "$SB/ps.txt"
check "Unity 2개면 Unity 작업은 원격" remote 'unity=2 ' 50 unity
check "Unity 2개여도 일반 작업은 로컬" local 'unity=2 ' 50
LRM_UNITY_SLOTS=3 check "슬롯 설정을 따름" local 'unity=2 ' 50 unity
: > "$SB/ps.txt"
check "여유 20% 면 워커 25개" local '구현워커상한=25' 20
check "여유 19% 면 워커 12개·원격" remote '구현워커상한=12' 19
check "여유 9% 면 워커 0개" remote '구현워커상한=0' 9
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
