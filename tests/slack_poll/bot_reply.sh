#!/usr/bin/env bash
# slack_poll.sh: slack-sent.jsonl 에 없는 봇 메시지(대기 목록·생존 경고·삭제된 메시지 자리)의 사용자 스레드 답글 수집 검증.
# 11-17 대기 목록 스레드에 단 사용자 답이 inbox 에 안 들어온 사고의 회귀 시험. 가짜 Slack API(로컬 HTTP)만 쓰고 임시 폴더에서만 읽고 씀.
set -uo pipefail
export LRM_PUSH=off LRM_ACK=off  # 메인 전달·확인 답장은 다른 시험이 맡음

HERE=$(cd "$(dirname "$0")" && pwd -P)
POLL="${BOT_REPLY_POLL:-$HERE/../../hubs/scripts/slack_poll.sh}"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-botreply.XXXXXX")
SRV=""
trap '[ -n "$SRV" ] && { kill "$SRV"; wait "$SRV"; } 2>/dev/null; rm -rf "${SB:?}"' EXIT
FIX="$SB/fix" CL="$SB/calls.log"
mkdir -p "$FIX"; : > "$CL"
ME=Uuser1; BOT=Ubot1
N=$(date +%s)
OLD=$((N - 7 * 3600))  # 수집 시작 시각(지금-6시간)보다 이전

mkhub() {
  mkdir -p "$1"
  printf 'SLACK_BOT_TOKEN=fake-token-0123456789\nSLACK_USER_ID=%s\n' "$ME" > "$1/slack-bot.env"
  printf '{"file_share_since":%s}' $((N - 60)) > "$1/slack-poll-state.json"
}
hist() { printf '{"ok":true,"messages":[%s]}' "$1" > "$FIX/conversations.history.json"; }
thread() { printf '{"ok":true,"messages":[%s]}' "$2" > "$FIX/conversations.replies.$1.json"; }
umsg() { printf '{"ts":"%s","user":"%s","text":"%s"}' "$1" "$ME" "$2"; }
botmsg() { printf '{"ts":"%s","user":"%s","bot_id":"B1","text":"%s"}' "$1" "$BOT" "$2"; }
pollh() { LRM_HUB_DIR="$1" SLACK_API_BASE="$API" bash "$POLL" --once > "$SB/$2.out" 2> "$SB/$2.err"; }
rcalls() { awk -F'\t' -v t="$1" '$1=="conversations.replies" && index($2, "ts=" t) {n++} END{print n+0}' "$CL"; }
inbox() { jq -r "$2" "$1/slack-inbox.jsonl" 2>/dev/null; }

printf '{"ok":true,"channel":{"id":"Dfake"}}' > "$FIX/conversations.open.json"
python3 -I "$HERE/fake_slack.py" "$FIX" "$SB/port" "$CL" &
SRV=$!
for _ in $(seq 1 50); do [ -s "$SB/port" ] && break; sleep 0.1; done
[ -s "$SB/port" ] || { ko "가짜 API 기동" "포트 파일 없음"; exit 1; }
API="http://127.0.0.1:$(cat "$SB/port")"

