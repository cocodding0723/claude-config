#!/bin/bash
# slack_notify.sh 시험용 가짜 curl. 실제 네트워크 접속 없음. 주소가 127.0.0.1 이 아니면 실패(7)해 실제 Slack 으로 못 나가게 막음.
# 기록 폴더는 환경 변수 FAKE_CURL_LOG. 인증 헤더 값은 기록하지 않음(토큰 비노출 시험).
#   calls.log   호출마다 주소 한 줄 + 인자(Authorization 값 가림)
#   post.json   마지막 chat.postMessage / files.completeUploadExternal 본문(-d JSON)
#   posts.log   위 두 호출의 본문 전체(한 줄씩, 순서 보존)
#   fail.api    내용이 있으면 chat.postMessage 가 {"ok":false,"error":"<파일 내용>"} 로 응답
set -u
LOG="${FAKE_CURL_LOG:?}"
url="" body="" prev=""
for a in "$@"; do
  case "$prev" in -d) body="$a" ;; esac
  prev="$a"; url="$a"
done
printf '%s\n' "$url" >> "$LOG/calls.log"
args=""
for a in "$@"; do
  case "$a" in Authorization:*) a="Authorization: <가림>" ;; esac
  args="$args [$a]"
done
printf '  args:%s\n' "$args" >> "$LOG/calls.log"
case "$url" in
  http://127.0.0.1:*) ;;
  *) exit 7 ;;
esac
method="${url##*/}"
case "$method" in
  conversations.open) printf '{"ok":true,"channel":{"id":"Dfake"}}' ;;
  chat.postMessage)
    if [ -s "$LOG/fail.api" ]; then printf '{"ok":false,"error":"%s"}' "$(cat "$LOG/fail.api")"; exit 0; fi
    printf '%s' "$body" > "$LOG/post.json"; printf '%s\n' "$body" >> "$LOG/posts.log"
    printf '{"ok":true,"ts":"1700000000.000100"}' ;;
  files.getUploadURLExternal) printf '{"ok":true,"upload_url":"http://127.0.0.1:9/upload","file_id":"Ffake1"}' ;;
  upload) ;;
  files.completeUploadExternal)
    printf '%s' "$body" > "$LOG/post.json"; printf '%s\n' "$body" >> "$LOG/posts.log"
    printf '{"ok":true,"files":[{"id":"Ffake1"}]}' ;;
  *) printf '{"ok":false,"error":"no_fixture"}' ;;
esac
