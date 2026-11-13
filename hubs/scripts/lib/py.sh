# 셸 스크립트 공용 python 선택기. `. "$HERE/lib/py.sh"` 로 불러오면 PY(3.10 이상 python3 절대 경로)를 설정함.
# 이유: launchd·훅의 짧은 PATH 는 /usr/bin/python3(3.9)를 잡고, 3.10 문법(X | None) 모듈이 TypeError 로 조용히 실패함.
# 순서: LRM_PYTHON3(plist 가 install 검증 경로를 주입) → PATH 의 python3 중 3.10 이상 → 알려진 설치 위치.
# 못 찾으면 원인·조치를 stderr 에 내고 종료 코드 3(불러온 스크립트가 종료됨).
# bash 3.2·set -eu 호환. 사용하는 스크립트는 맨 python3 대신 "$PY" 를 씀(tests/py_pin 이 검사).

lrm_py_ok() {  # 인자: 후보 경로. 실행 가능한 3.10 이상이면 0
  [ -n "${1:-}" ] && [ -f "$1" ] && [ -x "$1" ] \
    && "$1" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

lrm_py_find() {  # 3.10 이상 python3 절대 경로를 stdout 으로. 없으면 1
  local c d
  if [ -n "${LRM_PYTHON3:-}" ]; then  # 명시 지정은 조용히 우회하지 않음
    lrm_py_ok "$LRM_PYTHON3" && { printf '%s\n' "$LRM_PYTHON3"; return 0; }
    echo "LRM_PYTHON3=$LRM_PYTHON3 은 3.10 이상 python3 가 아님 — 절대 경로로 고치거나 변수를 지우고 install.sh 를 다시 실행" >&2
    return 2
  fi
  local IFS=:
  for d in $PATH; do
    [ -n "$d" ] || continue
    case "$d" in /*) ;; *) continue ;; esac
    c="$d/python3"
    lrm_py_ok "$c" && { printf '%s\n' "$c"; return 0; }
  done
  IFS=$' \t\n'
  # 프레임워크 설치는 버전 폴더 내림차순(최신 우선). python.org·Homebrew·수동 설치 위치
  # LRM_PY_KNOWN(공백 구분)은 시험이 위치 목록을 바꾸는 용도. 설정하면(빈 값 포함) 기본 목록을 대체함
  for c in ${LRM_PY_KNOWN-$(ls -d /Library/Frameworks/Python.framework/Versions/*/bin/python3 2>/dev/null | sort -r) /opt/homebrew/bin/python3 /usr/local/bin/python3}; do
    lrm_py_ok "$c" && { printf '%s\n' "$c"; return 0; }
  done
  return 1
}

PY="$(lrm_py_find)"; _lrm_py_rc=$?
if [ "$_lrm_py_rc" != 0 ]; then
  [ "$_lrm_py_rc" = 2 ] || echo "python 3.10 이상 없음(PATH=$PATH). launchd·훅이 시스템 python3(3.9)를 잡으면 모듈 import 가 실패함 — brew install python 뒤 install.sh 를 다시 실행하거나 LRM_PYTHON3=<3.10 이상 절대 경로> 지정" >&2
  exit 3
fi
unset _lrm_py_rc
export PY