# 대기 목록(기록 없음)에 사용자 답글 1건 + 반응 / 삭제된 메시지 자리에 답글 1건 / 봇끼리만 답한 진행 스레드 / 수집 시작 전 답글
P=$N.000100 T=$N.000200 F=$N.000300 Q=$OLD.000100
hist "{\"ts\":\"$P\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\":raising_hand: *답을 기다리는 결정 5개* — 눌러서 이동\\n• 둘째 줄\",
   \"reply_count\":1,\"latest_reply\":\"$N.000110\",\"reply_users\":[\"$ME\"],\"reactions\":[{\"name\":\"white_check_mark\",\"users\":[\"$ME\"],\"count\":1}]},
 {\"ts\":\"$T\",\"user\":\"USLACKBOT\",\"subtype\":\"tombstone\",\"text\":\"This message was deleted.\",\"reply_count\":1,\"latest_reply\":\"$N.000210\",\"reply_users\":[\"$ME\"]},
 {\"ts\":\"$F\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"*피드백 처리 현황*\",\"reply_count\":5,\"latest_reply\":\"$N.000390\",\"reply_users\":[\"$BOT\"]},
 {\"ts\":\"$Q\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"옛 대기 목록\",\"reply_count\":1,\"latest_reply\":\"$OLD.000110\",\"reply_users\":[\"$ME\"]}"
thread "$P" "$(botmsg "$P" "대기 목록"),$(umsg "$N.000110" "이거 작업 다 마무리함")"
thread "$T" "{\"ts\":\"$T\",\"user\":\"USLACKBOT\",\"subtype\":\"tombstone\"},$(umsg "$N.000210" "삭제된 곳 답")"
thread "$F" "$(botmsg "$F" "현황"),$(botmsg "$N.000390" "갱신")"
thread "$Q" "$(botmsg "$Q" "옛 대기 목록"),$(umsg "$OLD.000110" "옛 답")"

H="$SB/hubA"; mkhub "$H"
pollh "$H" a1; rc=$?
[ "$rc" = 0 ] && ok "종료 코드 0" || ko "종료 코드" "rc=$rc $(cat "$SB/a1.err")"
[ "$(inbox "$H" "select(.ts==\"$N.000110\") | [.type, .parent_ts, .title, .text] | join(\"|\")")" = "thread_reply|$P|:raising_hand: 답을 기다리는 결정 5개 — 눌러서 이동|이거 작업 다 마무리함" ] \
  && ok "기록 없는 대기 목록의 사용자 답글 → thread_reply(제목=봇 메시지 첫 줄, '*' 제거)" || ko "대기 목록 답글" "$(cat "$H/slack-inbox.jsonl" 2>/dev/null)"
[ "$(inbox "$H" "select(.ts==\"$N.000210\") | .title")" = "삭제된 봇 메시지" ] && ok "삭제된 메시지 자리(tombstone)의 답글도 수집(제목 고정 문구)" || ko "tombstone 답글" "$(cat "$H/slack-inbox.jsonl" 2>/dev/null)"
[ "$(inbox "$H" "select(.ts==\"$N.000110\") | has(\"decision_id\") or has(\"action\")")" = false ] && ok "결정 해석 필드 없음(원문만)" || ko "해석 필드" "$(inbox "$H" "select(.ts==\"$N.000110\")")"
[ "$(inbox "$H" 'select(.type=="reaction")' | wc -l | tr -d ' ')" = 0 ] && ok "기록 없는 봇 메시지의 반응은 그대로 수집 안 함(결정 답 오판 방지)" || ko "반응" "$(inbox "$H" 'select(.type=="reaction")')"
[ "$(rcalls "$F")" = 0 ] && ok "봇끼리만 답한 스레드(reply_users 에 본인 없음)는 답글 조회 생략" || ko "봇 스레드 조회" "$(rcalls "$F")"
[ "$(rcalls "$Q")" = 0 ] && [ -z "$(inbox "$H" "select(.ts==\"$OLD.000110\")")" ] && ok "수집 시작 시각(첫 실행-6시간) 이전 답글은 조회·기록 안 함" || ko "옛 답글" "$(rcalls "$Q")"
[ "$(jq -r '.bot_reply_since' "$H/slack-poll-state.json")" -le $((N - 6 * 3600)) ] && ok "상태에 bot_reply_since 기록(첫 실행-6시간)" || ko "상태 키" "$(jq -c . "$H/slack-poll-state.json" | cut -c1-200)"
[ "$(jq -r ".reacted[\"$N.000110\"]" "$H/slack-poll-state.json")" = eyes ] && ok "새 답글에 읽음 반응 👀 달림(reacted=eyes)" || ko "읽음 반응" "$(jq -c '.reacted' "$H/slack-poll-state.json")"
grep -q "^reactions.add	.*timestamp=$N.000110" "$CL" && ok "reactions.add 호출 대상 = 사용자 답글 ts" || ko "reactions.add" "$(grep reactions "$CL")"

before=$(wc -l < "$H/slack-inbox.jsonl" | tr -d ' ')
pollh "$H" a2
[ "$(wc -l < "$H/slack-inbox.jsonl" | tr -d ' ')" = "$before" ] && [ "$(rcalls "$P")" = 1 ] && ok "재실행: 중복 기록·답글 재조회 없음" || ko "재실행" "$(rcalls "$P")"

echo
echo "결과: PASS $PASS, FAIL $FAIL"
[ "$FAIL" = 0 ]
