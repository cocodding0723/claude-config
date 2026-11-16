#!/bin/bash
# 렌더 스크립트 공용 함수. source 로 읽음.

# 서브모듈이 빈 sparse 작업 공간은 컴파일 에러(CS0246)로 렌더가 죽으므로 시작 전 초기화
render_prepare_ws() {
  local ws="$1"
  if [ -n "$(git -C "$ws" submodule status --recursive 2>/dev/null | grep '^-')" ]; then
    echo "서브모듈 초기화: $ws" >&2
    if ! git -C "$ws" submodule update --init --recursive >&2; then
      echo "실패: 서브모듈 초기화 불가. 조치: git -C $ws submodule update --init --recursive 를 직접 실행해 원인 확인" >&2
      return 3
    fi
  fi
}

# 작업 공간별 로그. 공용 /tmp 로그를 여러 렌더가 덮어쓰던 문제 방지
render_log_path() {
  mkdir -p "$1/TestResults"
  echo "$1/TestResults/$2.log"
}

# PNG 가 하나도 없으면 로그의 에러 줄 요약 후 종료 코드 4
render_check_output() {
  local dir="$1" tag="$2" log="$3"
  if ls "$dir"/"${tag}"_*.png >/dev/null 2>&1; then
    ls "$dir"/"${tag}"_*.png
    return 0
  fi
  echo "실패: PNG 없음 ($dir/${tag}_*). 로그: $log" >&2
  grep -E "error CS|Exception|Error|없음" "$log" 2>/dev/null | tail -5 >&2 || tail -3 "$log" >&2
  return 4
}
