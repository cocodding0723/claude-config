#!/bin/bash
# 봇으로 사용자 DM 에 글·파일 게시. 토큰은 slack-bot.env(SLACK_BOT_TOKEN, SLACK_USER_ID)에서만 읽음, 출력 금지.
# 보낸 메시지 ts·channel 은 slack-sent.jsonl 에 기록(slack_poll.sh 가 스레드 답글·반응 수집에 사용).
set -eu
usage() {
  cat <<'EOF'
사용: slack_notify.sh [--tag 허브] [--title 제목] [--thread TS] "메시지" [파일경로]
  --tag     관련 허브 태그(기록용)
  --title   기록용 제목(기본: 메시지 첫 줄)
  --thread  해당 ts 메시지의 스레드 답글로 게시
예:
  slack_notify.sh "빌드 완료"
  slack_notify.sh --tag ui-hub "결정1 A/B?" report.html
  slack_notify.sh --thread 1730790000.123456 "반영함"
종료 코드: 0 성공, 1 API 실패, 2 설정·인자 오류
EOF
}
TAG="" TITLE="" THREAD=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --tag) TAG="${2:?--tag 값 필요}"; shift 2 ;;
    --title) TITLE="${2:?--title 값 필요}"; shift 2 ;;
    --thread) THREAD="${2:?--thread 값 필요}"; shift 2 ;;
    --) shift; break ;;
    -*) echo "알 수 없는 옵션: $1 — --help 참고" >&2; exit 2 ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || { usage >&2; exit 2; }
DIR="$HOME/.config/lrm-hubs"
ENV="$DIR/slack-bot.env"
SENT="$DIR/slack-sent.jsonl"
[ -f "$ENV" ] || { echo "slack-bot.env 없음 — 사용자 설정 필요" >&2; exit 2; }
. "$ENV"
: "${SLACK_BOT_TOKEN:?}" "${SLACK_USER_ID:?}"
API=https://slack.com/api
MSG="$1"
# 기본 이모지 → 워크스페이스 강비서 이모지
MSG="$(printf '%s' "$MSG" | sed -e 's/🅰️/:비서-a:/g' -e 's/🅱️/:비서-b:/g' -e 's/✅/:비서-추천대로:/g' -e 's/❌/:비서-보류:/g' -e 's/🙋/:비서-선택해주세요:/g' -e 's/📄/:비서-공유합니다:/g' -e 's/🔄/:비서-진행중:/g' -e 's/⚠️/:비서-긴급:/g')"
[ -n "$TITLE" ] || TITLE="${MSG%%$'\n'*}"
CH=$(curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" -d "users=$SLACK_USER_ID" $API/conversations.open | python3 -c 'import sys,json;print(json.load(sys.stdin)["channel"]["id"])')

# 응답 JSON 을 받아 ok/에러 출력 + 성공 시 sent 기록. ts 는 postMessage 만 제공, 파일 업로드는 file_id 로 남김.
record() {
  python3 -c '
import sys,json,time,os
d=json.load(sys.stdin); sent,ch,title,tag,thread,kind=sys.argv[1:7]
if not d.get("ok"):
    print(d.get("error")); sys.exit(1)
rec={"at":int(time.time()),"channel":ch,"ts":d.get("ts",""),"thread_ts":thread,"kind":kind,"title":title[:200],"tag":tag}
if kind=="file":
    rec["file_id"]=(d.get("files") or [{}])[0].get("id","")
    rec["path"]=os.environ.get("LRM_FILE_PATH","")
with open(sent,"a",encoding="utf-8") as f: f.write(json.dumps(rec,ensure_ascii=False)+"\n")
print("ok", rec["ts"])
' "$SENT" "$CH" "$TITLE" "$TAG" "$THREAD" "$1"
}

if [ $# -ge 2 ]; then
  F="$2"; export LRM_FILE_PATH="$F"; N=$(basename "$F"); L=$(stat -f %z "$F")
  R=$(curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" --data-urlencode "filename=$N" -d "length=$L" $API/files.getUploadURLExternal)
  U=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["upload_url"])'); ID=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["file_id"])')
  curl -s -o /dev/null -F "file=@$F" "$U"
  curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" -H 'Content-Type: application/json' \
    -d "$(python3 -c '
import json,sys
b={"files":[{"id":sys.argv[1],"title":sys.argv[2]}],"channel_id":sys.argv[3],"initial_comment":sys.argv[4]}
if sys.argv[5]: b["thread_ts"]=sys.argv[5]
print(json.dumps(b))' "$ID" "$N" "$CH" "$MSG" "$THREAD")" \
    $API/files.completeUploadExternal | record file
else
  curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" -H 'Content-Type: application/json' \
    -d "$(python3 -c '
import json,sys
b={"channel":sys.argv[1],"text":sys.argv[2]}
if sys.argv[3]: b["thread_ts"]=sys.argv[3]
print(json.dumps(b))' "$CH" "$MSG" "$THREAD")" \
    $API/chat.postMessage | record text
fi
