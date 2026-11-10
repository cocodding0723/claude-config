#!/bin/bash
# 메인 세션 터미널 핸들을 main.env 의 MAIN_TERMINAL 줄에 기록(slack_push 전달 대상). 다른 줄은 그대로 두고 값은 출력하지 않음.
set -eu
usage() {
  cat <<'EOF'
사용: set_main_terminal.sh <핸들>
  main.env 의 MAIN_TERMINAL=<핸들> 줄을 추가하거나 교체한다. 다른 줄은 순서대로 보존하고, 파일 내용·핸들 값은 출력하지 않는다.
  임시 파일에 쓴 뒤 원자적으로 교체하며 권한은 600 으로 맞춘다. main.env 가 심볼릭 링크면 링크는 그대로 두고 최종 대상 파일을 교체한다.
  줄 끝: 교체하는 MAIN_TERMINAL 줄만 LF 로 쓰고 다른 줄은 원형(CRLF 포함)대로 보존한다. CRLF 파일이면 줄 끝이 섞일 수 있으며 slack_push 는 값을 strip 해 읽으므로 동작에는 영향 없다.
핸들: orca terminal 핸들(term_ 뒤 16진·하이픈). 메인 세션에서 orca terminal list --json 으로 찾는다.
언제: 메인 코디네이터를 (재)시작한 직후 새 터미널 핸들로 한 번. 안 하면 slack_push 가 전달을 보류하고 '메인 연결 끊김'을 알린다.
환경 변수: LRM_MAIN_ENV 설정 파일 경로(기본 ~/.config/lrm-hubs/main.env)
예: set_main_terminal.sh term_0123abcd-…
종료 코드: 0 기록함, 2 인자·형식 오류 또는 쓰기 실패
EOF
}
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "") echo "핸들 필요 — 사용: set_main_terminal.sh <핸들> (--help 참고)" >&2; exit 2 ;;
esac
[ $# -eq 1 ] || { echo "인자는 핸들 하나만 — --help 참고" >&2; exit 2; }
[[ $1 =~ ^term_[0-9a-f-]+$ ]] || { echo "핸들 형식 오류: term_ 뒤에 16진·하이픈만 허용 — orca terminal list 의 handle 값을 그대로 넣음" >&2; exit 2; }
F=${LRM_MAIN_ENV:-$HOME/.config/lrm-hubs/main.env}
# 링크면 최종 대상에 임시 파일을 만들어 대상을 교체함(mv 가 링크 자체를 일반 파일로 바꾸는 것을 막음)
[ ! -L "$F" ] || F=$(python3 -I -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$F")
D=$(dirname "$F")
mkdir -p "$D"
umask 077
T=$(mktemp "$D/.main.env.XXXXXX") || { echo "임시 파일 생성 실패: $D — 폴더 권한 확인" >&2; exit 2; }
trap 'rm -f "$T"' EXIT
if [ -e "$F" ]; then
  # grep -v 는 남길 줄이 없으면 1 을 돌려줌. 2 이상(읽기 실패)만 오류
  rc=0; grep -v '^MAIN_TERMINAL=' "$F" > "$T" || rc=$?
  [ "$rc" -le 1 ] || { echo "main.env 읽기 실패 — 권한 확인" >&2; exit 2; }
fi
printf 'MAIN_TERMINAL=%s\n' "$1" >> "$T"
chmod 600 "$T"
mv -f "$T" "$F"
echo "MAIN_TERMINAL 기록함 — ${F##*/} (값은 출력하지 않음)"
