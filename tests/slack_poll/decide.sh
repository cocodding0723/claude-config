#!/usr/bin/env bash
# 번호 이모지 결정 카드·할 일 카드: 반응 해석(slack_poll.sh), 전달문(slack_push.py), 대기 목록 표시(slack_pending.sh) 검증.
# 가짜 Slack API(로컬 HTTP)만 사용하며 실제 Slack·토큰·네트워크·orca 는 쓰지 않음. 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함.
# 시나리오 P 반응 해석, Q 전달문(render 단위), S 대기 목록(--dry-run). 전송 경로(orca·보류)는 push.sh 가 맡음
set -uo pipefail
export LRM_PUSH=off PYTHONDONTWRITEBYTECODE=1  # 폴러 끝의 메인 전달이 실제 orca 를 부르지 않게 함

HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS="$HERE/../../hubs/scripts"
POLL="$SCRIPTS/slack_poll.sh" PUSH_PY="$SCRIPTS/slack_push.py" PENDING="$SCRIPTS/slack_pending.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-slackdecide.XXXXXX")
SRV=""
trap '[ -n "$SRV" ] && { kill "$SRV"; wait "$SRV"; } 2>/dev/null; rm -rf "${SB:?}"' EXIT
FIX="$SB/fix" CL="$SB/calls.log"
mkdir -p "$FIX"; : > "$CL"
FAKE_TOKEN="fake-token-0123456789-abcdefghij"
ME=Uuser1; BOT=Ubot1
N=$(date +%s)

mkhub() { # 허브 폴더: 설정 파일과 수집 경계 시각만 있는 새 허브
  mkdir -p "$1"
  printf 'SLACK_BOT_TOKEN=%s\nSLACK_USER_ID=%s\n' "$FAKE_TOKEN" "$ME" > "$1/slack-bot.env"
  printf '{"file_share_since":%s,"thread_broadcast_since":%s}' $((N - 60)) $((N - 60)) > "$1/slack-poll-state.json"
}
printf '{"ok":true,"channel":{"id":"Dfake"}}' > "$FIX/conversations.open.json"
printf '{"ok":true,"messages":[]}' > "$FIX/conversations.replies.json"
python3 -I "$HERE/fake_slack.py" "$FIX" "$SB/port" "$CL" &
SRV=$!
for _ in $(seq 1 50); do [ -s "$SB/port" ] && break; sleep 0.1; done
[ -s "$SB/port" ] || { ko "가짜 API 기동" "포트 파일 없음"; exit 1; }
API="http://127.0.0.1:$(cat "$SB/port")"
pollh() { LRM_ACK=off LRM_HUB_DIR="$1" SLACK_API_BASE="$API" bash "$POLL" --once > "$SB/$2.out" 2> "$SB/$2.err"; }

# conversations.history 메시지 1개. 반응 모양은 실제 응답과 같음: reactions:[{name,users,count}]
bot() { # ts [file_id] -- 이모지 이름...
  local ts="$1" f="" s="" n; shift
  if [ "$1" != -- ]; then f=",\"subtype\":\"file_share\",\"files\":[{\"id\":\"$1\"}]"; shift; fi
  shift
  for n in "$@"; do s="$s${s:+,}{\"name\":\"$n\",\"users\":[\"$ME\"],\"count\":1}"; done
  printf '{"ts":"%s","user":"%s","bot_id":"B1","text":"m"%s,"reactions":[%s]}' "$ts" "$BOT" "$f" "$s"
}
OPTS4='{"1":"오전 9시","2":"오전 10시","3":"지금처럼","4":"저녁 6시"}'
card() { # ts 선택지수 추천 결정번호 — slack_notify.sh --options 가 남기는 보낸 기록 모양
  jq -nc --arg ts "$1" --argjson n "$2" --argjson r "$3" --arg d "$4" --argjson at "$N" --argjson o "$OPTS4" \
    '{at:$at,channel:"Dfake",ts:$ts,thread_ts:"",kind:"text",title:"알림 시각",tag:"ui",form_kind:"decide",
      decision_id:$d,options:($o|with_entries(select((.key|tonumber)<=$n))),recommended:$r}'
}
T() { printf '%s.%s' "$N" "$1"; }

