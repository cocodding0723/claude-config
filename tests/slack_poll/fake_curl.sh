#!/bin/bash
# slack_poll.sh 시험용 가짜 curl. 실제 네트워크 접속 없음.
# 기록 폴더는 이 파일과 같은 폴더의 curl.logdir 에 적힌 경로(slack_poll.sh 가 curl 환경을 허용 목록으로 걸러 환경 변수로는 못 받음).
# 인자는 curl.args, 표준입력 설정은 curl.stdin.<번호>, 환경 점검은 curl.env.<번호> 에 남김.
#   curl.env.<번호> 1줄: 토큰 값이 든 환경 변수 수 / SLACK_BOT_TOKEN 이름 수 / 허용 목록 밖 시험용 비밀 이름 수
# 주소에 /fail 이 있으면 실패(22), /login 이 있으면 로그인 HTML(200 text/html), 그 외는 본문 저장 후 200.
set -u
LOG=$(cat "$(dirname "$0")/curl.logdir")
printf '%s\n' "$*" >> "$LOG/curl.args"
n=$(($(wc -l < "$LOG/curl.args")))
out="" prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
conf=$(cat)
printf '%s\n' "$conf" > "$LOG/curl.stdin.$n"
tok=$(printf '%s\n' "$conf" | sed -n 's/^header = "Authorization: Bearer \(.*\)"$/\1/p')
by_value=0
[ -n "$tok" ] && by_value=$(env | grep -cF -- "$tok")
printf '%s %s %s\n' "$by_value" "$(env | grep -c '^SLACK_BOT_TOKEN=')" "$(env | grep -c '^APP_SECRET_FAKE=')" > "$LOG/curl.env.$n"
env | cut -d= -f1 | sort > "$LOG/curl.envnames.$n"
url=$(printf '%s\n' "$conf" | sed -n 's/^url = "\(.*\)"$/\1/p')
case "$url" in
  */fail*) exit 22 ;;
  */login*) printf '<!DOCTYPE html><html><body>sign in</body></html>' > "$out"; printf '200\ttext/html; charset=utf-8'; exit 0 ;;
esac
printf 'DATA %s\n' "$url" > "$out"
printf '200\tapplication/octet-stream'
