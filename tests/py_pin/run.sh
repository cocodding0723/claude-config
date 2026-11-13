#!/usr/bin/env bash
# launchd·훅이 짧은 PATH 에서 시스템 python3(3.9)로 돌아 import 가 깨지는 사고(결정 답 동기화 중단)의 회귀 시험.
# ① 헬퍼 lib/py.sh 가 3.9 를 건너뛰고 3.10 이상을 고르는지 ② 셸 스크립트에 맨 python3 호출이 없는지
# ③ plist·셸이 부르는 .py 가 고른 인터프리터로 import 되는지 ④ 3.9 로 잘못 떠도 진입점이 원인·조치를 내는지
# 임시 폴더 안에서만 동작. PYPIN_SCRIPTS=<폴더> 로 변이 검증 가능.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
SCRIPTS=${PYPIN_SCRIPTS:-$REPO/hubs/scripts}
PLISTS=$REPO/hubs/launchd
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-pypin.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT

# 3.10 이상 실제 python3 와 가짜 3.9
REAL=""
for c in "${CLAUDE_CONFIG_PYTHON3:-}" "$(command -v python3 || true)" /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  [ -n "$c" ] && [ -x "$c" ] && "$c" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null && { REAL=$c; break; }
done
[ -n "$REAL" ] || { echo "시험 불가: 3.10 이상 python3 없음"; exit 2; }
OLD="$SB/old39"; mkdir -p "$OLD"
printf '#!/bin/sh\n# 가짜 3.9: 버전 검사(-c)는 실패, 그 밖은 3.9 TypeError 흉내\nexit 1\n' > "$OLD/python3"; chmod +x "$OLD/python3"
NONE="$SB/empty"; mkdir -p "$NONE"
LIB="$SCRIPTS/lib/py.sh"

pick() {  # pick <PATH> [환경 KEY=VAL…] → stdout "PY=<경로>" 또는 오류, rc 보존
  local p="$1"; shift
  env -i HOME="$SB" PATH="$p" "$@" /bin/bash -c '. "$1"; echo "PY=$PY"' _ "$LIB" 2>&1
}

# ── ① 헬퍼 ──
[ -f "$LIB" ] && ok "헬퍼 hubs/scripts/lib/py.sh 존재" || ko "헬퍼 없음" "$LIB"
out=$(pick "$OLD:$(dirname "$REAL")"); [ "$out" = "PY=$REAL" ] && ok "PATH 앞쪽이 3.9 여도 뒤의 3.10 이상을 고름" || ko "3.9 건너뛰기" "$out"
out=$(pick "$OLD" LRM_PY_KNOWN="$REAL"); [ "$out" = "PY=$REAL" ] && ok "PATH 에 3.9 뿐이면 알려진 위치의 3.10 이상을 고름" || ko "알려진 위치" "$out"
out=$(pick "$OLD:$(dirname "$REAL")" LRM_PYTHON3="$REAL"); [ "$out" = "PY=$REAL" ] && ok "LRM_PYTHON3(plist 주입)가 우선" || ko "LRM_PYTHON3" "$out"
out=$(pick "$OLD" LRM_PY_KNOWN=""); rc=$?
[ "$rc" = 3 ] && echo "$out" | grep -q '3.10 이상 없음' && echo "$out" | grep -q 'LRM_PYTHON3=' && ok "3.10 이상이 없으면 원인·조치와 종료 코드 3" || ko "없음 처리" "rc=$rc $out"
out=$(pick "$(dirname "$REAL")" LRM_PYTHON3="$OLD/python3"); rc=$?
[ "$rc" = 3 ] && echo "$out" | grep -q '3.10 이상 python3 가 아님' && ok "LRM_PYTHON3 가 3.9 면 조용히 우회하지 않고 실패" || ko "잘못된 LRM_PYTHON3" "rc=$rc $out"
out=$(pick "relative/bin:$OLD:$(dirname "$REAL")"); [ "$out" = "PY=$REAL" ] && ok "PATH 의 상대 경로 항목은 무시" || ko "상대 PATH" "$out"
if [ -x /usr/bin/python3 ]; then
  out=$(env -i HOME="$SB" PATH=/usr/bin:/bin /bin/bash -c '. "$1"; "$PY" -I -c "import sys; print(sys.version_info >= (3, 10))"' _ "$LIB" 2>&1)
  [ "$out" = True ] && ok "launchd 기본 PATH(/usr/bin:/bin)에서도 3.10 이상 선택" || ko "launchd PATH" "$out"
fi

