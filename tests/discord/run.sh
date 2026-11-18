#!/usr/bin/env bash
# 디스코드 구현(discord_notify·discord_poll·discord_pending) 시험. 로컬 가짜 Discord(stub_discord.py)만 씀, 실제 호출 없음.
# 슬랙판과 같은 기록 모양(slack-sent.jsonl·slack-inbox.jsonl)을 남기는지가 핵심.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
S=${DISCORD_SCRIPTS:-$REPO/hubs/scripts}
PY=${PY:-python3}
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

HUB=$(mktemp -d "${TMPDIR:-/tmp}/cc-discord.XXXXXX")
STUB=""
trap '[ -n "$STUB" ] && kill "$STUB" 2>/dev/null; rm -rf "${HUB:?}"' EXIT
export LRM_HUB_DIR="$HUB"
printf 'DISCORD_BOT_TOKEN=%s\nDISCORD_USER_ID=42\n' "FAKE-TOKEN" > "$HUB/discord-bot.env"
LOG="$HUB/req.jsonl"

start() {  # 인자: 시드 JSON
  [ -n "$STUB" ] && kill "$STUB" 2>/dev/null
  : > "$LOG"; rm -f "$HUB/port"; printf '%s' "$1" > "$HUB/seed.json"
  "$PY" "$HERE/stub_discord.py" "$HUB/seed.json" "$LOG" "$HUB/port" & STUB=$!; disown "$STUB"
  for _ in $(seq 1 50); do [ -s "$HUB/port" ] && break; sleep 0.1; done
  export DISCORD_API_BASE="http://127.0.0.1:$(cat "$HUB/port")"
}
q() { "$PY" -c "import json,sys; d=[json.loads(l) for l in open(sys.argv[1])]; print($2)" "$1"; }

# ---- 보내기 ---------------------------------------------------------------
start '{}'
out=$("$PY" -I "$S/discord_notify.py" --tag ui-hub --kind done "알림 시각 통일"); rc=$?
[ "$rc" = 0 ] && [ "$out" = "ok 1001" ] && ok "보내기: 성공 출력 'ok <메시지 ID>'" || ko "보내기" "rc=$rc $out"
[ "$(q "$LOG" 'd[-1]["auth"]')" = "Bot FAKE-TOKEN" ] && ok "보내기: Bot 토큰 인증" || ko "인증 헤더" ""
[ "$(q "$LOG" 'd[-1]["body"]["content"]')" = "✅ **알림 시각 통일**" ] && ok "보내기: --kind 이모지·굵은 첫 줄" || ko "kind 표시" "$(q "$LOG" 'd[-1]["body"]["content"]')"
[ "$(q "$HUB/slack-sent.jsonl" '(d[-1]["ts"], d[-1]["tag"], d[-1]["form_kind"], d[-1]["kind"])')" = "('1001', 'ui-hub', 'done', 'text')" ] \
  && ok "보내기: slack-sent.jsonl 에 슬랙과 같은 모양 기록" || ko "보낸 기록" "$(tail -1 "$HUB/slack-sent.jsonl")"

out=$("$PY" -I "$S/discord_notify.py" --kind decide --no-doc --options '1=오전 9시 => 출근 전,2=오전 10시 => 업무 시작 뒤' \
  --recommend 1 --decision-id D-0001 $'알림 시각\n배경: 통일'); rc=$?
reacts=$(q "$LOG" '" ".join(x["path"].split("/reactions/")[1].split("/")[0] for x in d if x["method"]=="PUT")')
[ "$rc" = 0 ] && [ "$reacts" = "1️⃣ 2️⃣ ✅ ❌" ] && ok "결정 카드: 번호·✅·❌ 반응을 미리 닮" || ko "카드 반응" "rc=$rc [$reacts]"
q "$LOG" '[x for x in d if x["method"]=="POST" and "body" in x and "content" in x["body"]][-1]["body"]["content"]' | grep -q '1️⃣ 오전 9시 → 출근 전' \
  && ok "결정 카드: 선택지·결과 줄" || ko "카드 본문" ""
[ "$(q "$HUB/slack-sent.jsonl" '(d[-1]["decision_id"], d[-1]["options"], d[-1]["recommended"])')" = "('D-0001', {'1': '오전 9시', '2': '오전 10시'}, 1)" ] \
  && ok "결정 카드: decision_id·options·recommended 기록" || ko "카드 기록" "$(tail -1 "$HUB/slack-sent.jsonl")"

