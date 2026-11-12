#!/usr/bin/env bash
# 결정 카드 스레드 글 답: 폴러 해석(slack_poll.sh reply_answer) → 메인 전달문(slack_push.py render) → 버튼 전달문(slack_socket.py said_text).
# 가짜 Slack API(로컬 HTTP)만 사용하며 실제 Slack·토큰·네트워크·orca 는 쓰지 않음. 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함.
# 시나리오 R 스레드 글 해석(보낸 기록 모양별), T 전달문 문구, B 버튼·직접 입력 전달문
set -uo pipefail
export LRM_PUSH=off PYTHONDONTWRITEBYTECODE=1

HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS="$HERE/../../hubs/scripts"
POLL="$SCRIPTS/slack_poll.sh" PUSH_PY="$SCRIPTS/slack_push.py" SOCK_PY="$SCRIPTS/slack_socket.py"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-slackreply.XXXXXX")
SRV=""
trap '[ -n "$SRV" ] && { kill "$SRV"; wait "$SRV"; } 2>/dev/null; rm -rf "${SB:?}"' EXIT
FIX="$SB/fix" CL="$SB/calls.log"
mkdir -p "$FIX"; : > "$CL"
FAKE_TOKEN="fake-token-0123456789-abcdefghij"
ME=Uuser1; BOT=Ubot1
N=$(date +%s)
T() { printf '%s.%s' "$N" "$1"; }

H="$SB/hub"; mkdir -p "$H"
printf 'SLACK_BOT_TOKEN=%s\nSLACK_USER_ID=%s\n' "$FAKE_TOKEN" "$ME" > "$H/slack-bot.env"
printf '{"file_share_since":%s,"thread_broadcast_since":%s}' $((N - 60)) $((N - 60)) > "$H/slack-poll-state.json"
printf '{"ok":true,"channel":{"id":"Dfake"}}' > "$FIX/conversations.open.json"
python3 -I "$HERE/fake_slack.py" "$FIX" "$SB/port" "$CL" &
SRV=$!
for _ in $(seq 1 50); do [ -s "$SB/port" ] && break; sleep 0.1; done
[ -s "$SB/port" ] || { ko "가짜 API 기동" "포트 파일 없음"; exit 1; }
API="http://127.0.0.1:$(cat "$SB/port")"

# 보낸 기록: R1 일반 번호 카드, R2 파일 첨부 카드(ts 없음·file_id, 실제 D-SUMAB 모양), R3 옵션 없는 옛 결정 카드, R4 옵션 모양이 틀린 카드
O2='{"1":"A 페이드+위로","2":"B 줄별 팝"}'
{
  jq -nc --arg ts "$(T 007100)" --argjson at "$N" --argjson o "$O2" '{at:$at,channel:"Dfake",ts:$ts,thread_ts:"",kind:"text",title:"하루 결산 연출",tag:"",form_kind:"decide",decision_id:"D-0R01",options:$o,recommended:2}'
  jq -nc --argjson at "$N" --argjson o "$O2" '{at:$at,channel:"Dfake",ts:"",thread_ts:"",kind:"file",title:"하루 결산 연출 파일",tag:"",form_kind:"decide",decision_id:"D-0R02",options:$o,recommended:1,file_id:"F61",path:"/x.mp4"}'
  jq -nc --arg ts "$(T 007300)" --argjson at "$N" '{at:$at,channel:"Dfake",ts:$ts,thread_ts:"",kind:"text",title:"결정 옛 카드",tag:"",form_kind:"decide"}'
  jq -nc --arg ts "$(T 007400)" --argjson at "$N" '{at:$at,channel:"Dfake",ts:$ts,thread_ts:"",kind:"text",title:"깨진 카드",tag:"",form_kind:"decide",decision_id:"D-0R04",options:["가","나"],recommended:1}'
} > "$H/slack-sent.jsonl"

