#!/usr/bin/env bash
# slack_poll.sh 읽음 확인 반응(👀→✅) 검증. 가짜 Slack API(로컬 HTTP)만 사용하며 실제 Slack·토큰·네트워크는 쓰지 않음.
# 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함.
set -uo pipefail
export LRM_PUSH=off  # 폴러 끝의 메인 전달(push)이 실제 orca 를 부르지 않게 함. push 는 push.sh 가 가짜 orca 로 시험

HERE=$(cd "$(dirname "$0")" && pwd -P)
POLL="$HERE/../../hubs/scripts/slack_poll.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-slackreact.XXXXXX")
SRV=""
trap '[ -n "$SRV" ] && { kill "$SRV"; wait "$SRV"; } 2>/dev/null; rm -rf "${SB:?}"' EXIT
FIX="$SB/fix" CL="$SB/calls.log"
mkdir -p "$FIX"; : > "$CL"

FAKE_TOKEN="fake-token-0123456789-abcdefghij"
ME=Uuser1; BOT=Ubot1
N=$(date +%s)
# 회차당 호출 상한. LRM_REACT_MAX 를 바꿔 돌려도(3 이상) 통과하도록 기대값을 이 값에서 계산함. ✅ 전환은 제거+추가 한 쌍
MAX=${LRM_REACT_MAX:-10}; PAIRS=$((MAX / 2))

mkhub() { # 허브 폴더: 설정 파일과 첨부 전환 시각만 있는 새 허브
  mkdir -p "$1"
  printf 'SLACK_BOT_TOKEN=%s\nSLACK_USER_ID=%s\n' "$FAKE_TOKEN" "$ME" > "$1/slack-bot.env"
  printf '{"file_share_since":%s,"thread_broadcast_since":%s}' $((N - 60)) $((N - 60)) > "$1/slack-poll-state.json"
}
hist() { printf '{"ok":true,"messages":[%s]}' "$1" > "$FIX/conversations.history.json"; }
note() { printf '{"ts":"%s","user":"%s","text":"메모 %s"}' "$1" "$ME" "$1"; }
okjson() { printf '{"ok":true}' > "$FIX/$1.json"; }
errjson() { printf '{"ok":false,"error":"%s"}' "$2" > "$FIX/$1.json"; }
backdate() { jq '.reactions_disabled_at -= 700' "$1/slack-poll-state.json" > "$1/s.tmp" && mv "$1/s.tmp" "$1/slack-poll-state.json"; }

printf '{"ok":true,"channel":{"id":"Dfake"}}' > "$FIX/conversations.open.json"
printf '{"ok":true,"messages":[]}' > "$FIX/conversations.replies.json"

python3 -I "$HERE/fake_slack.py" "$FIX" "$SB/port" "$CL" &
SRV=$!
for _ in $(seq 1 50); do [ -s "$SB/port" ] && break; sleep 0.1; done
[ -s "$SB/port" ] || { ko "가짜 API 기동" "포트 파일 없음"; exit 1; }
API="http://127.0.0.1:$(cat "$SB/port")"

pollh() { # 허브 폴더, 출력 파일 접두사
  # LRM_ACK=off: 수신 확인 답장(chat.postMessage)은 ack.sh 에서 따로 시험함
  LRM_ACK=off LRM_HUB_DIR="$1" SLACK_API_BASE="$API" bash "$POLL" --once > "$SB/$2.out" 2> "$SB/$2.err"
}
rcalls() { awk '/^reactions\./{n++} END{print n+0}' "$CL"; }
ncall() { # op name ts: 그 호출이 기록된 횟수
  local n; n=$(grep -cxF -- "$(printf 'reactions.%s\tchannel=Dfake&timestamp=%s&name=%s' "$1" "$3" "$2")" "$CL" || true); echo "$n"
}
st() { jq -r "$2" "$1/slack-poll-state.json"; }
errlines() { wc -l < "$SB/$1.err" | tr -d ' '; }
settle() { # 허브 폴더, 출력 접두사: 반응 호출이 더 안 나올 때까지(최대 15회차) 반복
  local c i
  for i in $(seq 1 15); do c=$(rcalls); pollh "$1" "$2$i"; [ "$(rcalls)" = "$c" ] && return 0; done
  return 1
}
inbox_n() { wc -l < "$1/slack-inbox.jsonl" | tr -d ' '; }