# ---- P. 반응 해석 ------------------------------------------------------------------------------
H="$SB/hubP"; mkhub "$H"
LONG=$(printf '긴%.0s' $(seq 1 90))
{
  card "$(T 005100)" 3 2 D-0001
  card "$(T 005200)" 3 2 D-0002
  card "$(T 005300)" 3 2 D-0003
  card "$(T 005400)" 3 2 D-0004
  card "$(T 005500)" 2 1 D-0005
  printf '{"at":%s,"channel":"Dfake","ts":"%s","thread_ts":"","kind":"text","title":"결정 6","tag":"ui","form_kind":"decide"}\n' "$N" "$(T 005600)"
  printf '{"at":%s,"channel":"Dfake","ts":"%s","thread_ts":"","kind":"text","title":"할 일 1","tag":"ui","form_kind":"todo","todo_id":"T-0001"}\n' "$N" "$(T 005700)"
  printf '{"at":%s,"channel":"Dfake","ts":"%s","thread_ts":"","kind":"text","title":"할 일 2","tag":"ui","form_kind":"todo"}\n' "$N" "$(T 005800)"
  printf '{"at":%s,"channel":"Dfake","ts":"%s","thread_ts":"","kind":"text","title":"할 일 3","tag":"ui","form_kind":"todo","todo_id":"T-0009"}\n' "$N" "$(T 005900)"
  printf '{"at":%s,"channel":"Dfake","ts":"","file_id":"F51","kind":"file","title":"시안 1","tag":"ui"}\n' "$N"
  card "$(T 006100)" 3 1 D-0011
  card "$(T 006200)" 3 1 D-0012 | jq -c '.options = ["가","나"]'
  card "$(T 006250)" 3 9 D-0015
  card "$(T 006300)" 3 2 D-0013
  card "$(T 006400)" 3 1 D-0014
  card "$(T 006600)" 3 1 D-0016 | jq -c --arg l "$LONG" '.options["1"] = $l'
  card "$(T 006700)" 3 1 D-0017 | jq -c '.form_kind = "todo"'
} > "$H/slack-sent.jsonl"
# 앞 회차에 이미 들어온 선택 1(P13·P14). 사용자가 1 을 취소하고 다른 반응을 누른 상황
for p in 006300:D-0013 006400:D-0014; do
  jq -nc --arg ts "$(T "${p%%:*}")" --arg d "${p#*:}" '{type:"reaction",emoji:"one",action:"pick",kind:"decide",decision_id:$d,pick:1,option_text:"오전 9시",parent_ts:$ts,title:"알림 시각",tag:"ui",channel:"Dfake",received_at:1}'