# 카드 메시지(봇) + 스레드 답글(사용자) 가짜 응답. 답글 ts 는 카드 ts 뒤
parent() { # ts [file_id] 답글수
  local f=""; [ -n "${2:-}" ] && f=",\"subtype\":\"file_share\",\"files\":[{\"id\":\"$2\"}]"
  printf '{"ts":"%s","user":"%s","bot_id":"B1","text":"m"%s,"reply_count":%s,"latest_reply":"%s.999999"}' "$1" "$BOT" "$f" "$3" "$N"
}
replies() { # 카드 ts 글...: conversations.replies 응답 파일. 글마다 ts 는 카드 ts 끝자리 + 순번
  local ts="$1" i=0 msgs; shift
  msgs=$(printf '{"ts":"%s","user":"%s","bot_id":"B1","text":"m"}' "$ts" "$BOT")
  for t in "$@"; do
    i=$((i + 1))
    msgs="$msgs,$(jq -nc --arg ts "$(T "$((${ts##*.} + i))")" --arg u "$ME" --arg t "$t" '{ts:$ts,user:$u,text:$t}')"
  done
  printf '{"ok":true,"messages":[%s]}' "$msgs" > "$FIX/conversations.replies.$ts.json"
}
R1TEXTS=(A B "🅱️" ":비서-a:" ":b:" 1 2 ":one:" "2번" "추천" "전부 추천" "추천대로 ㄱㄱ" "전부 추천대로 할게" "a." "3" "E" "1. 좌에서 우로 카드가 나오면서 연출" "이거 링크 줄수있나?" "b 로 하되 느리게")
replies "$(T 007100)" "${R1TEXTS[@]}"
R2TEXTS=(A "추천")
replies "$(T 007200)" "${R2TEXTS[@]}"
replies "$(T 007300)" A
replies "$(T 007400)" A
printf '{"ok":true,"messages":[%s]}' "$(parent "$(T 007100)" "" 17),$(parent "$(T 007200)" F61 2),$(parent "$(T 007300)" "" 1),$(parent "$(T 007400)" "" 1)" > "$FIX/conversations.history.json"

LRM_ACK=off LRM_HUB_DIR="$H" SLACK_API_BASE="$API" bash "$POLL" --once > "$SB/p.out" 2> "$SB/p.err"; rc=$?
[ "$rc" = 0 ] && ok "R 수집 종료 코드 0" || ko "R 수집" "rc=$rc $(cat "$SB/p.err")"

got() { # 카드 ts 접미사: 그 카드 스레드 글의 해석 필드(없는 키 생략), 글 순서대로 한 줄씩
  jq -c --arg p "$(T "$1")" 'select(.type=="thread_reply" and .parent_ts==$p)
    | {text,action,kind,pick,decision_id,option_text} | with_entries(select(.value!=null))' "$H/slack-inbox.jsonl"
}
want() { # 이름 카드ts접미사 글 기대(action[,pick])
  local g; g=$(got "$2" | jq -c --arg t "$3" 'select(.text==$t) | [.action,.pick]' | head -1)
  [ "$g" = "$4" ] && ok "R $1" || ko "R $1" "got=$g want=$4"
}
want "R1 'A' → pick 1" 007100 A '["pick",1]'
want "R1 'B' → pick 2" 007100 B '["pick",2]'
want "R1 🅱️ → pick 2" 007100 "🅱️" '["pick",2]'
want "R1 :비서-a: → pick 1" 007100 ":비서-a:" '["pick",1]'
want "R1 :b: → pick 2" 007100 ":b:" '["pick",2]'
want "R1 '1' → pick 1" 007100 1 '["pick",1]'
want "R1 '2번' → pick 2" 007100 "2번" '["pick",2]'
want "R1 :one: → pick 1" 007100 ":one:" '["pick",1]'
want "R1 '추천' → approve, pick=추천 번호 2" 007100 "추천" '["approve",2]'
want "R1 '전부 추천' → approve" 007100 "전부 추천" '["approve",2]'
want "R1 '추천대로 ㄱㄱ' → approve" 007100 "추천대로 ㄱㄱ" '["approve",2]'
want "R1 '전부 추천대로 할게' → approve(꼬리말 '할게')" 007100 "전부 추천대로 할게" '["approve",2]'
want "R1 'a.' → pick 1" 007100 "a." '["pick",1]'
want "R1 선택지 밖 '3' → 자유 글" 007100 3 '["free",null]'
want "R1 'E' → 자유 글" 007100 E '["free",null]'
want "R1 '1. 좌에서…' 긴 글의 머리 숫자는 선택이 아님 → 자유 글" 007100 "1. 좌에서 우로 카드가 나오면서 연출" '["free",null]'
want "R1 질문 글 → 자유 글" 007100 "이거 링크 줄수있나?" '["free",null]'
want "R1 'b 로 하되 느리게' → 자유 글" 007100 "b 로 하되 느리게" '["free",null]'
want "R2 파일 첨부 카드(ts 없음) 'A' → pick 1" 007200 A '["pick",1]'
want "R2 파일 첨부 카드 '추천' → approve, pick=추천 번호 1" 007200 "추천" '["approve",1]'
d=$(got 007100 | jq -rs 'map(select(.action!="free"))|map(.decision_id)|unique|join(",")')
[ "$d" = D-0R01 ] && ok "R 결정 번호·kind 가 레코드에 붙음" || ko "R 결정 번호" "$d"
g3=$(got 007300 | jq -c '[.kind,.decision_id,.action]')
[ "$g3" = '[null,null,null]' ] && ok "R 옵션·결정 번호 없는 옛 카드의 글은 레코드 모양 불변(해석 필드 없음)" || ko "R 옛 카드" "$g3"
g4=$(got 007400 | jq -c '[.kind,.decision_id,.action,.pick]')
[ "$g4" = '["decide","D-0R04","free",null]' ] && ok "R 옵션 모양이 깨진 카드의 'A' → 자유 글(선택 번호를 알 수 없음)" || ko "R 깨진 카드" "$g4"