printf 'x' > "$HUB/a.html"
out=$("$PY" -I "$S/discord_notify.py" "보고서" "$HUB/a.html"); rc=$?
[ "$rc" = 0 ] && [ "$(q "$LOG" 'd[-1].get("multipart") and d[-1]["body_has_payload"]')" = True ] && [ "$(q "$HUB/slack-sent.jsonl" 'd[-1]["kind"]')" = file ] \
  && ok "파일 첨부: multipart 전송·kind=file 기록" || ko "파일 첨부" "rc=$rc"

n=$(wc -l < "$HUB/slack-sent.jsonl")
start '{"fail": true}'
"$PY" -I "$S/discord_notify.py" "x" >/dev/null 2>&1; rc=$?
[ "$rc" = 1 ] && [ "$(wc -l < "$HUB/slack-sent.jsonl")" = "$n" ] && ok "API 실패: 종료 코드 1·기록 안 함" || ko "API 실패" "rc=$rc"

mv "$HUB/discord-bot.env" "$HUB/env.bak"
out=$("$PY" -I "$S/discord_notify.py" "x" 2>&1); rc=$?
mv "$HUB/env.bak" "$HUB/discord-bot.env"
[ "$rc" = 2 ] && echo "$out" | grep -q 'discord-bot.env 없음' && ok "토큰 파일 없음: 종료 코드 2·원인·조치" || ko "토큰 파일 없음" "rc=$rc"
echo "$out" | grep -q 'FAKE' && ko "토큰 노출" "" || ok "오류 출력에 토큰 없음"

# ---- 받기 ---------------------------------------------------------------
CARD=$(q "$HUB/slack-sent.jsonl" '[x for x in d if x.get("decision_id")][-1]["ts"]')
BASE=$("$PY" -c 'import sys,time; sys.path.insert(0,sys.argv[1]); import discord_api as a; print(int(a.snowflake_at(time.time()-60)))' "$S")
U1=$((BASE + 1)) U2=$((BASE + 2))
# 카드 ID 를 최근 범위로 옮겨 기간 필터를 통과시킴
"$PY" - "$HUB/slack-sent.jsonl" "$CARD" "$BASE" <<'PY'
import json,sys
p,old,new=sys.argv[1:]; rows=[json.loads(l) for l in open(p)]
for r in rows:
    if r["ts"]==old: r["ts"]=new