done > "$H/slack-inbox.jsonl"
printf '{"ok":true,"messages":[%s]}' "$(bot "$(T 005100)" -- two),$(bot "$(T 005200)" -- white_check_mark),$(bot "$(T 005300)" -- x),$(bot "$(T 005400)" -- four),\
$(bot "$(T 005500)" -- three one),$(bot "$(T 005600)" -- white_check_mark 비서-a one),$(bot "$(T 005700)" -- white_check_mark),\
$(bot "$(T 005800)" -- hammer_and_wrench 비서-추천대로),$(bot "$(T 005900)" -- x),$(bot "$(T 006000)" F51 -- white_check_mark one),\
$(bot "$(T 006100)" -- one two),$(bot "$(T 006200)" -- white_check_mark),$(bot "$(T 006250)" -- x),$(bot "$(T 006300)" -- x),\
$(bot "$(T 006400)" -- white_check_mark),$(bot "$(T 006600)" -- one),$(bot "$(T 006700)" -- one white_check_mark)" > "$FIX/conversations.history.json"
pollh "$H" p1; rc=$?
got() { # ts 접미사: 그 메시지 반응 줄의 해석 필드(없는 키 생략). 여러 줄이면 공백으로 이음
  jq -c --arg p "$(T "$1")" 'select(.type=="reaction" and .parent_ts==$p and .received_at!=1)
    | {emoji,action,kind,pick,decision_id,option_text,conflict,choices,todo_id} | with_entries(select(.value!=null))' "$H/slack-inbox.jsonl" | tr '\n' ' '
}
want() { # 이름 ts접미사 기대값
  local g; g=$(got "$2")
  [ "$g" = "$3" ] && ok "P $1" || ko "P $1" "got=$g"
}
[ "$rc" = 0 ] && ok "P 수집 종료 코드 0" || ko "P 수집" "rc=$rc $(cat "$SB/p1.err")"
want "번호 반응 :two: → pick 2, 문구, 결정 번호" 005100 '{"emoji":"two","action":"pick","kind":"decide","pick":2,"decision_id":"D-0001","option_text":"오전 10시"} '
want "✅ → approve, pick=추천 번호 2(1 고정 아님)" 005200 '{"emoji":"white_check_mark","action":"approve","kind":"decide","pick":2,"decision_id":"D-0002","option_text":"오전 10시"} '
want "❌ → hold, pick 없음" 005300 '{"emoji":"x","action":"hold","kind":"decide","decision_id":"D-0003"} '
want "선택지 3개 카드의 :four: → 범위 밖, 이름 그대로" 005400 '{"emoji":"four","action":"four","kind":"decide","decision_id":"D-0004"} '
want "선택지 2개 카드: :three: 는 범위 밖 이름 그대로, :one: 은 pick 1(범위 밖 반응은 충돌로 안 셈)" 005500 '{"emoji":"three","action":"three","kind":"decide","decision_id":"D-0005"} {"emoji":"one","action":"pick","kind":"decide","pick":1,"decision_id":"D-0005","option_text":"오전 9시"} '
want "옛 결정 카드(옵션 없음): ✅=approve :비서-a:=a :one:=이름 그대로, kind·pick 없음(기존 모양)" 005600 '{"emoji":"white_check_mark","action":"approve"} {"emoji":"비서-a","action":"a"} {"emoji":"one","action":"one"} '
want "할 일 ✅ → done, todo_id" 005700 '{"emoji":"white_check_mark","action":"done","kind":"todo","todo_id":"T-0001"} '
want "할 일 🛠️ → done, :비서-추천대로: 는 이름 그대로, todo_id 없으면 키 없음" 005800 '{"emoji":"hammer_and_wrench","action":"done","kind":"todo"} {"emoji":"비서-추천대로","action":"비서-추천대로","kind":"todo"} '
want "할 일 ❌ → hold(보류)" 005900 '{"emoji":"x","action":"hold","kind":"todo","todo_id":"T-0009"} '
r10=$(jq -c 'select(.type=="reaction" and (.parent_ts|startswith("'"$N"'.006000"))) | "\(.emoji)=\(.action)=\(.kind)"' "$H/slack-inbox.jsonl" | tr '\n' ' ')
[ "$r10" = '"white_check_mark=pass=review" "one=one=review" ' ] && ok "P 시안(review) 해석 불변: ✅=pass, :one: 은 이름 그대로" || ko "P 시안 불변" "$r10"
want "한 카드에 :one:·:two: → 둘 다 남기고 conflict·choices(1,2)" 006100 '{"emoji":"one","action":"pick","kind":"decide","pick":1,"decision_id":"D-0011","option_text":"오전 9시","conflict":true,"choices":["1","2"]} {"emoji":"two","action":"pick","kind":"decide","pick":2,"decision_id":"D-0011","option_text":"오전 10시","conflict":true,"choices":["1","2"]} '
want "옵션 모양이 틀린 기록(목록) → 해석 안 함(✅ 도 이름 그대로)" 006200 '{"emoji":"white_check_mark","action":"white_check_mark","kind":"decide"} '
want "추천 번호가 범위 밖인 기록 → 해석 안 함(❌ 도 이름 그대로)" 006250 '{"emoji":"x","action":"x","kind":"decide"} '
want "앞 회차 pick 1 뒤 ❌ → conflict·choices(1,보류)" 006300 '{"emoji":"x","action":"hold","kind":"decide","decision_id":"D-0013","conflict":true,"choices":["1","hold"]} '
want "앞 회차 pick 1 뒤 ✅(추천 1) → 같은 답이라 충돌 아님" 006400 '{"emoji":"white_check_mark","action":"approve","kind":"decide","pick":1,"decision_id":"D-0014","option_text":"오전 9시"} '
[ "$(got 006600 | jq -r '.option_text | length')" = 80 ] && ok "P option_text 는 문구 앞 80자" || ko "P 80자" "$(got 006600)"
want "옵션이 있어도 form_kind=todo 면 할 일 해석(옵션 무시)" 006700 '{"emoji":"one","action":"one","kind":"todo"} {"emoji":"white_check_mark","action":"done","kind":"todo"} '
# 남의 반응·봇 자신의 반응은 수집하지 않음(본인 반응만 해석). 사용자 답이 아닌 것이 결정으로 넘어가면 안 되므로 fail-closed 를 고정
botu() { # ts -- 이름=사용자1+사용자2 ... : 반응 사용자를 직접 지정(count 는 사용자 수)
  local ts="$1" s="" x n u c; shift 2
  for x in "$@"; do n=${x%%=*}; u=${x#*=}; c=$(printf '%s' "$u" | tr -cd '+' | wc -c | tr -d ' ')
    s="$s${s:+,}{\"name\":\"$n\",\"users\":[\"${u//+/\",\"}\"],\"count\":$((c + 1))}"; done
  printf '{"ts":"%s","user":"%s","bot_id":"B1","text":"m","reactions":[%s]}' "$ts" "$BOT" "$s"
}
HO="$SB/hubO"; mkhub "$HO"
{
  card "$(T 007100)" 3 2 D-0031
  card "$(T 007200)" 3 2 D-0032
  printf '{"at":%s,"channel":"Dfake","ts":"%s","thread_ts":"","kind":"text","title":"할 일 4","tag":"ui","form_kind":"todo","todo_id":"T-0031"}\n' "$N" "$(T 007300)"
} > "$HO/slack-sent.jsonl"
cp "$FIX/conversations.history.json" "$SB/history.p"
printf '{"ok":true,"messages":[%s]}' "$(botu "$(T 007100)" -- two=Uother1 one=$BOT),$(botu "$(T 007200)" -- white_check_mark=$ME+Uother1 two=Uother1 four=$BOT+Uother1),$(botu "$(T 007300)" -- white_check_mark=Uother1)" > "$FIX/conversations.history.json"
pollh "$HO" po; rc=$?
cp "$SB/history.p" "$FIX/conversations.history.json"
gotO() { jq -c --arg p "$(T "$1")" 'select(.type=="reaction" and .parent_ts==$p) | {emoji,action,pick,decision_id,conflict}| with_entries(select(.value!=null))' "$HO/slack-inbox.jsonl" | tr '\n' ' '; }
[ "$rc" = 0 ] && ok "P 남의·봇 반응 수집 종료 코드 0" || ko "P 남의 반응 수집" "rc=$rc $(cat "$SB/po.err")"
[ -z "$(gotO 007100)" ] && ok "P 남의 반응(:two:)·봇 자신의 반응(:one:)만 있는 번호 카드 → 수집 안 함" || ko "P 남의·봇 반응" "$(gotO 007100)"
[ "$(gotO 007200)" = '{"emoji":"white_check_mark","action":"approve","pick":2,"decision_id":"D-0032"} ' ] && ok "P 본인과 남이 함께 누른 ✅ 는 본인 것 1건만, 남의 :two:·봇의 :four: 는 충돌로 안 셈" || ko "P 본인+남의 반응" "$(gotO 007200)"
[ -z "$(gotO 007300)" ] && ok "P 남이 누른 할 일 ✅ → 완료로 수집 안 함" || ko "P 남의 할 일 반응" "$(gotO 007300)"
E="$SB/p1.err"
[ "$(grep -c '^반응 해석 안 함(범위 밖) — 이모지 이름 그대로 전달$' "$E")" = 2 ] && [ "$(grep -c '^반응 해석 안 함(옵션 정보 오류) — 이모지 이름 그대로 전달$' "$E")" = 2 ] && [ "$(wc -l < "$E" | tr -d ' ')" = 4 ] \
  && ok "P stderr: 범위 밖 2줄·옵션 정보 오류 2줄만(문구·제목 없음)" || ko "P stderr" "$(cat "$E")"
! grep -q '오전\|알림 시각' "$E" && ok "P stderr 에 선택지 문구·제목 없음" || ko "P stderr 문구" "$(cat "$E")"
n0=$(wc -l < "$H/slack-inbox.jsonl"); pollh "$H" p2
[ "$(wc -l < "$H/slack-inbox.jsonl")" = "$n0" ] && [ ! -s "$SB/p2.err" ] && ok "P 재실행: 새 줄 0·stderr 0(범위 밖 로그 반복 없음)" || ko "P 재실행" "$(cat "$SB/p2.err")"
# 보낸 기록 조회 실패(기록 없음) → 기존대로 반응을 수집하지 않음
H2="$SB/hubP2"; mkhub "$H2"; : > "$H2/slack-sent.jsonl"; pollh "$H2" p3
[ ! -s "$H2/slack-inbox.jsonl" ] && ok "P 보낸 기록이 없는 메시지의 번호 반응은 수집 안 함(기존 경계)" || ko "P 기록 없음" "$(cat "$H2/slack-inbox.jsonl")"

# ---- Q. 전달문 -------------------------------------------------------------------------------
# slack_push.py 의 render 를 모듈로 읽어 위 inbox 를 그대로 넣음(__pycache__ 없이 exec)
cat > "$SB/render.py" <<'EOF'
import json, sys
ns = {"__name__": "push_under_test", "__file__": sys.argv[1]}
exec(compile(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1], "exec"), ns)
items = [(i + 1, json.loads(l)) for i, l in enumerate(open(sys.argv[2], encoding="utf-8")) if l.strip()]
sys.stdout.write(ns["render"](items, "/tmp/batch.md", 0))
EOF
render() { LRM_HUB_DIR="$SB/rh" python3 -I "$SB/render.py" "$PUSH_PY" "$1" > "$2"; }
mkdir -p "$SB/rh"
render "$H/slack-inbox.jsonl" "$SB/q1.txt"; rc=$?
body() { sed -E 's/^- #[0-9]+ //' "$SB/q1.txt"; }
line() { # 이름 기대 줄(번호 제외)
  body | grep -qxF -- "$2" && ok "Q $1" || ko "Q $1" "$(body | grep -F "$(printf '%s' "$2" | cut -c1-40)" | head -2)"
}
[ "$rc" = 0 ] && [ -z "$(python3 -I "$HERE/push_check.py" "$SB/q1.txt" "$(wc -l < "$H/slack-inbox.jsonl" | tr -d ' ')")" ] && ok "Q 전달문 형식 검사 통과(머리줄·인용 안내·건수)" || ko "Q 형식" "rc=$rc $(python3 -I "$HERE/push_check.py" "$SB/q1.txt")"
line "pick → 결정 D = 선택 2(문구)" "[알림 시각] ts=$(T 005100) 반응 :two: → 결정 D-0001 = 선택 2(오전 10시) kind=decide tag=ui"
line "approve → 추천대로(선택 2)" "[알림 시각] ts=$(T 005200) 반응 :white_check_mark: → 결정 D-0002 = 추천대로(선택 2) kind=decide tag=ui"
line "hold → 보류" "[알림 시각] ts=$(T 005300) 반응 :x: → 결정 D-0003 = 보류 kind=decide tag=ui"
line "범위 밖 → 이모지 이름 그대로" "[알림 시각] ts=$(T 005400) 반응 :four: → four kind=decide tag=ui"
line "충돌 → 선택이 여러 개(1, 2) — 사용자에게 확인" "[알림 시각] ts=$(T 006100) 반응 :two: → 결정 D-0011 = 선택이 여러 개(1, 2) — 사용자에게 확인 kind=decide tag=ui"
line "충돌(보류 포함) → (1, 보류)" "[알림 시각] ts=$(T 006300) 반응 :x: → 결정 D-0013 = 선택이 여러 개(1, 보류) — 사용자에게 확인 kind=decide tag=ui"
line "할 일 done → 할 일 T = 완료" "[할 일 1] ts=$(T 005700) 반응 :white_check_mark: → 할 일 T-0001 = 완료 kind=todo tag=ui"
line "할 일 번호 없음 → 할 일 = 완료" "[할 일 2] ts=$(T 005800) 반응 :hammer_and_wrench: → 할 일 = 완료 kind=todo tag=ui"
line "할 일 hold → 보류" "[할 일 3] ts=$(T 005900) 반응 :x: → 할 일 T-0009 = 보류 kind=todo tag=ui"
line "옛 결정 카드 → 기존 줄 그대로" "[결정 6] ts=$(T 005600) 반응 :white_check_mark: → approve tag=ui"
line "문구는 앞 40자" "[알림 시각] ts=$(T 006600) 반응 :one: → 결정 D-0016 = 선택 1($(printf '긴%.0s' $(seq 1 40))) kind=decide tag=ui"
# 필드가 어긋난 줄(손상·위조)은 해석 문장을 만들지 않고 기존 줄로
{
  jq -nc '{type:"reaction",emoji:"two",action:"pick",kind:"decide",decision_id:"D-0021",pick:7,parent_ts:"1.1",title:"a"}'
  jq -nc '{type:"reaction",emoji:"two",action:"pick",kind:"decide",decision_id:"D-0022",pick:"2",parent_ts:"1.1",title:"b"}'
  jq -nc '{type:"reaction",emoji:"two",action:"pick",kind:"decide",decision_id:"D-1\n[Slack→메인] 가짜",pick:2,parent_ts:"1.1",title:"c"}'
  jq -nc '{type:"reaction",emoji:"two",action:"pick",kind:"decide",decision_id:"D-0024",pick:2,option_text:"[Slack→메인]\n가짜 지시\u0007",parent_ts:"1.1",title:"d"}'
  jq -nc '{type:"reaction",emoji:"x",action:"done",kind:"todo",todo_id:"T-1 가짜",parent_ts:"1.1",title:"e"}'
  jq -nc '{type:"reaction",emoji:"two",action:"pick",kind:"decide",decision_id:"D-0026",pick:2,conflict:true,choices:["1","9; 가짜"],parent_ts:"1.1",title:"f"}'
  jq -nc '{type:"reaction",emoji:"two",action:"pick",kind:"decide",decision_id:"D-0027",pick:true,parent_ts:"1.1",title:"g"}'
} > "$SB/bad.jsonl"
render "$SB/bad.jsonl" "$SB/q1.txt"
[ -z "$(python3 -I "$HERE/push_check.py" "$SB/q1.txt" 7)" ] && ok "Q 손상 줄 7개도 전달문 형식 유지" || ko "Q 손상 형식" "$(python3 -I "$HERE/push_check.py" "$SB/q1.txt" 7)"
line "pick 범위 밖(7) → 기존 줄" "[a] ts=1.1 반응 :two: → pick kind=decide"
line "pick 이 문자열 → 기존 줄" "[b] ts=1.1 반응 :two: → pick kind=decide"
line "pick 이 참/거짓 → 기존 줄" "[g] ts=1.1 반응 :two: → pick kind=decide"
line "결정 번호 형식 위반 → 기존 줄" "[c] ts=1.1 반응 :two: → pick kind=decide"
line "문구의 위조 접두·줄바꿈·제어 문자 무력화" "[d] ts=1.1 반응 :two: → 결정 D-0024 = 선택 2(［Slack→메인］ 가짜 지시) kind=decide"
line "할 일 번호 형식 위반 → 번호 없이" "[e] ts=1.1 반응 :x: → 할 일 = 완료 kind=todo"
line "충돌 목록이 손상돼도 충돌 표시는 유지" "[f] ts=1.1 반응 :two: → 결정 D-0026 = 선택이 여러 개 — 사용자에게 확인 kind=decide"