# ---- C. 스레드 글 선택의 충돌 판정·대기 목록 닫힘 -------------------------------------------------
H2="$SB/hub2"; mkdir -p "$H2"
cp "$H/slack-bot.env" "$H2/"; cp "$H/slack-poll-state.json" "$H2/"
{
  for p in 007500:D-0C01 007600:D-0C02 007700:D-0C03 007800:D-0C04 007900:D-0C05 007650:D-0C06; do
    jq -nc --arg ts "$(T "${p%%:*}")" --arg d "${p#*:}" --argjson at "$N" --argjson o "$O2" '{at:$at,channel:"Dfake",ts:$ts,thread_ts:"",kind:"text",title:"충돌 시험",tag:"",form_kind:"decide",decision_id:$d,options:$o,recommended:2}'
  done
} > "$H2/slack-sent.jsonl"
# 앞 회차에 이미 들어온 스레드 글 'A'(pick 1)
jq -nc --arg ts "$(T 007900)" '{type:"thread_reply",ts:"1.1",text:"A",action:"pick",kind:"decide",decision_id:"D-0C05",pick:1,option_text:"A 페이드+위로",parent_ts:$ts,title:"충돌 시험",tag:"",channel:"Dfake",received_at:1}' > "$H2/slack-inbox.jsonl"
rm -f "$FIX"/conversations.replies.*.json
replies "$(T 007500)" A; replies "$(T 007600)" A B; replies "$(T 007700)" A; replies "$(T 007800)" "이거 링크 줄수있나?"; replies "$(T 007650)" A "추천"
pr() { # ts 답글수 [반응 이름]
  local r=""; [ -n "${3:-}" ] && r=",\"reactions\":[{\"name\":\"$3\",\"users\":[\"$ME\"],\"count\":1}]"
  printf '{"ts":"%s","user":"%s","bot_id":"B1","text":"m","reply_count":%s,"latest_reply":"%s.999999"%s}' "$1" "$BOT" "$2" "$N" "$r"
}
printf '{"ok":true,"messages":[%s]}' "$(pr "$(T 007500)" 1 two),$(pr "$(T 007600)" 2),$(pr "$(T 007700)" 1),$(pr "$(T 007800)" 1),$(pr "$(T 007900)" 0 two),$(pr "$(T 007650)" 2)" > "$FIX/conversations.history.json"
LRM_ACK=off LRM_HUB_DIR="$H2" SLACK_API_BASE="$API" bash "$POLL" --once > "$SB/c.out" 2> "$SB/c.err"; rc=$?
[ "$rc" = 0 ] && ok "C 수집 종료 코드 0" || ko "C 수집" "rc=$rc $(cat "$SB/c.err")"
cf() { jq -c --arg p "$(T "$1")" 'select(.parent_ts==$p and .received_at!=1 and .action!=null and .action!="free") | [.type,.action,.pick,.conflict,.choices]' "$H2/slack-inbox.jsonl" | tr '\n' ' '; }
[ "$(cf 007500)" = '["reaction","pick",2,true,["1","2"]] ["thread_reply","pick",1,true,["1","2"]] ' ] || [ "$(cf 007500)" = '["thread_reply","pick",1,true,["1","2"]] ["reaction","pick",2,true,["1","2"]] ' ] \
  && ok "C 스레드 'A'(1) + 반응 :two: → 둘 다 conflict·choices(1,2)" || ko "C 글+반응 충돌" "$(cf 007500)"
