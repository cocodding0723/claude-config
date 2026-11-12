#!/bin/bash
# 메인 세션 터미널 핸들을 main.env 의 MAIN_TERMINAL 줄에 기록(slack_push 전달 대상). 다른 줄은 그대로 두고 값은 출력하지 않음.
set -eu
usage() {
  cat <<'EOF'
사용: set_main_terminal.sh <핸들>
      set_main_terminal.sh --auto [--dry-run]
  main.env 의 MAIN_TERMINAL=<핸들> 줄을 추가하거나 교체한다. 다른 줄은 순서대로 보존하고, 파일 내용·핸들 값은 출력하지 않는다.
  main.env 옆 잠금 파일(.main-env.lock) 아래에서 임시 파일에 쓴 뒤 원자적으로 교체하며(자동 재탐색과 같은 잠금) 권한은 600 으로 맞춘다. main.env 가 심볼릭 링크면 링크는 그대로 두고 최종 대상 파일을 교체한다.
  줄 끝: 교체하는 MAIN_TERMINAL 줄만 LF 로 쓰고 다른 줄은 원형(CRLF 포함)대로 보존한다. CRLF 파일이면 줄 끝이 섞일 수 있으며 slack_push 는 값을 strip 해 읽으므로 동작에는 영향 없다.
--auto  핸들을 직접 찾아 기록함(slack_push 의 자동 재탐색과 같은 기준). orca terminal list 에서 제목이 MAIN-HANDOFF 로 시작하고(제목 중간에 낀 터미널은 제외. 운영 규약: 이 접두부는 메인 전용이며 워커 제목은 이 접두부로 시작하지 않는다), 메인 체크아웃(LRM_PUSH_MAIN,
        기본 ~/orca/LoveRevolutionMaker) 소속이며, agentIdentity=claude 이고, 연결됨·쓰기 가능한 터미널이 정확히 1개일 때만 기록한다.
        0개·2개 이상이면 아무것도 쓰지 않고 후보 수와 조치를 안내한 뒤 종료 코드 3. --dry-run 은 찾기만 하고 main.env 를 쓰지 않는다(핸들은 앞 8자만 출력).
핸들: orca terminal 핸들(term_ 뒤 16진·하이픈). 메인 세션에서 orca terminal list --json 으로 찾는다.
언제: 메인 코디네이터를 (재)시작한 직후 새 터미널 핸들로 한 번. 안 하면 slack_push 가 전달을 보류하고 '메인 연결 끊김'을 알린다.
환경 변수: LRM_MAIN_ENV 설정 파일 경로(기본 ~/.config/lrm-hubs/main.env) · LRM_PUSH_MAIN 메인 체크아웃 경로 · LRM_PUSH_ORCA orca 실행 파일(기본 orca)
예: set_main_terminal.sh term_0123abcd-…
    set_main_terminal.sh --auto --dry-run   # 지금 메인으로 보이는 터미널이 하나인지 확인만
종료 코드: 0 기록함(--dry-run 은 1개 발견), 2 인자·형식 오류 또는 쓰기·조회 실패, 3 --auto 후보가 0개이거나 2개 이상
EOF
}
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "") echo "핸들 필요 — 사용: set_main_terminal.sh <핸들> (--help 참고)" >&2; exit 2 ;;
esac
# 공용 모듈은 링크로 설치돼도 원본 폴더에서 찾음
SELF=$(python3 -I -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$0")
if [ "$1" = "--auto" ]; then
  shift
  case "${1:-}" in "") ;; --dry-run) [ $# -eq 1 ] || { echo "--dry-run 뒤 인자 없음 — --help 참고" >&2; exit 2; } ;; *) echo "알 수 없는 옵션: $1 — --help 참고" >&2; exit 2 ;; esac
  exec python3 -I "$(dirname "$SELF")/main_terminal.py" "$@"  # 재탐색은 공용 모듈이 맡음
fi
[ $# -eq 1 ] || { echo "인자는 핸들 하나만 — --help 참고" >&2; exit 2; }
[[ $1 =~ ^term_[0-9a-f-]+$ ]] || { echo "핸들 형식 오류: term_ 뒤에 16진·하이픈만 허용 — orca terminal list 의 handle 값을 그대로 넣음" >&2; exit 2; }
# 기록은 공용 모듈이 맡음: 자동 재탐색과 같은 잠금 아래에서 교체하므로 오래된 탐색 결과가 이 값을 덮지 못함
exec python3 -I "$(dirname "$SELF")/main_terminal.py" --set "$1"