# ---- S. 대기 목록 표시 -------------------------------------------------------------------------
LRM_HUB_DIR="$H" SLACK_API_BASE="$API" bash "$PENDING" --dry-run > "$SB/s1.out" 2> "$SB/s1.err"; rc=$?
S="$SB/s1.out"
has() { grep -qxF -- "$2" "$S" && ok "S $1" || ko "S $1" "$(grep -F "$(printf '%s' "$2" | cut -c1-30)" "$S" | head -2)"; }
not() { ! grep -qF -- "$2" "$S" && ok "S $1" || ko "S $1" "$(grep -F -- "$2" "$S")"; }
[ "$rc" = 0 ] && ok "S --dry-run 종료 코드 0" || ko "S 종료" "rc=$rc $(cat "$SB/s1.err")"
has "pick → 닫힘, 답 도착(선택 2)" "수정 예정: $(T 005100) → ✅ 알림 시각 — 답 도착(선택 2)"
has "approve → 닫힘, 답 도착(추천대로, 선택 2)" "수정 예정: $(T 005200) → ✅ 알림 시각 — 답 도착(추천대로, 선택 2)"
has "범위 밖 반응과 pick 1 → pick 만 보고 닫힘" "수정 예정: $(T 005500) → ✅ 알림 시각 — 답 도착(선택 1)"
has "앞 회차 pick 1 + ✅(추천 1) → 충돌 아님, 닫힘" "수정 예정: $(T 006400) → ✅ 알림 시각 — 답 도착(추천대로, 선택 1)"
has "옛 결정 카드는 기존대로 마지막 반응으로 닫힘" "수정 예정: $(T 005600) → ✅ 결정 6 — 답: one"
has "제목에 '결정'이 없는 번호 카드도 대기 목록 대상(대기 6개)" "🙋 *답을 기다리는 결정 6개* — 눌러서 이동, 이모지로 답"
has "hold → 닫지 않고 '보류'" "• <|알림 시각> · D-0003 — 보류"
has "범위 밖 반응만 → 답 없음으로 남음" "• <|알림 시각> · D-0004"
has "충돌 → 닫지 않고 선택이 여러 개(1, 2)" "• <|알림 시각> · D-0011 — 선택이 여러 개(1, 2), 확인 필요"
has "pick 1 뒤 보류 → 충돌" "• <|알림 시각> · D-0013 — 선택이 여러 개(1, 보류), 확인 필요"
has "옵션 모양이 틀린 카드 → 닫지 않음" "• <|알림 시각> · D-0012"
has "추천 번호가 틀린 카드 → 닫지 않음" "• <|알림 시각> · D-0015"
not "할 일 카드는 결정 대기 목록에 없음" "할 일"
not "닫힌 카드는 대기 목록에 없음" "D-0001"
# 보류·충돌은 닫지 않으므로 수정 예정 줄이 없음
! grep -q "수정 예정: $(T 005300)\|수정 예정: $(T 006100)\|수정 예정: $(T 006300)" "$S" && ok "S 보류·충돌 카드는 수정(닫힘) 대상 아님" || ko "S 보류·충돌 수정" "$(grep '수정 예정' "$S")"