[ "$(cf 007600)" = '["thread_reply","pick",1,true,["1","2"]] ["thread_reply","pick",2,true,["1","2"]] ' ] && ok "C 스레드 글끼리 'A'·'B' → conflict" || ko "C 글끼리 충돌" "$(cf 007600)"
[ "$(cf 007700)" = '["thread_reply","pick",1,null,null] ' ] && ok "C 스레드 글 'A' 하나 → 충돌 없음" || ko "C 글 하나" "$(cf 007700)"
[ "$(cf 007900)" = '["reaction","pick",2,true,["1","2"]] ' ] && ok "C 앞 회차 스레드 'A' 뒤 반응 :two: → conflict" || ko "C 앞 회차 충돌" "$(cf 007900)"
[ "$(cf 007650)" = '["thread_reply","pick",1,null,null] ["thread_reply","approve",2,true,["1","2"]] ' ] || [ "$(cf 007650)" = '["thread_reply","pick",1,true,["1","2"]] ["thread_reply","approve",2,true,["1","2"]] ' ] \
  && ok "C 'A'(1) 뒤 '추천'(approve 2) → 서로 다른 선택이라 conflict" || ko "C 글+추천" "$(cf 007650)"
LRM_HUB_DIR="$H2" SLACK_API_BASE="$API" bash "$SCRIPTS/slack_pending.sh" --dry-run > "$SB/c2.out" 2> "$SB/c2.err"; rc=$?
S2="$SB/c2.out"
[ "$rc" = 0 ] && ok "C 대기 목록 종료 코드 0" || ko "C 대기 목록" "rc=$rc $(cat "$SB/c2.err")"
grep -qF "수정 예정: $(T 007700) → ✅ 충돌 시험 — 답 도착(선택 1)" "$S2" && ok "C 스레드 글 'A' 만 있는 카드 → 대기 종료(답 도착)" || ko "C 글 답 닫힘" "$(cat "$S2")"
grep -qF "D-0C04" "$S2" && ! grep -qF "수정 예정: $(T 007800)" "$S2" && ok "C 자유 글만 있는 카드는 계속 대기" || ko "C 자유 글 대기" "$(cat "$S2")"
grep -qF "D-0C01 — 선택이 여러 개(1, 2), 확인 필요" "$S2" && grep -qF "D-0C02 — 선택이 여러 개(1, 2), 확인 필요" "$S2" && ok "C 충돌 카드(글+반응·글끼리)는 닫지 않고 확인 필요 표시" || ko "C 충돌 표시" "$(cat "$S2")"