# ── ② 맨 python3 호출 금지(주석·안내 문구 제외, 명령 위치만) ──
bare=$("$REAL" -I - "$SCRIPTS" <<'PY'
import glob, os, re, sys
cmd = re.compile(r'(^\s*|[|;({]\s*|&&\s*|\bthen\s+|\bexec\s+|\bcaffeinate -i\s+|\$\(\s*)python3\b')
for f in sorted(glob.glob(os.path.join(sys.argv[1], "*.sh"))):
    for n, line in enumerate(open(f, encoding="utf-8"), 1):
        if line.lstrip().startswith("#"):
            continue
        if cmd.search(line):
            print(f"{os.path.basename(f)}:{n}: {line.strip()[:80]}")
PY
)
[ -z "$bare" ] && ok "hubs/scripts/*.sh 에 맨 python3 호출 없음" || ko "맨 python3 호출 남음" "$(echo "$bare" | head -3 | tr '\n' ' ')"
# "$PY" 를 쓰는 스크립트는 모두 헬퍼를 불러오고, 불러오는 줄이 첫 사용보다 앞
miss=""
for f in "$SCRIPTS"/*.sh; do
  grep -q '"\$PY"\|\$PY ' "$f" || continue
  s=$(grep -n 'lib/py\.sh' "$f" | head -1 | cut -d: -f1); u=$(grep -n '"\$PY"' "$f" | head -1 | cut -d: -f1)
  { [ -n "$s" ] && { [ -z "$u" ] || [ "$s" -lt "$u" ]; }; } || miss="$miss $(basename "$f")"
done
[ -z "$miss" ] && ok "\$PY 쓰는 스크립트는 모두 lib/py.sh 를 먼저 불러옴" || ko "헬퍼 미적용" "$miss"
grep -q $'^hubs/scripts/lib/py.sh\t.config/lrm-hubs/lib/py.sh\tlink' "$REPO/links.tsv" && ok "links.tsv 에 헬퍼 설치 경로 등록" || ko "links.tsv" "lib/py.sh 줄 없음"

# ── plist: 하드코딩 python 금지, 셸 진입점 plist 는 LRM_PYTHON3 주입 ──
bad=""
for p in "$PLISTS"/*.plist; do
  b=$(basename "$p")
  grep -qE '/usr/bin/python3|<string>python3</string>' "$p" && bad="$bad $b(하드코딩)"
  if grep -q '<string>/bin/bash</string>' "$p" && grep -qE '<string>__HOME__/\.config/lrm-hubs/[a-z_]+\.sh</string>' "$p"; then
    sh=$(grep -oE 'lrm-hubs/[a-z_]+\.sh' "$p" | head -1 | cut -d/ -f2)
    if grep -q 'lib/py\.sh' "$SCRIPTS/$sh" 2>/dev/null; then
      grep -q '<key>LRM_PYTHON3</key><string>__PYTHON3__</string>' "$p" || bad="$bad $b(LRM_PYTHON3 없음)"
    fi
  fi
done
[ -z "$bad" ] && ok "plist: python 하드코딩 없고 셸 진입점은 LRM_PYTHON3=__PYTHON3__ 주입" || ko "plist 점검" "$bad"

# ── ③ launchd 진입점 .py 가 고른 인터프리터로 import 됨 ──
mods=$("$REAL" -I - "$SCRIPTS" "$PLISTS" <<'PY'
import glob, os, re, sys
scripts, plists = sys.argv[1:3]
names = set()
for f in glob.glob(os.path.join(plists, "*.plist")) + glob.glob(os.path.join(scripts, "*.sh")):
    names.update(re.findall(r'([A-Za-z0-9_]+)\.py\b', open(f, encoding="utf-8").read()))
print(" ".join(sorted(n for n in names if os.path.isfile(os.path.join(scripts, n + ".py")))))
PY
)
[ -n "$mods" ] && ok "진입점 후보 .py 수집: $(echo "$mods" | wc -w | tr -d ' ')개" || ko "진입점 수집" "비어 있음"
fail=""
for m in $mods; do
  out=$(cd "$SB" && PYTHONDONTWRITEBYTECODE=1 "$REAL" -I -c "
import sys
sys.path.insert(0, '$SCRIPTS')
try:
    import $m
except ModuleNotFoundError as e:
    if e.name and os.path.exists('$SCRIPTS/' + e.name + '.py'): raise
    print('SKIP ' + str(e.name)); sys.exit(0)
" 2>&1) || fail="$fail $m($(echo "$out" | tail -1 | cut -c1-60))"
done
[ -z "$fail" ] && ok "진입점 .py 전부 import 성공" || ko "import 실패" "$fail"

# ── ④ 3.9 로 잘못 떠도 조용히 실패하지 않음: 진입점이 원인·조치를 냄 ──
if [ -x /usr/bin/python3 ] && /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info < (3, 10) else 1)' 2>/dev/null; then
  for m in panel_collect progress_collect progress_digest session_gc; do
    out=$(cd "$SB" && /usr/bin/python3 -I "$SCRIPTS/$m.py" --help 2>&1); rc=$?
    [ "$rc" = 2 ] && echo "$out" | grep -q 'Python 3.10 이상 필요' && ok "$m.py: 3.9 로 실행하면 '3.10 이상 필요' 안내·종료 코드 2" || ko "$m.py 3.9 안내" "rc=$rc $(echo "$out" | tail -1 | cut -c1-80)"
  done
else
  echo "SKIP  시스템 3.9 없음: ④ 건너뜀"
fi

printf '\n결과: 통과 %d, 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