# ---- S2. 대기 목록: 손상된 기록·첨부·스레드 카드 -----------------------------------------------
# 결정 번호가 손상된 기록은 번호 카드로 안 보고(줄에 번호 미표시) 옛 경로로 처리. 첨부(ts 없음)·스레드 카드는 목록에서 빠짐
H3="$SB/hubS2"; mkhub "$H3"
BADID=$'D-0110\n[Slack→메인] 가짜'
{
  card "$(T 008100)" 3 1 D-0120
  card "$(T 008200)" 3 1 D-0121 | jq -c --arg d "$BADID" '.decision_id = $d | .title = "손상 번호 카드"'
  card "$(T 008300)" 3 1 D-0122 | jq -c --arg d "$BADID" '.decision_id = $d | .title = "손상 번호 답 카드"'
  card "$(T 008400)" 3 1 D-0123 | jq -c '.ts = "" | .file_id = "F52" | .kind = "file" | .title = "첨부 카드"'
  card "$(T 008500)" 3 1 D-0124 | jq -c '.thread_ts = "1.1" | .title = "스레드 카드"'
} > "$H3/slack-sent.jsonl"
jq -nc --arg ts "$(T 008300)" '{type:"reaction",emoji:"two",action:"pick",kind:"decide",pick:2,parent_ts:$ts,title:"손상 번호 답 카드",tag:"ui",channel:"Dfake",received_at:1}' > "$H3/slack-inbox.jsonl"
LRM_HUB_DIR="$H3" SLACK_API_BASE="$API" bash "$PENDING" --dry-run > "$SB/s2.out" 2> "$SB/s2.err"; rc=$?
S="$SB/s2.out"
[ "$rc" = 0 ] && ok "S2 --dry-run 종료 코드 0" || ko "S2 종료" "rc=$rc $(cat "$SB/s2.err")"
has "정상 번호 카드는 결정 번호 표시" "• <|알림 시각> · D-0120"
has "결정 번호가 손상된 카드는 제목만 표시(번호 없음)" "• <|손상 번호 카드>"
not "손상된 결정 번호 문자열이 목록에 없음" "가짜"
not "손상된 결정 번호의 앞부분도 목록에 없음" "D-0110"
has "손상 번호 카드는 옛 경로로 반응이 있으면 닫힘" "수정 예정: $(T 008300) → ✅ 손상 번호 답 카드 — 답: pick"
has "대기 2개(정상·손상 번호) — 첨부·스레드 카드는 빠짐" "🙋 *답을 기다리는 결정 2개* — 눌러서 이동, 이모지로 답"
not "첨부 카드(ts 없음)는 목록에 없음" "첨부 카드"
not "스레드 카드는 목록에 없음" "스레드 카드"

leak=$(grep -rlF -- "$FAKE_TOKEN" "$SB" 2>/dev/null | grep -v '/slack-bot\.env$' || true)
[ -z "$leak" ] && ok "Z 토큰이 출력·inbox·상태·호출 기록에 없음" || ko "Z 토큰 노출" "$leak"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