open(p,"w").write("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in rows))
PY
start "{\"messages\": [
  {\"id\": \"$BASE\", \"author\": {\"id\": \"900\"}, \"content\": \"card\", \"reactions\": [{\"emoji\": {\"name\": \"2️⃣\"}, \"count\": 2, \"me\": true}, {\"emoji\": {\"name\": \"✅\"}, \"count\": 1, \"me\": true}]},
  {\"id\": \"$U1\", \"author\": {\"id\": \"42\"}, \"content\": \"현황\"},
  {\"id\": \"$U2\", \"author\": {\"id\": \"42\"}, \"content\": \"추천대로 ㄱㄱ\", \"message_reference\": {\"message_id\": \"$BASE\"}}],
  \"reactors\": {\"$BASE|2️⃣\": [\"900\", \"42\"]}}"
out=$("$PY" -I "$S/discord_poll.py" --once --json); rc=$?
[ "$rc" = 0 ] && [ "$out" = '{"ok": true, "added": 3}' ] && ok "받기: 새 항목 3건" || ko "받기" "rc=$rc $out"
IN="$HUB/slack-inbox.jsonl"
[ "$(q "$IN" '[(x["type"],x.get("command")) for x in d if x.get("ts")=="'$U1'"]')" = "[('command', 'status')]" ] && ok "받기: '현황' → command status" || ko "명령" ""
[ "$(q "$IN" '[(x["type"],x["parent_ts"],x["action"],x["pick"]) for x in d if x.get("ts")=="'$U2'"]')" = "[('thread_reply', '$BASE', 'approve', 1)]" ] \
  && ok "받기: 카드 답장 '추천대로' → thread_reply approve pick=1" || ko "답장" "$(cat "$IN")"
[ "$(q "$IN" '[(x["emoji"],x["action"],x["pick"],x["decision_id"]) for x in d if x["type"]=="reaction"]')" = "[('two', 'pick', 2, 'D-0001')]" ] \
  && ok "받기: 사용자 2️⃣ 반응만 → reaction emoji=two pick=2 (봇 혼자 단 ✅ 제외)" || ko "반응" "$(cat "$IN")"
out=$("$PY" -I "$S/discord_poll.py" --once --json)
[ "$out" = '{"ok": true, "added": 0}' ] && ok "받기 재실행: 중복 0건" || ko "중복" "$out"
out=$("$PY" -I "$S/discord_poll.py" 2>&1); rc=$?
[ "$rc" = 2 ] && ok "받기: --once 없으면 종료 코드 2" || ko "--once" "rc=$rc"

# ---- 답 기다리는 결정 -----------------------------------------------------
"$PY" -I "$S/discord_notify.py" --kind decide --no-doc --options '1=가,2=나' --recommend 2 --decision-id D-0002 "둘째 결정" >/dev/null
out=$("$PY" -I "$S/discord_pending.py" --dry-run)
echo "$out" | grep -q 'D-0002' && ! echo "$out" | grep -q 'D-0001' && ok "대기 목록: 답한 D-0001 제외, D-0002 포함" || ko "대기 목록" "$out"
"$PY" -I "$S/discord_pending.py" >/dev/null; first=$("$PY" -c 'import json,sys;print(json.load(open(sys.argv[1]))["msg"])' "$HUB/discord-pending-state.json")
"$PY" -I "$S/discord_pending.py" >/dev/null
[ "$(q "$LOG" '[x["path"] for x in d if x["method"]=="DELETE"]')" = "['/channels/500/messages/$first']" ] && [ -s "$HUB/slack-pending-history.jsonl" ] \
  && ok "대기 목록: 새로 보낸 뒤 이전 목록 삭제·이력 기록" || ko "목록 교체" ""
mkdir "$HUB/.slack-pending.lock"; "$PY" -I "$S/discord_pending.py" >/dev/null; rc=$?; rmdir "$HUB/.slack-pending.lock"
[ "$rc" = 75 ] && ok "대기 목록: 실행 중 잠금이면 종료 코드 75" || ko "잠금" "rc=$rc"

# ---- 새 인자(--card·--update·--broadcast) ------------------------------------
start '{}'
out=$("$PY" -I "$S/discord_notify.py" --card lrm-q5o.2 "카드 연결"); rc=$?
[ "$rc" = 0 ] && [ "$(q "$HUB/slack-sent.jsonl" 'd[-1].get("card")')" = "lrm-q5o.2" ] && ok "--card: sent 기록에 card 칸" || ko "--card" "rc=$rc"
"$PY" -I "$S/discord_notify.py" --card 'Bad Id' "x" >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "--card 형식 오류: 종료 코드 2" || ko "--card 형식" "rc=$rc"
mid=${out#ok }; n=$(wc -l < "$HUB/slack-sent.jsonl")
out=$("$PY" -I "$S/discord_notify.py" --update "$mid" "고친 본문"); rc=$?
[ "$rc" = 0 ] && [ "$out" = "ok $mid" ] && [ "$(q "$LOG" 'd[-1]["method"]+" "+d[-1]["path"]+" "+d[-1]["body"]["content"]')" = "PATCH /channels/500/messages/$mid 고친 본문" ] \
  && [ "$(wc -l < "$HUB/slack-sent.jsonl")" = "$n" ] && ok "--update: 본문 PATCH·sent 기록 안 함" || ko "--update" "rc=$rc $out"
"$PY" -I "$S/discord_notify.py" --update "$mid" --thread 1 "x" >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "--update + --thread: 종료 코드 2" || ko "--update 조합" "rc=$rc"
"$PY" -I "$S/discord_notify.py" --update 99999 "x" >/dev/null 2>&1; rc=$?
[ "$rc" = 1 ] && ok "--update 없는 메시지: 종료 코드 1" || ko "--update 없음" "rc=$rc"
out=$("$PY" -I "$S/discord_notify.py" --thread "$mid" --broadcast "답글"); rc=$?
[ "$rc" = 0 ] && [ "$(q "$LOG" 'd[-1]["body"]["message_reference"]["message_id"]')" = "$mid" ] && ok "--broadcast: 받고 답장으로 보냄" || ko "--broadcast" "rc=$rc"
"$PY" -I "$S/discord_notify.py" --broadcast "x" >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "--broadcast 단독: 종료 코드 2" || ko "--broadcast 단독" "rc=$rc"

printf '결과: PASS %d, FAIL %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