# ---- 시나리오 A: 일반 수집 -------------------------------------------------------
# 새 메모·명령·스레드 답글 / 봇 메시지·봇 답글 / 내 반응 / 전환 이전 첨부 메시지
H="$SB/hubA"; mkhub "$H"
OLD=$((N - 86400))
hist "$(note "$N.000100"),
 {\"ts\":\"$N.000200\",\"user\":\"$ME\",\"text\":\"승인\"},
 {\"ts\":\"$N.000300\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":1,\"latest_reply\":\"$N.000400\",\"reactions\":[{\"name\":\"white_check_mark\",\"count\":1,\"users\":[\"$ME\"]}]},
 {\"ts\":\"$N.000500\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"봇 일반 메시지\"},
 {\"ts\":\"$OLD.000050\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"지난 첨부\",\"files\":[]}"
cat > "$FIX/conversations.replies.json" <<EOF
{"ok":true,"messages":[
 {"ts":"$N.000300","user":"$BOT","bot_id":"B1","text":"결정 필요"},
 {"ts":"$N.000400","user":"$ME","text":"답글"},
 {"ts":"$N.000450","user":"$BOT","bot_id":"B1","text":"봇 답글"}
]}
EOF
printf '{"ts":"%s.000300","at":%s,"title":"결정 1","tag":"t"}\n' "$N" "$N" > "$H/slack-sent.jsonl"

pollh "$H" a1; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 4 ] && ok "수집 정상(메모·명령·반응·스레드 답글 4건)" || ko "수집" "rc=$rc $(inbox_n "$H")"
[ "$(rcalls)" = 4 ] && [ "$(ncall add eyes "$N.000100")" = 1 ] && [ "$(ncall add eyes "$N.000200")" = 1 ] && [ "$(ncall add eyes "$N.000400")" = 1 ] && [ "$(ncall add eyes "$N.000300")" = 1 ] && ok "ⓐ 새 메모·명령·스레드 답글, 내 반응이 달린 봇 메시지에 eyes 각 1회(총 4회)" || ko "ⓐ 👀" "$(cat "$CL")"
[ "$(st "$H" '.reacted | to_entries | map(.value) | unique | join(",")')" = eyes ] && [ "$(st "$H" '.reacted_on["'"$N.000300"'"].s')" = eyes ] && ok "ⓐ 진행 표에 eyes 기록(메시지 3건 reacted, 반응 1건 reacted_on)" || ko "ⓐ 상태" "$(st "$H" '.reacted') $(st "$H" '.reacted_on')"
[ "$(errlines a1)" = 0 ] && ok "ⓐ 정상 시 stderr 출력 없음" || ko "ⓐ stderr" "$(cat "$SB/a1.err")"
c=$(rcalls); pollh "$H" a2; rc=$?
[ "$rc" = 0 ] && [ "$(rcalls)" = "$c" ] && ok "ⓐ 재실행: 반응 호출 0회(중복 없음)" || ko "ⓐ 재실행" "$(rcalls) vs $c"
# 상태 파일이 비어(손상) 있어도 inbox 에서 seen 이 복원되어 다시 달지 않음
: > "$H/slack-poll-state.json"; c=$(rcalls); pollh "$H" a3; rc=$?
[ "$rc" = 0 ] && [ "$(rcalls)" = "$c" ] && ok "ⓐ 상태 파일 손상 뒤에도 호출 0회(inbox 로 seen 복원)" || ko "ⓐ 상태 손상" "rc=$rc $(rcalls) vs $c"

[ "$(ncall add eyes "$N.000450")" = 0 ] && [ "$(ncall add eyes "$N.000500")" = 0 ] && ok "ⓑ 봇 답글·반응 없는 봇 메시지에는 호출 0" || ko "ⓑ 봇" "$(cat "$CL")"
[ "$(jq -c 'select(.type=="reaction") | .parent_ts' "$H/slack-inbox.jsonl")" = "\"$N.000300\"" ] && ok "ⓑ 내 반응은 reaction 레코드로 기록(반응에는 못 달아 봇 메시지에 확인 표식)" || ko "ⓑ reaction 레코드" ""
[ "$(ncall add eyes "$OLD.000050")" = 0 ] && ! grep -q "$OLD" "$CL" && ok "ⓑ 전환 이전(오래된) 첨부 메시지에는 호출 0" || ko "ⓑ 지난 첨부" ""
! grep -q '^chat\.' "$CL" && ok "텍스트 메시지 전송(chat.*) 호출 없음" || ko "chat 호출" ""

# ---- 시나리오 B: 읽음 처리 → ✅ ----------------------------------------------------
H="$SB/hubB"; mkhub "$H"
hist "$(note "$N.001100"),
 {\"ts\":\"$N.001200\",\"user\":\"$ME\",\"text\":\"승인\"},
 $(note "$N.001300")"
pollh "$H" b1
[ "$(st "$H" '[.reacted[]] | join(",")')" = "eyes,eyes,eyes" ] || ko "ⓒ 준비" "$(st "$H" '.reacted')"
echo 2 > "$H/.main-read-lines"; c=$(rcalls); pollh "$H" b2; rc=$?
exp=$(( 2 * (PAIRS < 2 ? PAIRS : 2) ))
[ "$rc" = 0 ] && [ "$(( $(rcalls) - c ))" = "$exp" ] && ok "ⓒ 읽은 줄 2개 → 첫 회차 호출 ${exp}회(줄마다 eyes 제거 + ✅ 추가 한 쌍, 상한 $MAX)" || ko "ⓒ 호출 수" "rc=$rc $(( $(rcalls) - c )) vs $exp"
settle "$H" bs && ok "ⓒ 상한에 걸려 남은 줄은 다음 회차에 이어짐(끝에는 호출 0회)" || ko "ⓒ 이월" ""
[ "$(ncall remove eyes "$N.001100")" = 1 ] && [ "$(ncall add white_check_mark "$N.001100")" = 1 ] && [ "$(ncall remove eyes "$N.001200")" = 1 ] && [ "$(ncall add white_check_mark "$N.001200")" = 1 ] && ok "ⓒ 읽힌 두 줄: eyes 제거 + white_check_mark 추가" || ko "ⓒ 전환" ""
[ "$(ncall remove eyes "$N.001300")" = 0 ] && [ "$(ncall add white_check_mark "$N.001300")" = 0 ] && ok "ⓒ 아직 안 지난 줄은 그대로" || ko "ⓒ 미읽음" ""
[ "$(st "$H" '[.reacted["'"$N.001100"'"], .reacted["'"$N.001200"'"], .reacted["'"$N.001300"'"]] | join(",")')" = "done,done,eyes" ] && ok "ⓒ 진행 표: done,done,eyes" || ko "ⓒ 상태" "$(st "$H" '.reacted')"
awk -F'\t' '$1=="reactions.remove" && $2 !~ /name=eyes$/ {bad=1} END{exit bad}' "$CL" && ok "ⓒ 제거는 봇이 단 eyes 에만(다른 반응은 건드리지 않음)" || ko "ⓒ 제거 대상" ""
c=$(rcalls); pollh "$H" b3
[ "$(rcalls)" = "$c" ] && ok "ⓒ 같은 표식으로 재실행: 호출 0회" || ko "ⓒ 재실행" ""
echo 99 > "$H/.main-read-lines"; pollh "$H" b4; rc=$?
[ "$rc" = 0 ] && [ "$(st "$H" '[.reacted[]] | unique | join(",")')" = done ] && [ "$(ncall add white_check_mark "$N.001300")" = 1 ] && ok "ⓒ 표식이 줄 수보다 커도 안전, 남은 줄도 ✅" || ko "ⓒ 큰 표식" "rc=$rc"
# 표식 파일이 깨져도 수집·종료 코드 정상
H="$SB/hubB2"; mkhub "$H"; hist "$(note "$N.000600")"; pollh "$H" b5; echo '깨짐' > "$H/.main-read-lines"; c=$(rcalls); pollh "$H" b6; rc=$?
[ "$rc" = 0 ] && [ "$(rcalls)" = "$c" ] && [ "$(st "$H" '.reacted["'"$N.000600"'"]')" = eyes ] && ok "ⓒ 표식이 깨져도 종료 코드 0, 전환 없음" || ko "ⓒ 깨진 표식" "rc=$rc"

# ---- 시나리오 C: scope 없음(missing_scope) ----------------------------------------
H="$SB/hubC"; mkhub "$H"; hist "$(note "$N.000700")"
errjson reactions.add missing_scope
c=$(rcalls); pollh "$H" c1; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 1 ] && ok "ⓓ missing_scope: 종료 코드 0, 수집은 정상" || ko "ⓓ 종료·수집" "rc=$rc"
[ "$(errlines c1)" = 1 ] && grep -q 'missing_scope' "$SB/c1.err" && ok "ⓓ 오류 요약은 stderr 한 줄" || ko "ⓓ stderr" "$(cat "$SB/c1.err")"
[ "$(st "$H" '.reactions_disabled_reason')" = missing_scope ] && [ "$(( $(rcalls) - c ))" = 1 ] && ok "ⓓ 상태에 reactions_disabled_reason 기록, 호출은 1회에서 중단" || ko "ⓓ 상태" "$(st "$H" '.reactions_disabled_reason') $(( $(rcalls) - c ))"
hist "$(note "$N.000700"),$(note "$N.000710")"
c=$(rcalls); pollh "$H" c2; rc=$?
[ "$rc" = 0 ] && [ "$(rcalls)" = "$c" ] && [ "$(errlines c2)" = 0 ] && [ "$(inbox_n "$H")" = 2 ] && ok "ⓓ 10분 안에는 재시도 없이 stderr 도 조용함(새 메시지 수집은 계속)" || ko "ⓓ 10분 내" "rc=$rc 호출 $(( $(rcalls) - c )) stderr $(errlines c2)"
# 10분 경과: 재시도하되 같은 사유는 다시 알리지 않음
backdate "$H"; c=$(rcalls); pollh "$H" c3; rc=$?
[ "$rc" = 0 ] && [ "$(( $(rcalls) - c ))" = 1 ] && [ "$(errlines c3)" = 0 ] && ok "ⓓ 10분 뒤 재시도 1회, 같은 사유는 stderr 반복 안 함" || ko "ⓓ 재시도" "rc=$rc 호출 $(( $(rcalls) - c )) stderr $(errlines c3)"
# scope 추가 뒤: 다음 재시도에서 복구, 대기 중 쌓인 메시지에도 👀
okjson reactions.add; backdate "$H"; c=$(rcalls); pollh "$H" c4; rc=$?
[ "$rc" = 0 ] && [ "$(( $(rcalls) - c ))" = 2 ] && [ "$(st "$H" 'has("reactions_disabled_reason")')" = false ] && [ "$(st "$H" '[.reacted[]] | unique | join(",")')" = eyes ] && ok "ⓓ scope 추가 뒤 복구: 사유 삭제, 쌓인 2건에 👀" || ko "ⓓ 복구" "rc=$rc $(st "$H" '.reacted')"
# 사유가 바뀌면 새로 한 번 알림
H="$SB/hubC2"; mkhub "$H"; hist "$(note "$N.000720")"; errjson reactions.add invalid_auth; pollh "$H" c5
errjson reactions.add missing_scope; backdate "$H"; pollh "$H" c6
[ "$(errlines c5)" = 1 ] && [ "$(errlines c6)" = 1 ] && grep -q 'missing_scope' "$SB/c6.err" && ok "ⓓ 사유가 바뀌면 새 사유로 한 줄 알림" || ko "ⓓ 사유 변경" "$(cat "$SB/c5.err" "$SB/c6.err")"
okjson reactions.add

# ---- 시나리오 D: 회차당 호출 상한 ---------------------------------------------------
H="$SB/hubD"; mkhub "$H"
msgs=""; for i in $(seq 1 12); do msgs="$msgs$([ -n "$msgs" ] && echo ,)$(note "$N.0008$(printf '%02d' "$i")")"; done
hist "$msgs"
start=$(wc -l < "$CL" | tr -d ' ')
exp=$(( MAX < 12 ? MAX : 12 ))
c=$(rcalls); pollh "$H" d1; rc=$?
[ "$rc" = 0 ] && [ "$(( $(rcalls) - c ))" = "$exp" ] && ok "ⓔ 새 메시지 12건: 첫 회차 호출 ${exp}회(상한 $MAX)" || ko "ⓔ 상한" "rc=$rc $(( $(rcalls) - c )) vs $exp"
[ "$(inbox_n "$H")" = 12 ] && ok "ⓔ 상한과 무관하게 12건 모두 수집" || ko "ⓔ 수집" "$(inbox_n "$H")"
settle "$H" ds
[ "$(st "$H" '[.reacted[]] | unique | join(",")')" = eyes ] && [ "$(st "$H" '.reacted | length')" = 12 ] && ok "ⓔ 나머지는 다음 회차로 이월, 12건 모두 eyes 이후 호출 0회" || ko "ⓔ 이월" "$(st "$H" '.reacted | to_entries | map(.value) | join(",")')"
# 읽음 처리도 같은 상한: 12건 전부 읽힘 → 24회가 필요하지만 회차당 한 쌍 단위로 상한까지
echo 12 > "$H/.main-read-lines"; c=$(rcalls); pollh "$H" d4
exp=$(( 2 * (PAIRS < 12 ? PAIRS : 12) ))
[ "$(( $(rcalls) - c ))" = "$exp" ] && ok "ⓔ ✅ 전환도 회차당 상한(한 쌍=2회 단위, ${exp}회)" || ko "ⓔ ✅ 상한" "$(( $(rcalls) - c )) vs $exp"
settle "$H" dt
[ "$(st "$H" '[.reacted[]] | unique | join(",")')" = done ] && ok "ⓔ 몇 회차 뒤 12건 모두 ✅" || ko "ⓔ 이월 완료" "$(st "$H" '.reacted | to_entries | map(.value) | join(",")')"
dup=$(tail -n +$((start + 1)) "$CL" | grep '^reactions\.' | sort | uniq -d | head -1)
[ -z "$dup" ] && ok "ⓔ 같은 메시지·같은 반응의 중복 호출 없음(이 시나리오 전체 로그, 제거 재호출 포함)" || ko "ⓔ 중복" "$dup"

# ---- 시나리오 E: already_reacted / no_reaction / 오류 분류 --------------------------
H="$SB/hubE"; mkhub "$H"
hist "$(note "$N.000900"),$(note "$N.000910"),$(note "$N.000920")"
errjson "reactions.add.$N.000900" already_reacted
errjson "reactions.add.$N.000910" message_not_found
pollh "$H" e1; rc=$?
[ "$rc" = 0 ] && [ "$(errlines e1)" = 0 ] && [ "$(st "$H" '.reacted["'"$N.000900"'"]')" = eyes ] && ok "ⓕ already_reacted 는 성공으로 처리(eyes, stderr 없음)" || ko "ⓕ already_reacted" "rc=$rc $(st "$H" '.reacted')"
[ "$(st "$H" '.reacted["'"$N.000910"'"]')" = skip ] && [ "$(st "$H" '.reacted["'"$N.000920"'"]')" = eyes ] && ok "ⓕ message_not_found 는 그 메시지만 포기(skip), 나머지는 계속" || ko "ⓕ 메시지 단위 오류" "$(st "$H" '.reacted')"
echo 3 > "$H/.main-read-lines"
errjson "reactions.remove.$N.000900" no_reaction
pollh "$H" e2; rc=$?
[ "$rc" = 0 ] && [ "$(st "$H" '.reacted["'"$N.000900"'"]')" = done ] && [ "$(ncall add white_check_mark "$N.000900")" = 1 ] && ok "ⓕ no_reaction(이미 없음)이어도 ✅ 를 추가하고 done" || ko "ⓕ no_reaction" "rc=$rc $(st "$H" '.reacted')"
# 일시 오류: 상태 유지, 다음 회차에 재시도
H="$SB/hubE2"; mkhub "$H"; hist "$(note "$N.000930")"
errjson reactions.add internal_error
pollh "$H" e3; rc=$?
[ "$rc" = 0 ] && [ "$(errlines e3)" = 0 ] && [ "$(st "$H" '.reacted["'"$N.000930"'"]')" = new ] && [ "$(inbox_n "$H")" = 1 ] && ok "일시 오류: 종료 코드 0, 수집 정상, 진행 표는 new 유지" || ko "일시 오류" "rc=$rc $(st "$H" '.reacted')"
okjson reactions.add; pollh "$H" e4
[ "$(st "$H" '.reacted["'"$N.000930"'"]')" = eyes ] && ok "일시 오류 뒤 다음 회차에 👀 재시도" || ko "재시도" ""
# 30분 넘게 묵은 새 메시지에는 달지 않음
H="$SB/hubE3"; mkhub "$H"; hist "$(note "$((N - 3600)).000940")"; c=$(rcalls); pollh "$H" e5
[ "$(rcalls)" = "$c" ] && [ "$(st "$H" '.reacted["'"$((N - 3600)).000940"'"]')" = skip ] && ok "30분 넘게 묵은 새 메시지에는 👀 를 달지 않음" || ko "묵은 메시지" ""

# ---- 시나리오 F: 권한 정지 중 쌓인 묵은 메시지에 소급 ✅ 를 달지 않음 -------------------
# 묵은 new 4건 + 최근 new 1건이 모두 읽힌 상태. 정지 사유는 남아 있음
mkold() { # 허브 폴더, 정지 시각 오프셋(초): 하루 묵은 new 3건과 5분 전 new 1건을 inbox·진행 표에 심고 모두 읽힘 처리
  local h=$1 off=$2 t
  : > "$h/slack-inbox.jsonl"
  for t in "$OLDT.000001" "$OLDT.000002" "$OLDT.000003" "$RECT.000004"; do
    printf '{"type":"note","ts":"%s","text":"메모","channel":"Dfake","received_at":%s}\n' "$t" "$N" >> "$h/slack-inbox.jsonl"
  done
  jq --argjson at "$((N - off))" --arg a "$OLDT.000001" --arg b "$OLDT.000002" --arg c "$OLDT.000003" --arg d "$RECT.000004" \
    '. + {reacted: {($a):"new",($b):"new",($c):"new",($d):"new"}, reactions_disabled_reason:"missing_scope", reactions_disabled_at:$at}' \
    "$h/slack-poll-state.json" > "$h/s.tmp" && mv "$h/s.tmp" "$h/slack-poll-state.json"
  echo "$(wc -l < "$h/slack-inbox.jsonl" | tr -d ' ')" > "$h/.main-read-lines"
}
OLDT=$((N - 86400)); RECT=$((N - 300))
H="$SB/hubF"; mkhub "$H"; mkold "$H" 86400; hist ""
c=$(rcalls); pollh "$H" f1; rc=$?
oldadds=$(( $(ncall add white_check_mark "$OLDT.000001") + $(ncall add white_check_mark "$OLDT.000002") + $(ncall add white_check_mark "$OLDT.000003") ))
[ "$rc" = 0 ] && [ "$oldadds" = 0 ] && ok "Ⓜ1 복구 회차: 하루 묵은 읽힌 new 3건에 white_check_mark 호출 0회" || ko "Ⓜ1 소급" "rc=$rc 묵은 ✅ 호출 $oldadds"
[ "$(ncall add white_check_mark "$RECT.000004")" = 1 ] && [ "$(ncall add eyes "$RECT.000004")" = 0 ] && ok "Ⓜ1 30분 안 읽힌 new 는 정상 ✅(👀 없이 바로)" || ko "Ⓜ1 최근" "$(cat "$CL" | tail -3)"
[ "$(st "$H" '[.reacted["'"$OLDT.000001"'"], .reacted["'"$OLDT.000002"'"], .reacted["'"$OLDT.000003"'"], .reacted["'"$RECT.000004"'"]] | join(",")')" = "skip,skip,skip,done" ] && ok "Ⓜ1 진행 표: 묵은 건 skip, 최근 건 done" || ko "Ⓜ1 상태" "$(st "$H" '.reacted')"
# 정지가 아직 풀리지 않은 회차(10분 안)에도 묵은 new 는 호출 없이 skip 으로 정리됨
H="$SB/hubF2"; mkhub "$H"; mkold "$H" 60; hist ""
c=$(rcalls); pollh "$H" f2; rc=$?
[ "$rc" = 0 ] && [ "$(rcalls)" = "$c" ] && [ "$(st "$H" '[.reacted["'"$OLDT.000001"'"], .reacted["'"$OLDT.000002"'"], .reacted["'"$OLDT.000003"'"], .reacted["'"$RECT.000004"'"]] | join(",")')" = "skip,skip,skip,new" ] && ok "Ⓜ1 정지 중에도 호출 없이 묵은 new 만 skip 정리(최근 건은 new 유지)" || ko "Ⓜ1 정지 중 정리" "rc=$rc $(st "$H" '.reacted')"

# ---- 시나리오 G: 👀 전에 이미 읽힌 new --------------------------------------------
# 정책: 읽힌 new 에는 👀 를 달지 않고 곧바로 ✅ 만 단다(제거 호출 없음)
H="$SB/hubG"; mkhub "$H"; hist "$(note "$N.001500")"; echo 99 > "$H/.main-read-lines"
c=$(rcalls); pollh "$H" g1; rc=$?
[ "$rc" = 0 ] && [ "$(( $(rcalls) - c ))" = 1 ] && [ "$(ncall add eyes "$N.001500")" = 0 ] && [ "$(ncall remove eyes "$N.001500")" = 0 ] && [ "$(ncall add white_check_mark "$N.001500")" = 1 ] && [ "$(st "$H" '.reacted["'"$N.001500"'"]')" = done ] && ok "👀 전에 읽힌 new: eyes·remove 없이 white_check_mark 1회로 done" || ko "읽힌 new" "rc=$rc $(tail -3 "$CL")"

# ---- 시나리오 H: 상한 때문에 제거만 되고 추가가 밀리지 않음 -------------------------------
H="$SB/hubH"; mkhub "$H"; hist "$(note "$N.001600"),$(note "$N.001610")"
LRM_REACT_MAX=3 pollh "$H" h1
echo 2 > "$H/.main-read-lines"; c=$(rcalls); LRM_REACT_MAX=3 pollh "$H" h2; rc=$?
[ "$rc" = 0 ] && [ "$(( $(rcalls) - c ))" = 2 ] && [ "$(ncall remove eyes "$N.001600")" = 1 ] && [ "$(ncall add white_check_mark "$N.001600")" = 1 ] && [ "$(ncall remove eyes "$N.001610")" = 0 ] && ok "ⓛ1 상한 3: 한 쌍(제거+추가)만 하고 둘째 줄은 제거도 시작하지 않음" || ko "ⓛ1 쌍 단위" "rc=$rc $(( $(rcalls) - c )) $(tail -4 "$CL")"
LRM_REACT_MAX=3 pollh "$H" h3
[ "$(ncall remove eyes "$N.001610")" = 1 ] && [ "$(ncall remove eyes "$N.001600")" = 1 ] && [ "$(st "$H" '[.reacted[]] | unique | join(",")')" = done ] && ok "ⓛ1 다음 회차에 이어 완료, 같은 ts 의 reactions.remove 는 각 1회" || ko "ⓛ1 이어서" "$(st "$H" '.reacted')"

# ---- 시나리오 I: 비정상 LRM_REACT_MAX ------------------------------------------------
for v in abc 1.5 -5 99999 ""; do
  H="$SB/hubI$v"; mkhub "$H"; hist "$(note "$N.001700")"
  LRM_REACT_MAX="$v" pollh "$H" "i$v"; rc=$?
  [ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 1 ] && [ "$(errlines "i$v")" = 0 ] && ok "ⓛ2 LRM_REACT_MAX='$v': 종료 코드 0, 수집 정상, stderr 조용" || ko "ⓛ2 값 '$v'" "rc=$rc $(cat "$SB/i$v.err")"
done
[ "$(st "$SB/hubIabc" '.reacted["'"$N.001700"'"]')" = eyes ] && [ "$(st "$SB/hubI-5" '.reacted["'"$N.001700"'"]')" = new ] && ok "ⓛ2 'abc' 는 기본값(👀 달림), 음수는 0(반응 호출 없음)" || ko "ⓛ2 값 해석" "$(st "$SB/hubIabc" '.reacted') $(st "$SB/hubI-5" '.reacted')"

# ---- 시나리오 J: 반응 호출 중 프로세스가 죽어도 inbox 중복 기록 없음 ------------------------
H="$SB/hubJ"; mkhub "$H"; hist "$(note "$N.001800")"
printf '3' > "$FIX/hang.reactions.add"
c=$(rcalls); set -m; pollh "$H" j1 & jp=$!; set +m
for _ in $(seq 1 50); do [ "$(rcalls)" -gt "$c" ] && break; sleep 0.1; done
kill -9 -- "-$jp" 2>/dev/null; wait "$jp" 2>/dev/null
printf '0' > "$FIX/hang.reactions.add"
[ "$(rcalls)" -gt "$c" ] && [ "$(inbox_n "$H")" = 1 ] && [ "$(st "$H" '.reacted["'"$N.001800"'"]')" = new ] && ok "ⓛ4 반응 호출 중 강제 종료: inbox 1건, 진행 표에 new 가 이미 저장됨" || ko "ⓛ4 종료 시점" "inbox $(inbox_n "$H") $(st "$H" '.reacted')"
touch -t 202001010000 "$H/.slack-poll.lock"   # 죽은 실행의 잠금 잔재: 5분이 지난 것으로 보이게 함
pollh "$H" j2; rc=$?
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 1 ] && [ "$(st "$H" '.reacted["'"$N.001800"'"]')" = eyes ] && ok "ⓛ4 재실행: inbox 중복 기록 없음, 👀 는 이어서 달림" || ko "ⓛ4 재실행" "rc=$rc inbox $(inbox_n "$H") $(st "$H" '.reacted')"

# ---- 시나리오 K: 즉시성 경로별 👀(한 회차) ------------------------------------------------
# 경로: 본인 최상위 글·본인 첨부 글·본인 글 스레드 답글·봇 메시지 스레드 답글·결정 카드 명령 답글·'채널에도 보내기' 답글·
#       봇 메시지에 단 반응·봇 첨부(시안) 메시지에 단 반응. 결정 카드는 2시간 전 메시지(반응 확인은 수집 시각 기준이라 묵음 아님)
H="$SB/hubK"; mkhub "$H"; OLDC=$((N - 7200))
hist "$(note "$N.002100"),
 {\"ts\":\"$N.002110\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"내 첨부\",\"files\":[]},
 {\"ts\":\"$N.002120\",\"user\":\"$ME\",\"text\":\"본인 글\",\"reply_count\":1,\"latest_reply\":\"$N.002121\"},
 {\"ts\":\"$OLDC.000100\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 3\",\"reply_count\":3,\"latest_reply\":\"$N.002133\",\"reactions\":[{\"name\":\"white_check_mark\",\"count\":1,\"users\":[\"$ME\"]}]},
 {\"ts\":\"$N.002140\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"subtype\":\"file_share\",\"text\":\"시안\",\"files\":[{\"id\":\"F77\"}],\"reactions\":[{\"name\":\"x\",\"count\":1,\"users\":[\"$ME\"]}]}"
printf '{"ok":true,"messages":[{"ts":"%s.002120","user":"%s","text":"본인 글"},{"ts":"%s.002121","user":"%s","text":"내 글 답글"}]}' "$N" "$ME" "$N" "$ME" > "$FIX/conversations.replies.$N.002120.json"
printf '{"ok":true,"messages":[{"ts":"%s.000100","user":"%s","bot_id":"B1"},{"ts":"%s.002131","user":"%s","text":"카드 답글"},{"ts":"%s.002132","user":"%s","text":"결정 3 A"},{"ts":"%s.002133","user":"%s","subtype":"thread_broadcast","text":"채널에도"}]}' "$OLDC" "$BOT" "$N" "$ME" "$N" "$ME" "$N" "$ME" > "$FIX/conversations.replies.$OLDC.000100.json"
printf '{"ts":"%s.000100","at":%s,"title":"결정 3","tag":"t"}\n{"ts":"","file_id":"F77","kind":"file","at":%s,"title":"시안 1","tag":"ui"}\n' "$OLDC" "$N" "$N" > "$H/slack-sent.jsonl"
c=$(rcalls); pollh "$H" k1; rc=$?
paths="$N.002100 $N.002110 $N.002120 $N.002121 $N.002131 $N.002132 $N.002133 $OLDC.000100 $N.002140"
miss=""; for t in $paths; do [ "$(ncall add eyes "$t")" = 1 ] || miss="$miss $t"; done
[ "$rc" = 0 ] && [ -z "$miss" ] && [ "$(( $(rcalls) - c ))" = 9 ] && ok "ⓚ 9건(8개 경로) 모두 수집 첫 회차에 👀 1회(본인 글 2·첨부·본인 글 답글·카드 답글·명령 답글·채널에도 답글·결정 카드 반응·시안 반응)" || ko "ⓚ 경로별 👀" "rc=$rc 빠짐:$miss $(( $(rcalls) - c ))"
[ "$(jq -r 'select(.ts=="'"$N.002133"'") | .type' "$H/slack-inbox.jsonl")" = thread_reply ] && ok "ⓚ '채널에도 보내기' 답글(thread_broadcast)을 thread_reply 로 수집" || ko "ⓚ thread_broadcast" "$(cat "$H/slack-inbox.jsonl")"
[ "$(jq -r 'select(.ts=="'"$N.002132"'") | .command' "$H/slack-inbox.jsonl")" = decision ] && ok "ⓚ 결정 카드 명령 답글은 command 로 분류된 채 👀" || ko "ⓚ 명령 답글" ""
[ "$(st "$H" '[.reacted_on["'"$OLDC.000100"'"].s, .reacted_on["'"$N.002140"'"].s] | join(",")')" = "eyes,eyes" ] && ok "ⓚ 반응 확인 진행 표(reacted_on): 2시간 전 결정 카드도 eyes" || ko "ⓚ reacted_on" "$(st "$H" '.reacted_on')"

# 반응 줄이 읽히면 👀 제거 + ☑️(ballot_box_with_check). 사용자 ✅ 와 같은 white_check_mark 는 봇 메시지에 달지 않음
L=$(grep -n '"type": "reaction"' "$H/slack-inbox.jsonl" | tail -1 | cut -d: -f1)
echo "$L" > "$H/.main-read-lines"; settle "$H" ks
[ "$(ncall remove eyes "$OLDC.000100")" = 1 ] && [ "$(ncall add ballot_box_with_check "$OLDC.000100")" = 1 ] && [ "$(ncall add white_check_mark "$OLDC.000100")" = 0 ] && [ "$(st "$H" '.reacted_on["'"$OLDC.000100"'"].s')" = done ] && ok "ⓚ 반응 줄이 읽히면 봇 메시지의 👀 → ☑️ (사용자 ✅ 와 구분, white_check_mark 호출 0)" || ko "ⓚ ☑️ 전환" "$(st "$H" '.reacted_on') $(grep "$OLDC" "$CL" | tail -3)"
awk -F'\t' '$1=="reactions.remove" && $2 !~ /name=eyes$/ {bad=1} END{exit bad}' "$CL" && ok "ⓚ 제거는 봇의 eyes 만(사용자가 단 ✅·❌ 는 건드리지 않음)" || ko "ⓚ 제거 대상" ""
# 같은 메시지에 새 반응 → 다시 👀. 새 반응 줄이 안 읽혔으면 앞 반응이 읽혔어도 ☑️ 로 바꾸지 않음
hist "{\"ts\":\"$OLDC.000100\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 3\",\"reply_count\":3,\"latest_reply\":\"$N.002133\",\"reactions\":[{\"name\":\"white_check_mark\",\"count\":1,\"users\":[\"$ME\"]},{\"name\":\"x\",\"count\":1,\"users\":[\"$ME\"]}]}"
pollh "$H" k2; pollh "$H" k3
[ "$(ncall add eyes "$OLDC.000100")" = 2 ] && [ "$(st "$H" '.reacted_on["'"$OLDC.000100"'"].s')" = eyes ] && [ "$(ncall add ballot_box_with_check "$OLDC.000100")" = 1 ] && ok "ⓚ 끝난 메시지에 새 반응: 다시 👀, 새 반응 줄이 읽히기 전엔 ☑️ 로 안 바꿈" || ko "ⓚ 재대기" "$(st "$H" '.reacted_on')"
# 반응 취소(목록에서 사라짐)는 inbox 에 새 줄을 만들지 않음
n0=$(inbox_n "$H"); hist "{\"ts\":\"$OLDC.000100\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 3\",\"reply_count\":3,\"latest_reply\":\"$N.002133\"}"; pollh "$H" k4
[ "$(inbox_n "$H")" = "$n0" ] && ok "ⓚ 반응 취소: inbox 새 줄 없음(철회 안내는 범위 밖)" || ko "ⓚ 취소" "$(inbox_n "$H") vs $n0"

# 30분 넘게 묵은 반응 확인 대기는 호출 없이 skip
H="$SB/hubK2"; mkhub "$H"; hist ""
jq --arg k "$OLDC.000200" --argjson at $((N - 3600)) '. + {reacted_on: {($k): {s:"new", at:$at}}}' "$H/slack-poll-state.json" > "$H/s.tmp" && mv "$H/s.tmp" "$H/slack-poll-state.json"
c=$(rcalls); pollh "$H" k5
[ "$(rcalls)" = "$c" ] && [ "$(st "$H" '.reacted_on["'"$OLDC.000200"'"].s')" = skip ] && ok "ⓚ 수집 30분 넘게 묵은 반응 확인 대기는 호출 없이 skip" || ko "ⓚ 묵음" "$(st "$H" '.reacted_on')"
# 깨진 진행 표 칸은 버리고 수집 계속
jq '. + {reacted_on: {"x": 1, "1.5": {s:"bogus", at:1}, "2.5": "eyes"}}' "$H/slack-poll-state.json" > "$H/s.tmp" && mv "$H/s.tmp" "$H/slack-poll-state.json"
pollh "$H" k6; rc=$?
[ "$rc" = 0 ] && [ "$(st "$H" '.reacted_on | length')" = 0 ] && [ "$(errlines k6)" = 0 ] && ok "ⓚ 형식이 틀린 reacted_on 칸은 조용히 버림(종료 코드 0)" || ko "ⓚ 깨진 표" "rc=$rc $(st "$H" '.reacted_on')"

# 회차 호출 상한은 메시지·반응 확인이 함께 씀
H="$SB/hubK3"; mkhub "$H"
hist "$(note "$N.002200"),$(note "$N.002210"),{\"ts\":\"$N.002220\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 4\",\"reactions\":[{\"name\":\"x\",\"count\":1,\"users\":[\"$ME\"]}]}"
printf '{"ts":"%s.002220","at":%s,"title":"결정 4","tag":"t"}\n' "$N" "$N" > "$H/slack-sent.jsonl"
c=$(rcalls); LRM_REACT_MAX=2 pollh "$H" k7
[ "$(( $(rcalls) - c ))" = 2 ] && [ "$(ncall add eyes "$N.002220")" = 0 ] && [ "$(st "$H" '.reacted_on["'"$N.002220"'"].s')" = new ] && ok "ⓚ 상한 2: 메시지 2건 먼저, 반응 확인은 다음 회차로 이월" || ko "ⓚ 상한 공유" "$(( $(rcalls) - c ))"
LRM_REACT_MAX=2 pollh "$H" k8
[ "$(ncall add eyes "$N.002220")" = 1 ] && ok "ⓚ 다음 회차에 반응 확인 👀" || ko "ⓚ 이월" ""

# reactions:write 없음(missing_scope): 반응·댓글 수집은 계속, stderr 는 사유·scope 이름 한 줄
H="$SB/hubK4"; mkhub "$H"
hist "{\"ts\":\"$N.002300\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 5\",\"reply_count\":1,\"latest_reply\":\"$N.002301\",\"reactions\":[{\"name\":\"white_check_mark\",\"count\":1,\"users\":[\"$ME\"]}]}"
printf '{"ok":true,"messages":[{"ts":"%s.002301","user":"%s","text":"댓글"}]}' "$N" "$ME" > "$FIX/conversations.replies.$N.002300.json"
printf '{"ts":"%s.002300","at":%s,"title":"결정 5","tag":"t"}\n' "$N" "$N" > "$H/slack-sent.jsonl"
errjson reactions.add missing_scope; pollh "$H" k9; rc=$?; okjson reactions.add
[ "$rc" = 0 ] && [ "$(inbox_n "$H")" = 2 ] && [ "$(errlines k9)" = 1 ] && grep -q 'missing_scope.*reactions:write' "$SB/k9.err" && ok "ⓚ missing_scope: 반응·댓글 2건 수집, 종료 코드 0, stderr 한 줄(사유·scope 이름)" || ko "ⓚ missing_scope" "rc=$rc $(inbox_n "$H") $(cat "$SB/k9.err")"
[ "$(st "$H" '.reacted_on["'"$N.002300"'"].s')" = new ] && ok "ⓚ 권한 없음 동안 반응 확인은 new 로 대기(복구 뒤 30분 안이면 👀)" || ko "ⓚ 대기 유지" "$(st "$H" '.reacted_on')"

# ---- 시나리오 R: 메시지 종류별 반응 해석(결정 카드 vs 시안) ------------------------------------
# 같은 이모지가 종류에 따라 다르게 읽힘. 종류: 보낸 기록의 form_kind·kind(text·file 제외) → 없으면 첨부 기록이고 제목에 '결정'이 없으면 review
H="$SB/hubR"; mkhub "$H"
rx_all() { local s="" n; for n in "$@"; do s="$s$([ -n "$s" ] && echo ,){\"name\":\"$n\",\"count\":1,\"users\":[\"$ME\"]}"; done; echo "[$s]"; }
EM="white_check_mark x 비서-a 비서-b 비서-추천대로 tada"
botmsg() { # ts [첨부 file_id]
  local f=""; [ -n "${2:-}" ] && f=",\"subtype\":\"file_share\",\"files\":[{\"id\":\"$2\"}]"
  printf '{"ts":"%s","user":"%s","bot_id":"B1","text":"m"%s,"reactions":%s}' "$1" "$BOT" "$f" "$(rx_all $EM)"
}
hist "$(botmsg "$N.003100"),$(botmsg "$N.003200" F31),$(botmsg "$N.003300" F32),$(botmsg "$N.003400"),$(botmsg "$N.003500"),$(botmsg "$N.003600" F33),$(botmsg "$N.003700" F34),$(botmsg "$N.003900")"
{
  printf '{"ts":"%s.003100","at":%s,"kind":"text","title":"결정 1 알림 시각","tag":"t"}\n' "$N" "$N"            # 결정 카드(글)
  printf '{"ts":"","file_id":"F31","kind":"file","at":%s,"title":"시안 1","tag":"ui"}\n' "$N"                      # 이미지 첨부, 종류 없음 → review 추정
  printf '{"ts":"","file_id":"F32","kind":"file","at":%s,"title":"결정 2 시안 고르기","tag":"ui"}\n' "$N"          # 첨부 결정 카드 → 결정 해석
  printf '{"ts":"%s.003400","at":%s,"kind":"review","title":"시안 2","tag":"ui"}\n' "$N" "$N"                     # 글 + 명시 kind=review
  printf '{"ts":"%s.003500","at":%s,"kind":"text","form_kind":"review","title":"시안 3","tag":"ui"}\n' "$N" "$N"  # 양식 A form_kind=review
  printf '{"ts":"","file_id":"F33","kind":"file","form_kind":"decide","at":%s,"title":"시안 4","tag":"ui"}\n' "$N"  # 첨부 + form_kind=decide → 결정
  printf '{"ts":"","file_id":"F34","kind":"file","form_kind":"done","at":%s,"title":"완료 보고","tag":"ui"}\n' "$N"  # 첨부 + 다른 명시 종류 → 결정 표(기존)
  printf '{"ts":"%s.003900","at":%s,"title":"A/B 골라줘","tag":"t"}\n' "$N" "$N"                                  # 첨부 없는 글 + 종류 키 없음 + 제목에 '결정' 없음 → 결정 해석
} > "$H/slack-sent.jsonl"
pollh "$H" r1; rc=$?
acts() { jq -r --arg p "$1" 'select(.type=="reaction" and .parent_ts==$p) | "\(.emoji)=\(.action)"' "$H/slack-inbox.jsonl" | sort | tr '\n' ' '; }
kinds() { jq -r --arg p "$1" 'select(.parent_ts==$p) | .kind // "-"' "$H/slack-inbox.jsonl" | sort -u | tr '\n' ' '; }
DEC="tada=tada white_check_mark=approve x=hold 비서-a=a 비서-b=b 비서-추천대로=approve "
REV="tada=tada white_check_mark=pass x=fail 비서-a=pick_a 비서-b=pick_b 비서-추천대로=비서-추천대로 "
[ "$rc" = 0 ] && [ "$(acts "$N.003100")" = "$DEC" ] && [ "$(kinds "$N.003100")" = "- " ] && ok "ⓡ 결정 카드(글): ✅=approve ❌=hold 비서-a/b=a/b, 모르는 이모지는 이름 그대로, kind 키 없음" || ko "ⓡ 결정 카드" "rc=$rc $(acts "$N.003100")|$(kinds "$N.003100")"
[ "$(acts "$N.003200")" = "$REV" ] && [ "$(kinds "$N.003200")" = "review " ] && ok "ⓡ 첨부 메시지(종류 없음): review 로 추정, ✅=pass ❌=fail 비서-a/b=pick_a/pick_b, 추천대로는 이름 그대로" || ko "ⓡ 시안 추정" "$(acts "$N.003200")|$(kinds "$N.003200")"
[ "$(acts "$N.003300")" = "$DEC" ] && [ "$(kinds "$N.003300")" = "- " ] && ok "ⓡ 제목에 '결정'이 든 첨부 메시지는 결정 해석 유지(✅=approve)" || ko "ⓡ 첨부 결정 카드" "$(acts "$N.003300")"
[ "$(acts "$N.003400")" = "$REV" ] && [ "$(acts "$N.003500")" = "$REV" ] && [ "$(kinds "$N.003500")" = "review " ] && ok "ⓡ 명시 kind=review·form_kind=review(글 메시지)도 시안 해석" || ko "ⓡ 명시 review" "$(acts "$N.003400")|$(acts "$N.003500")"
[ "$(acts "$N.003600")" = "$DEC" ] && [ "$(acts "$N.003700")" = "$DEC" ] && ok "ⓡ 첨부라도 form_kind 가 decide·done 이면 결정 표(명시값이 추정보다 우선)" || ko "ⓡ 명시 우선" "$(acts "$N.003600")|$(acts "$N.003700")"
[ "$(acts "$N.003900")" = "$DEC" ] && [ "$(kinds "$N.003900")" = "- " ] && ok "ⓡ 첨부 없는 글(종류 키 없음, 제목에 '결정' 없음 \"A/B 골라줘\")은 결정 해석 유지(✅=approve, review 로 오인 안 함)" || ko "ⓡ 첨부 없는 글" "$(acts "$N.003900")|$(kinds "$N.003900")"
# 시안 메시지 스레드 답글에도 kind=review
H="$SB/hubR2"; mkhub "$H"
hist "{\"ts\":\"$N.003800\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"subtype\":\"file_share\",\"files\":[{\"id\":\"F38\"}],\"reply_count\":1,\"latest_reply\":\"$N.003801\"}"
printf '{"ok":true,"messages":[{"ts":"%s.003801","user":"%s","text":"색이 진함"}]}' "$N" "$ME" > "$FIX/conversations.replies.$N.003800.json"
printf '{"ts":"","file_id":"F38","kind":"file","at":%s,"title":"시안 5","tag":"ui"}\n' "$N" > "$H/slack-sent.jsonl"
pollh "$H" r2
[ "$(jq -r 'select(.type=="thread_reply") | "\(.kind) \(.title)"' "$H/slack-inbox.jsonl")" = "review 시안 5" ] && ok "ⓡ 시안 메시지 스레드 답글 레코드에도 kind=review·제목" || ko "ⓡ 답글 kind" "$(cat "$H/slack-inbox.jsonl")"

# ---- 시나리오 T: '채널에도 보내기' 답글 수집 경계(thread_broadcast_since) ----------------------------
# 배포 전에 달린 broadcast 답글("결정 3 A")이 스레드 재조회 때 새 결정 명령으로 재생되면 안 됨. 일반 답글은 기존 동작 유지
CT=$((N - 7200)).000500; OLDB=$((N - 172800)).000900
printf '{"ts":"%s","at":%s,"title":"결정 5","tag":"t"}\n' "$CT" "$N" > "$SB/sent.T"
printf '{"ok":true,"messages":[{"ts":"%s","user":"%s","bot_id":"B1"},{"ts":"%s","user":"%s","subtype":"thread_broadcast","text":"결정 3 A"},{"ts":"%s.004001","user":"%s","text":"새 일반 답글"},{"ts":"%s.004002","user":"%s","subtype":"thread_broadcast","text":"새 broadcast"}]}' "$CT" "$BOT" "$OLDB" "$ME" "$N" "$ME" "$N" "$ME" > "$FIX/conversations.replies.$CT.json"
hist "{\"ts\":\"$CT\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 5\",\"reply_count\":3,\"latest_reply\":\"$N.004002\"}"
tcheck() { # 허브 접두사 설명: 옛 broadcast 는 inbox 에 없고 seen 에만, 새 일반 답글·새 broadcast 는 수집
  local H="$SB/$1" old new
  old=$(jq -r 'select(.ts=="'"$OLDB"'") | .ts' "$H/slack-inbox.jsonl" 2>/dev/null)
  new=$(jq -r 'select(.type=="thread_reply") | .ts' "$H/slack-inbox.jsonl" 2>/dev/null | sort | tr '\n' ' ')
  [ -z "$old" ] && [ "$(jq -r 'select(.command=="decision") | .ts' "$H/slack-inbox.jsonl" 2>/dev/null | wc -l | tr -d ' ')" = 0 ] && [ "$(st "$H" '.seen | has("msg:'"$OLDB"'")')" = true ] \
    && ok "$2: 옛 broadcast 답글은 새 명령으로 기록 안 됨(seen 에만)" || ko "$2 옛 broadcast" "old=$old $(cat "$H/slack-inbox.jsonl" 2>/dev/null)"
  echo "$new" > "$SB/$1.newtr"
}
# T1: 경계가 있는 상태(스레드 마커가 이미 있음) — 옛 broadcast 제외, 새 일반 답글·새 broadcast 수집
H="$SB/hubT1"; mkhub "$H"; cp "$SB/sent.T" "$H/slack-sent.jsonl"
jq --arg k "$CT" '. + {latest: {($k): "1.0"}}' "$H/slack-poll-state.json" > "$H/s.tmp" && mv "$H/s.tmp" "$H/slack-poll-state.json"
pollh "$H" t1
tcheck hubT1 "ⓣ 경계 있음"
[ "$(cat "$SB/hubT1.newtr")" = "$N.004001 $N.004002 " ] && ok "ⓣ 경계 이후의 새 일반 답글·새 broadcast 답글은 수집" || ko "ⓣ 새 답글" "$(cat "$SB/hubT1.newtr")"
# T2: 상태 유실 첫 회차(경계 키 없음) — 옛 broadcast 제외, 경계 키가 지금 시각으로 기록됨
H="$SB/hubT2"; mkhub "$H"; cp "$SB/sent.T" "$H/slack-sent.jsonl"
jq --arg k "$CT" 'del(.thread_broadcast_since) | . + {latest: {($k): "1.0"}}' "$H/slack-poll-state.json" > "$H/s.tmp" && mv "$H/s.tmp" "$H/slack-poll-state.json"
pollh "$H" t2
tcheck hubT2 "ⓣ 상태 유실 첫 회차"
[ "$(st "$H" '(.thread_broadcast_since | type) + ":" + ((.thread_broadcast_since >= '"$N"') | tostring)')" = "number:true" ] && ok "ⓣ 경계 키가 없으면 지금 시각으로 저장" || ko "ⓣ 경계 초기화" "$(st "$H" '.thread_broadcast_since')"
# T3: reacted_on 30일 보관 정리(RX_KEEP) — 31일 전 칸은 정리, 1일 전 칸은 유지
H="$SB/hubT3"; mkhub "$H"; hist ""
jq --argjson o $((N - 31 * 86400)) --argjson r $((N - 86400)) '. + {reacted_on: {"\($o).000100": {s:"done", at:$o}, "\($r).000100": {s:"done", at:$r}}}' "$H/slack-poll-state.json" > "$H/s.tmp" && mv "$H/s.tmp" "$H/slack-poll-state.json"
pollh "$H" t3
[ "$(st "$H" '.reacted_on | keys | map(split(".")[0] | tonumber) | map(. > '"$((N - 2 * 86400))"') | join(",")')" = true ] && ok "ⓣ reacted_on: 31일 전 칸은 정리, 1일 전 칸은 유지(RX_KEEP 30일)" || ko "ⓣ reacted_on 정리" "$(st "$H" '.reacted_on')"

# ---- ⓖ 토큰 비노출 ------------------------------------------------------------------
leak=$(grep -rlF -- "$FAKE_TOKEN" "$SB" 2>/dev/null | grep -v '/slack-bot\.env$' || true)
[ -z "$leak" ] && ok "ⓖ 토큰이 stdout·stderr·상태·inbox·가짜 API 호출 기록에 없음(설정 파일 제외 전체 검색)" || ko "ⓖ 토큰 노출" "$leak"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