# ---- T. 메인 전달문 -------------------------------------------------------------------------------
cat > "$SB/render.py" <<'EOF'
import json, sys
ns = {"__name__": "push_under_test", "__file__": sys.argv[1]}
exec(compile(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1], "exec"), ns)
items = [(i + 1, json.loads(l)) for i, l in enumerate(open(sys.argv[2], encoding="utf-8")) if l.strip()]
sys.stdout.write(ns["render"](items, "/tmp/batch.md", 0))
EOF
mkdir -p "$SB/rh"
LRM_HUB_DIR="$SB/rh" python3 -I "$SB/render.py" "$PUSH_PY" "$H/slack-inbox.jsonl" > "$SB/q.txt"
line() { grep -F -- "$1" "$SB/q.txt" | head -1 | sed -E 's/^- #[0-9]+ //'; }
has() { grep -qF -- "$2" "$SB/q.txt" && ok "T $1" || ko "T $1" "없음: $2 / $(grep -c . "$SB/q.txt")줄"; }
has "R1 스레드에 A·B·추천이 섞이면 전달문도 '선택이 여러 개' (충돌)" "→ 결정 D-0R01 = 선택이 여러 개(1, 2) — 사용자에게 확인 (스레드 글)"
has "파일 카드 'A' → 결정 D-0R02 = 선택 1(문구) (스레드 글)" "→ 결정 D-0R02 = 선택 1(A 페이드+위로) (스레드 글)"
has "파일 카드 '추천'(추천=1, 'A' 와 같은 선택) → 충돌 아님, 추천대로(선택 1)" "→ 결정 D-0R02 = 추천대로(선택 1) (스레드 글)"
has "자유 글 → 결정 D-0R01 답(자유 글) + 원문 인용" "→ 결정 D-0R01 답(자유 글)"
grep -qF "> 1. 좌에서 우로 카드가 나오면서 연출" "$SB/q.txt" && ok "T 자유 글 원문은 그대로 인용" || ko "T 자유 글 원문" "$(cat "$SB/q.txt" | head -5)"
grep -E '^- #[0-9]+ \[깨진 카드\]' "$SB/q.txt" | grep -qF "답(자유 글)" && ok "T 깨진 카드의 글도 '답(자유 글)'" || ko "T 깨진 카드" "$(grep 깨진 "$SB/q.txt")"
grep -E '^- #[0-9]+ \[결정 옛 카드\]' "$SB/q.txt" | grep -qF "→" && ko "T 옛 카드는 해석 문구 없음" "$(grep '옛 카드' "$SB/q.txt")" || ok "T 옛 카드(옵션 없음)의 글은 기존 전달문 그대로(해석 문구 없음)"
n=$(grep -c "^- #" "$SB/q.txt"); [ "$n" = 23 ] && ok "T 글 23건 모두 전달(누락 없음)" || ko "T 건수" "$n: $(grep '^- #' "$SB/q.txt" | cut -c1-60 | tr '\n' '|')"

# ---- B. 버튼·직접 입력 전달문 ------------------------------------------------------------------
cat > "$SB/said.py" <<'EOF'
import sys, types
sys.modules["slack_sdk"] = types.ModuleType("slack_sdk")
src = open(sys.argv[1], encoding="utf-8").read().split("\ndef said_text", 1)[1].split("\nclass Decide", 1)[0]
import re
ns = {"re": re}
exec("def said_text" + src, ns)
f = ns["said_text"]
print(f("D-0B01", {"action": "pick", "pick": 1}, "1. A 유지"))
print(f("D-0B02", {"action": "pick", "pick": 2}, "2. B 줄별 팝"))
print(f("D-0B03", {"action": "custom", "text": "1. 양식을 제대로 정해야할듯"}, "직접 입력: 1. 양식을 제대로 정해야할듯"))
EOF
b=$(python3 -I "$SB/said.py" "$SOCK_PY")
[ "$(printf '%s' "$b" | sed -n 1p)" = "결정 D-0B01 = 선택 1(A 유지) (버튼)" ] && ok "B 버튼 pick → '결정 D-0B01 = 선택 1(A 유지) (버튼)'" || ko "B pick" "$b"
[ "$(printf '%s' "$b" | sed -n 2p)" = "결정 D-0B02 = 선택 2(B 줄별 팝) (버튼)" ] && ok "B 버튼 pick 2" || ko "B pick 2" "$b"
[ "$(printf '%s' "$b" | sed -n 3p)" = "결정 D-0B03 답(자유 글): 1. 양식을 제대로 정해야할듯" ] && ok "B 직접 입력 → '결정 D-0B03 답(자유 글): 원문'" || ko "B custom" "$b"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
