#!/bin/bash
# 봇 DM 명령 중 토큰 없이 바로 답할 수 있는 것을 처리. slack_poll.sh 가 매 주기 부름.
# 대기 조회 실패(exit 1)는 "결정 없음"이 아니라 "확인 불가"로 응답. 이력 기록 실패·429 로 실패 경로가 늘어서임.
# 지원: 대기|미승인 → 미답 결정 링크, PR → 열린 초안 PR 링크. 처리한 줄 수는 .cmd-read 에 기록.
set -u
H="${LRM_HUB_DIR:-$HOME/.config/lrm-hubs}"; F="$H/slack-inbox.jsonl"; S="$H/.cmd-read"
[ -f "$F" ] || exit 0
n=$(cat "$S" 2>/dev/null || wc -l < "$F"); m=$(wc -l < "$F"); [ "$m" -le "$n" ] && { echo "$m" > "$S"; exit 0; }
tail -n $((m-n)) "$F" | python3 -c 'import sys,json
for l in sys.stdin:
  try: d=json.loads(l)
  except Exception: continue
  if d.get("type") in ("command","note") and not d.get("parent_ts"): print((d.get("text") or d.get("command") or "").strip())' | while read -r t; do
  case "$t" in
    대기|미승인|"승인 대기") O=$("$H/slack_pending.sh" 2>>"$H/slack-poll.log"); R=$?; echo "$O" >>"$H/slack-poll.log"
      if [ "$R" -ne 0 ]; then "$H/slack_notify.sh" "⚠️ 대기 목록 확인 불가 — slack-poll.log 확인" >/dev/null
      elif [ "$O" = "대기 0" ]; then "$H/slack_notify.sh" "✅ 답을 기다리는 결정 없음" >/dev/null; fi ;;
    PR|pr|"초안 PR") L=$(cd "$HOME/orca/LoveRevolutionMaker" && gh pr list --state open --author @me --json number,title,url,isDraft --jq '.[] | "• <\(.url)|#\(.number)> \(.title)\(if .isDraft then " (초안)" else "" end)"' 2>/dev/null); "$H/slack_notify.sh" "📋 *열린 내 PR*
${L:-없음}" >/dev/null ;;
    할일|"할 일") "$H/slack_notify.sh" "$(cat "$H/todo.md")" >/dev/null ;;
    CI|ci) L=$(cd "$HOME/orca/LoveRevolutionMaker" && gh run list --limit 30 --json databaseId,status,workflowName,headBranch,url --jq '.[] | select(.status=="in_progress" or .status=="queued") | "• <\(.url)|\(.workflowName)> \(.headBranch) (\(.status))"' 2>/dev/null); "$H/slack_notify.sh" "⚙️ *돌고 있는 CI*
${L:-없음}" >/dev/null ;;
    문서\ *) k="${t#문서 }"; P=$(python3 -c 'import sys,json
k=sys.argv[1]; r=[json.loads(l) for l in open(sys.argv[2]) if l.strip()]
r=[x for x in r if x.get("path") and k in (x.get("title","")+x.get("path",""))]
print(r[-1]["path"] if r else "")' "$k" "$H/slack-sent.jsonl"); if [ -n "$P" ] && [ -f "$P" ]; then "$H/slack_notify.sh" --title "다시 보냄" "📄 다시 보냄: $k" "$P" >/dev/null; else "$H/slack_notify.sh" "⚪ '$k' 문서를 못 찾음 (오늘 이후 보낸 문서만 검색)" >/dev/null; fi ;;
  esac
done
echo "$m" > "$S"
