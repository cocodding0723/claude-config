#!/usr/bin/env bash
# slack_notify.sh 양식 A 검사·--kind 검증. 가짜 curl(PATH 앞)과 로컬 주소(SLACK_API_BASE=127.0.0.1)만 쓰며 실제 Slack·토큰·네트워크는 쓰지 않음.
# 임시 폴더(mktemp -d)에서만 읽고 쓰며 종료 시 정리함. NOTIFY_SH=<경로> 로 변형 사본을 시험할 수 있음(변이 확인용).
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
NOTIFY="${NOTIFY_SH:-$HERE/../../hubs/scripts/slack_notify.sh}"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-slacknotify.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
LOG="$SB/log" BIN="$SB/bin"
mkdir -p "$LOG" "$BIN"
cp "$HERE/fake_curl.sh" "$BIN/curl"; chmod 755 "$BIN/curl"
# 가짜 python3: FAKE_PY_FAIL=check|attach|both|opts|card 면 해당 부분 호출(인자 3번째)만 실패, 그 외는 진짜 python3 로 위임
printf '#!/bin/bash\ncase "${FAKE_PY_FAIL:-}:${3:-}" in check:check|attach:attach|both:check|both:attach|opts:opts|card:card) exit 1 ;; esac\nexec "%s" "$@"\n' "$(command -v python3)" > "$BIN/python3"
chmod 755 "$BIN/python3"
: > "$LOG/calls.log"; : > "$LOG/post.json"; : > "$LOG/fail.api"
API=http://127.0.0.1:9
FAKE_TOKEN="fake-token-0123456789-abcdefghij"
unset LRM_FORMAT
export LRM_BTN_WARN=off  # 버튼 없음 경고는 J 에서만 확인(기존 시험이 stderr 0줄을 봄)

HOME_DIR="" CFG=""
newhome() { # 이름: 설정 파일만 있는 새 HOME(로그·sent 기록 분리용)
  HOME_DIR="$SB/home-$1"; CFG="$HOME_DIR/.config/lrm-hubs"
  mkdir -p "$CFG"
  printf 'SLACK_BOT_TOKEN=%s\nSLACK_USER_ID=Uuser1\n' "$FAKE_TOKEN" > "$CFG/slack-bot.env"
}
newhome main

run_n() { # 인자를 slack_notify.sh 에 그대로 전달. 결과: RC, $SB/out, $SB/err, $LOG/post.json
  : > "$LOG/calls.log"; : > "$LOG/post.json"; : > "$LOG/posts.log"
  HOME="$HOME_DIR" PATH="$BIN:$PATH" SLACK_API_BASE="$API" FAKE_CURL_LOG="$LOG" bash "$NOTIFY" "$@" > "$SB/out" 2> "$SB/err"
  RC=$?
}
# 본문 조회. 색 띠 본문(attachments[0].text)이 있으면 그것을, 첫 줄 자동 굵게(*…*)는 풀어서 반환. 없으면 text·initial_comment
text() { jq -r '(.attachments[0].text // .attachments[0].blocks[0].text.text // .text // .initial_comment // empty) | sub("^\\*(?<a>[^*\n]+)\\*(?<b>\n|$)"; "\(.a)\(.b)")' "$LOG/post.json" 2>/dev/null; }
payload() { jq -r "$1" "$LOG/post.json" 2>/dev/null; }
posted() { [ -s "$LOG/post.json" ]; }
nocall() { [ ! -s "$LOG/calls.log" ]; }
errn() { grep -c . "$SB/err" || true; }
lastlog() { tail -n 1 "$CFG/slack-format.log" 2>/dev/null; }
logn() { if [ -f "$CFG/slack-format.log" ]; then wc -l < "$CFG/slack-format.log" | tr -d ' '; else echo 0; fi; }
lastrec() { tail -n 1 "$CFG/slack-sent.jsonl"; }

TODO_G=$'\xf0\x9f\x9b\xa0\xef\xb8\x8f'   # 🛠️ (VS16 포함, bash 3.2 는 \U 이스케이프 미지원)
K40=$(printf '가%.0s' $(seq 1 40)); K41="${K40}가"
ANS=$'\n\n👉 답: 확인'
TABS=$'\t'

bash -n "$NOTIFY" 2>/dev/null && ok "bash -n 통과" || ko "bash -n" "문법 오류"

# ---- A. --kind 6종 이모지 매핑 ---------------------------------------------------------------
for spec in "decide|:비서-선택해주세요:" "todo|$TODO_G" "done|:white_check_mark:" "progress|:비서-진행중:" "warn|:비서-긴급:" "doc|:비서-공유합니다:"; do
  k=${spec%%|*}; e=${spec#*|}
  run_n --kind "$k" $([ "$k" = decide ] && echo --no-doc) "주제$ANS"
  [ "$RC" = 0 ] && [ "$(text | head -1)" = "$e 주제" ] && [ "$(errn)" = 0 ] && ok "A --kind $k → 첫 줄 앞에 '$e' 한 번, 경고 없음" || ko "A --kind $k" "rc=$RC $(text | head -1)"
done
run_n --kind decide --no-doc "🙋 주제$ANS"
[ "$RC" = 0 ] && [ "$(text | head -1)" = ":비서-선택해주세요: 주제" ] && ok "A 첫 줄에 이미 🙋 → 이모지 중복 부착 없음" || ko "A 중복 🙋" "$(text | head -1)"
run_n --kind done ":white_check_mark: 끝"
[ "$(text)" = ":white_check_mark: 끝" ] && ok "A 첫 줄에 이미 :white_check_mark: → 부착 없음(치환 이름도 인식)" || ko "A 치환 이름" "$(text)"
run_n --kind warn "✅ 끝"
[ "$(text)" = ":white_check_mark: 끝" ] && ok "A 종류가 달라도 6종 이모지가 이미 있으면 부착 없음(치환표만 적용, 줄머리 ✅ 는 완료 표시)" || ko "A 다른 종류 이모지" "$(text)"
run_n --kind done $'첫줄\n둘째 줄에만 🙋'
[ "$(text | head -1)" = ":white_check_mark: 첫줄" ] && ok "A 이모지가 둘째 줄에만 있으면 첫 줄에 부착" || ko "A 첫 줄만 판정" "$(text | head -1)"
run_n --kind bogus "x"
[ "$RC" = 2 ] && nocall && ! posted && grep -q '알 수 없는 --kind: bogus' "$SB/err" && ok "A 모르는 --kind → 종료 코드 2, 원인·조치 메시지, 전송·API 호출 0회" || ko "A 모르는 kind" "rc=$RC $(cat "$SB/err")"
run_n --kind
[ "$RC" = 2 ] && nocall && ok "A --kind 값 없음 → 종료 코드 2" || ko "A --kind 값 없음" "rc=$RC"
run_n --kind done "끝" ; r1=$(lastrec)
[ "$(printf '%s' "$r1" | jq -r '.form_kind')" = done ] && [ "$(printf '%s' "$r1" | jq 'has("unchecked")')" = false ] && ok "A sent 기록에 form_kind=done, unchecked 없음" || ko "A sent 기록" "$r1"

# ---- B. 양식 검사 규칙 ------------------------------------------------------------------------
newhome b
run_n --kind done "✅ $K40"
[ "$RC" = 0 ] && [ "$(errn)" = 0 ] && ok "B 첫 줄 40자(이모지 제외) → 통과" || ko "B 40자" "$(cat "$SB/err")"
run_n --kind done "✅ $K41"
[ "$RC" = 0 ] && grep -q '^경고: 첫 줄이 41자(최대 40)' "$SB/err" && ok "B 첫 줄 41자 → 위반(경고)" || ko "B 41자" "$(cat "$SB/err")"
SPACED=$(printf '가 %.0s' $(seq 1 40))
run_n --kind done "✅ $SPACED"
[ "$(errn)" = 0 ] && ok "B 공백은 길이에 안 셈(40자+공백 40개 → 통과)" || ko "B 공백 제외" "$(cat "$SB/err")"
run_n --kind done "🛠️ :비서-a: ${K40} :비서-b:"
[ "$(errn)" = 0 ] && ok "B 이모지(유니코드·:이름:)는 길이에 안 셈" || ko "B 이모지 제외" "$(cat "$SB/err")"
run_n --kind done $'✅ 완료\n\n• 무엇: LRM-542 수정'
[ "$RC" = 0 ] && grep -q '경고: 본문에 티켓 번호 LRM-542 — 맨 아래' "$SB/err" && ok "B 본문의 LRM-n → 위반(원인+조치)" || ko "B LRM" "$(cat "$SB/err")"
run_n --kind done $'✅ 완료\n\n• 무엇: 수정\n\n참고: LRM-542, abc1234'
[ "$(errn)" = 0 ] && ok "B '참고:' 줄 이후의 티켓·커밋 번호는 허용" || ko "B 참고 허용" "$(cat "$SB/err")"
run_n --kind done $'✅ 완료\n\n```\nLRM-1 abc1234def\n```\n• 무엇: `LRM-2 a1b2c3d4` 와 https://x.example/a1b2c3d4e/LRM-3 은 그대로'
[ "$(errn)" = 0 ] && ok "B 코드 블록·인라인 코드·URL 안의 티켓·hex 는 허용" || ko "B 제외 구간" "$(cat "$SB/err")"
run_n --kind done $'✅ 완료\n\n• 무엇: 커밋 abc1234 반영'
grep -q '경고: 본문에 커밋 번호로 보이는 값 abc1234' "$SB/err" && ok "B 숫자·영문이 섞인 7자 hex → 위반" || ko "B hex" "$(cat "$SB/err")"
run_n --kind done $'✅ 완료\n\n• 무엇: ABC1234 반영'
grep -q 'ABC1234' "$SB/err" && ok "B 대문자 hex 도 위반" || ko "B 대문자 hex" "$(cat "$SB/err")"
run_n --kind done $'✅ 완료\n\n• 무엇: deadbeef 와 1234567 과 abcdef 는 단어·숫자라 통과'
[ "$(errn)" = 0 ] && ok "B 영단어형 hex(deadbeef)·순수 숫자 7자리·6자 hex 는 통과" || ko "B 영단어·숫자" "$(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n• 무엇: 정할 것'
grep -q "경고: 결정·할 일인데 '👉 답:' 줄이 없음" "$SB/err" && ok "B decide 에 👉 줄 없음 → 위반" || ko "B 👉 없음" "$(cat "$SB/err")"
run_n --kind todo $'주제\n\n• 무엇: 할 일'
grep -q "👉 답:' 줄이 없음" "$SB/err" && ok "B todo 에 👉 줄 없음 → 위반" || ko "B todo 👉" "$(cat "$SB/err")"
run_n --kind decide --no-doc "주제$ANS"
[ "$(errn)" = 0 ] && ok "B 직접 쓴 👉 줄 → 통과" || ko "B 👉 있음" "$(cat "$SB/err")"
run_n --kind done $'완료\n\n• 무엇: 끝'
[ "$(errn)" = 0 ] && ok "B done 은 👉 줄 불필요" || ko "B done 👉" "$(cat "$SB/err")"
run_n --kind done $'완료\n\n• *무엇*: 끝\n• *왜*: 이유'
[ "$RC" = 0 ] && grep -q '^경고: 굵게가 2곳' "$SB/err" && ! grep -q '양식 위반' "$SB/err" && posted && ok "B 굵게 2곳 → 경고만(위반 건수에 안 셈, 전송)" || ko "B 굵게" "$(cat "$SB/err")"
run_n --kind done $'완료\n\n• *무엇*: 끝\n* 불릿 한 줄\n2 * 3 * 4'
[ "$(errn)" = 0 ] && ok "B 굵게 1곳·불릿(*)·곱셈 기호는 경고 없음" || ko "B 굵게 1곳" "$(cat "$SB/err")"
LRM_FORMAT=strict run_n --kind done $'완료\n\n• *무엇*: 끝\n• *왜*: 이유'
[ "$RC" = 0 ] && posted && ok "B strict 에서도 굵게 경고는 거부하지 않음" || ko "B strict 굵게" "rc=$RC"

# 경계 보강: 앞 경계(Slack ID형)·굵게 뒤 경계·참고 위치·👉 형식·키캡 길이
SID_SHAPE=U'ABC1234567'   # Slack 사용자 ID 모양의 가짜 값(U + 대문자·숫자 10자)
run_n --kind done $'완료\n\n• 무엇: 사용자 '"$SID_SHAPE"$' 확인'
[ "$(errn)" = 0 ] && ok "B 영문 뒤에 붙은 hex 형태(Slack ID 모양 U+영숫자 10자)는 앞 경계 때문에 통과" || ko "B hex 앞 경계" "$(cat "$SB/err")"
run_n --kind done $'완료\n\n• *a*b *c*'
[ "$(errn)" = 0 ] && ok "B 굵게 뒤에 영문이 붙은 *a*b 는 굵게로 안 셈(*c* 1곳만 → 경고 없음)" || ko "B 굵게 뒤 경계" "$(cat "$SB/err")"
run_n --kind done $'완료\n\n*결정*이 *필요*함'
grep -q '^경고: 굵게가 2곳' "$SB/err" && ok "B 굵게 뒤에 한글 조사가 붙어도 굵게로 셈" || ko "B 굵게 조사" "$(cat "$SB/err")"
run_n --kind done $'완료\n\n참고: x\n\n• 무엇: LRM-1\n\n참고: y'
grep -q '본문에 티켓 번호 LRM-1' "$SB/err" && ok "B '참고:' 가 둘이면 마지막 줄부터 제외(앞 참고 뒤 본문의 티켓은 검사)" || ko "B 참고 마지막 일치" "$(cat "$SB/err")"
run_n --kind done $'참고: ✅ 문서\n\n• 무엇: LRM-2'
grep -q '본문에 티켓 번호 LRM-2' "$SB/err" && ok "B 첫 줄이 '참고:' 여도 제외 구간으로 안 봄(본문 티켓 검사)" || ko "B 첫 줄 참고" "$(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n👉 참고 링크'
grep -q "👉 답:' 줄이 없음" "$SB/err" && ok "B '답:' 없는 👉 줄은 답 줄로 안 침" || ko "B 👉 엄격" "$(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n  :point_right: 답: 확인'
[ "$(errn)" = 0 ] && ok "B ':point_right: 답:' 줄(앞 공백 허용)도 답 줄로 침" || ko "B point_right" "$(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n👉  답 : 확인'
[ "$(errn)" = 0 ] && ok "B '👉' 와 '답' 사이·'답' 뒤 공백 허용" || ko "B 👉 공백" "$(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n참고: x\n👉 답: 확인'
grep -q "👉 답:' 줄이 없음" "$SB/err" && ok "B '참고:' 줄 뒤의 👉 답 은 본문이 아니라 답 줄로 안 침" || ko "B 참고 뒤 👉" "$(cat "$SB/err")"
KEYCAP=$'1\xef\xb8\x8f\xe2\x83\xa3'   # 1️⃣ = 숫자 1 + VS16 + U+20E3(Me)
K39=$(printf '가%.0s' $(seq 1 39))
run_n --kind done "$KEYCAP $K39"
[ "$(errn)" = 0 ] && ok "B 키캡(1️⃣)은 1자로 셈(1+가×39=40자 → 통과)" || ko "B 키캡 40" "$(cat "$SB/err")"
run_n --kind done "$KEYCAP ${K39}가"
grep -q '^경고: 첫 줄이 41자(최대 40)' "$SB/err" && ok "B 키캡 포함 41자 → 위반" || ko "B 키캡 41" "$(cat "$SB/err")"

# ---- C. 단계 모드 ----------------------------------------------------------------------------
newhome c
run_n --kind decide --no-doc "✅ $K41"
[ "$RC" = 0 ] && posted && [ "$(sed -n 1p "$SB/err" | cut -c1-3)" = "경고:" ] && [ "$(tail -n 1 "$SB/err")" = "양식 위반 2건(전송함)" ] && [ "$(grep -c '^경고:' "$SB/err")" = 2 ] && ok "C warn(기본): 위반 2건도 전송·종료 코드 0, stderr 경고 줄 + 마지막 줄 '양식 위반 2건(전송함)'" || ko "C warn" "rc=$RC $(cat "$SB/err")"
LRM_FORMAT=strict run_n --kind decide --no-doc "✅ $K41"
[ "$RC" = 2 ] && ! posted && nocall && [ "$(grep -c '^거부:' "$SB/err")" = 2 ] && tail -n 1 "$SB/err" | grep -q '^양식 위반 2건(전송 안 함)' && ok "C strict: 위반 시 미전송·API 호출 0회·종료 코드 2, '거부:' 줄" || ko "C strict" "rc=$RC $(cat "$SB/err")"
LRM_FORMAT=strict run_n --kind done "✅ 깨끗한 완료"
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "C strict: 위반 없으면 그대로 전송" || ko "C strict 정상" "rc=$RC"
LRM_FORMAT=off run_n --kind decide --no-doc "✅ $K41"
n0=$(logn)
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "C off: 위반해도 검사 없이 전송, stderr 없음" || ko "C off" "rc=$RC $(cat "$SB/err")"
LRM_FORMAT=off run_n --kind todo "✅ $K41"
[ "$(logn)" = "$n0" ] && ok "C off: decide 가 아닌 kind 는 검사 로그에 줄을 안 남김" || ko "C off 로그" ""
LRM_FORMAT=bogus run_n --kind done "끝"
[ "$RC" = 2 ] && nocall && grep -q 'LRM_FORMAT 값 오류: bogus' "$SB/err" && ok "C LRM_FORMAT 잘못된 값 + --kind → 종료 코드 2, 원인·조치" || ko "C 잘못된 모드" "rc=$RC"
LRM_FORMAT=bogus run_n "그냥 보냄 $K41"
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "C LRM_FORMAT 값이 이상해도 --kind 없는 호출은 영향 없음" || ko "C 모드 무영향" "rc=$RC"
LRM_FORMAT=strict run_n --no-check --kind decide --no-doc "✅ $K41"
n1=$(logn)
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && [ "$(lastrec | jq -r '.unchecked')" = true ] && [ "$(lastrec | jq -r '.form_kind')" = decide ] && ok "C --no-check: strict 에서도 검사 없이 전송, sent 기록 unchecked:true" || ko "C --no-check" "rc=$RC $(lastrec)"
run_n --no-check --kind todo "✅ $K41"
[ "$(logn)" = "$n1" ] && ok "C --no-check: 검사를 안 했으므로 decide 가 아닌 kind 는 로그 줄 없음" || ko "C --no-check 로그" ""
run_n --no-check "kind 없이도 기록"
[ "$(lastrec | jq -r '.unchecked')" = true ] && ok "C --no-check 만 줘도 unchecked:true 기록" || ko "C --no-check 단독" "$(lastrec)"
newhome pyfail
FAKE_PY_FAIL=check run_n --kind done "깨끗"
[ "$RC" = 0 ] && posted && grep -qx '양식 검사 실패 — 검사 없이 전송' "$SB/err" && [ "$(errn)" = 1 ] && printf '%s\n' "$(lastlog)" | grep -Eq "${TABS}check-fail${TABS}warn" && ok "C python3 검사 실패 + warn → 한 줄 알리고 검사 없이 전송, 종료 코드 0, 로그 check-fail" || ko "C 검사 실패 warn" "rc=$RC $(cat "$SB/err") / $(lastlog)"
FAKE_PY_FAIL=check LRM_FORMAT=strict run_n --kind done "깨끗"
[ "$RC" = 2 ] && ! posted && nocall && grep -q '^양식 검사 실패(전송 안 함)' "$SB/err" && ok "C python3 검사 실패 + strict → 미전송·종료 코드 2·API 호출 0회" || ko "C 검사 실패 strict" "rc=$RC $(cat "$SB/err")"

# ---- D. 집계 로그 ----------------------------------------------------------------------------
newhome d
run_n --kind decide --no-doc --tag ui-hub --title 비밀제목마커 $'✅ 비밀본문마커 LRM-9\n\n• 무엇: 비밀본문마커'
RX="^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{4}${TABS}decide${TABS}ticket,no-answer${TABS}warn${TABS}ui-hub\$"
[ "$(logn)" = 1 ] && printf '%s\n' "$(lastlog)" | grep -Eq "$RX" && ok "D 로그 한 줄: 시각 TAB 종류 TAB 위반 코드 TAB 모드 TAB 태그" || ko "D 로그 형식" "$(lastlog)"
[ "$(stat -f %Lp "$CFG/slack-format.log")" = 600 ] && ok "D 로그 파일 권한 600" || ko "D 권한" "$(stat -f %Lp "$CFG/slack-format.log")"
[ "$(grep -c '비밀본문마커\|비밀제목마커\|LRM-9' "$CFG/slack-format.log" || true)" = 0 ] && ok "D 로그에 본문·제목·번호가 없음" || ko "D 본문 기록" "$(cat "$CFG/slack-format.log")"
run_n --kind done "깨끗"
printf '%s\n' "$(lastlog)" | grep -Eq "${TABS}done${TABS}ok${TABS}warn${TABS}-\$" && [ "$(logn)" = 2 ] && ok "D 위반 없음 → 'ok', 태그 없음 → '-', 호출마다 1줄 추가" || ko "D ok 줄" "$(lastlog)"
LRM_FORMAT=strict run_n --kind todo "주제"
printf '%s\n' "$(lastlog)" | grep -Eq "${TABS}todo${TABS}no-answer${TABS}strict${TABS}-\$" && [ "$(logn)" = 3 ] && ok "D strict 거부도 기록(모드 strict)" || ko "D strict 로그" "$(lastlog)"
run_n --kind done --tag "$(printf 'a\tb\nc')" "깨끗"
[ "$(lastlog | awk -F'\t' '{print NF}')" = 5 ] && ok "D 태그의 탭·줄바꿈은 공백으로 바꿔 5칸 유지" || ko "D 태그 정리" "$(lastlog)"
run_n --kind done --tag $'a\033[31mb' "깨끗"
[ "$(lastlog | cut -f5)" = 'a[31mb' ] && [ "$(lastlog | awk -F'\t' '{print NF}')" = 5 ] && ! LC_ALL=C grep -q $'\033' "$CFG/slack-format.log" && ok "D 태그의 ESC 등 제어 문자는 제거해 기록" || ko "D 태그 제어 문자" "$(lastlog | od -c | head -3)"
chmod 644 "$CFG/slack-format.log"; run_n --kind done "깨끗"
[ "$(stat -f %Lp "$CFG/slack-format.log")" = 600 ] && ok "D 기존 로그 권한이 느슨해도 600 으로 되돌림" || ko "D 권한 복구" ""
newhome dfail; mkdir "$CFG/slack-format.log"
run_n --kind done "깨끗"
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "D 로그 쓰기 실패(경로가 폴더)여도 전송·종료 코드 0·stderr 없음" || ko "D 쓰기 실패" "rc=$RC $(cat "$SB/err")"

# ---- E. --kind 없는 기존 호출 호환 ---------------------------------------------------------------
newhome e
run_n "그냥 알림 $K41 LRM-542 abc1234"
[ "$RC" = 0 ] && [ "$(cat "$SB/out")" = "ok 1700000000.000100" ] && [ "$(errn)" = 0 ] && [ "$(text)" = "그냥 알림 $K41 LRM-542 abc1234" ] && ok "E --kind 없음: 위반이 있어도 stdout/종료 코드/전송 내용 그대로, 경고 없음" || ko "E 무검사" "rc=$RC $(cat "$SB/out") $(cat "$SB/err")"
[ ! -e "$CFG/slack-format.log" ] && ok "E --kind 없음: 검사 로그 파일을 만들지 않음" || ko "E 로그 없음" ""
[ "$(lastrec | jq -c 'keys')" = '["at","channel","kind","tag","thread_ts","title","ts"]' ] && ok "E --kind 없음: sent 레코드 필드 불변(form_kind·unchecked 없음)" || ko "E sent 모양" "$(lastrec)"
run_n "🅰️ 🅱️ ✅ ❌ 🙋 📄 🔄 ⚠️"
[ "$(text)" = ":비서-a: :비서-b: :비서-추천대로: :비서-보류: :비서-선택해주세요: :비서-공유합니다: :비서-진행중: :비서-긴급:" ] && ok "E 기존 치환표 8종 그대로" || ko "E 치환표" "$(text)"
# 기존 호출자 불변 원칙의 명시적 예외: 줄머리 ✅ 는 --kind 가 없어도 완료 표시(:white_check_mark:). slack-sent.jsonl title 도 따라 바뀜
run_n "✅ 답을 기다리는 결정 없음"
[ "$(text)" = ":white_check_mark: 답을 기다리는 결정 없음" ] && [ "$(lastrec | jq -r '.title')" = ":white_check_mark: 답을 기다리는 결정 없음" ] && [ "$(errn)" = 0 ] && ok "E [예외] --kind 없는 줄머리 ✅ → :white_check_mark:, sent title 도 같이 바뀜" || ko "E 줄머리 ✅" "$(text) / $(lastrec)"
run_n "선택: ✅ 추천대로 / 🅰️ ✅"
[ "$(text)" = "선택: :비서-추천대로: 추천대로 / :비서-a: :비서-추천대로:" ] && ok "E 줄 중간 ✅(반응 선택지)는 :비서-추천대로: 유지" || ko "E 줄 중간 ✅" "$(text)"
run_n $'첫줄\n✅ 둘째\n  ✅ 공백\n• ✅ 불릿\n- ✅ 하이픈\n* ✅ 별표'
[ "$(text)" = $'첫줄\n:white_check_mark: 둘째\n  :white_check_mark: 공백\n• :white_check_mark: 불릿\n- :비서-추천대로: 하이픈\n* :비서-추천대로: 별표' ] && ok "E 둘째 줄 머리·공백 뒤·'•' 불릿 뒤 ✅ 는 완료 표시, '-'·'*' 불릿 뒤는 유지" || ko "E 줄머리 변형" "$(text)"
run_n --tag t1 --title 제목X --thread 1700000000.000001 "본문"
[ "$RC" = 0 ] && [ "$(jq -r '.thread_ts' "$LOG/post.json")" = 1700000000.000001 ] && [ "$(lastrec | jq -r '[.tag,.title,.thread_ts,.kind] | join(",")')" = "t1,제목X,1700000000.000001,text" ] && ok "E 옛 인자 조합 --tag --title --thread" || ko "E 옛 인자" "rc=$RC $(lastrec)"
run_n -- "-로 시작하는 본문"
[ "$RC" = 0 ] && [ "$(text)" = "-로 시작하는 본문" ] && ok "E -- 뒤의 본문 처리" || ko "E --" "rc=$RC"
printf 'x' > "$SB/f.txt"
run_n --tag h "첨부 본문" "$SB/f.txt"
[ "$RC" = 0 ] && [ "$(text)" = "첨부 본문" ] && [ "$(lastrec | jq -r '[.kind,.file_id] | join(",")')" = "file,Ffake1" ] && ok "E 파일 첨부 호출 그대로(kind=file, file_id)" || ko "E 첨부" "rc=$RC $(lastrec)"
run_n --kind done "첨부 완료" "$SB/f.txt"
[ "$RC" = 0 ] && [ "$(text)" = ":white_check_mark: 첨부 완료" ] && ok "E 파일 첨부 + --kind: 설명글에 이모지 부착" || ko "E 첨부+kind" "$(text)"
run_n; [ "$RC" = 2 ] && grep -q '^사용:' "$SB/err" && ok "E 인자 없음 → 사용법, 종료 코드 2" || ko "E 인자 없음" "rc=$RC"
run_n --help; [ "$RC" = 0 ] && grep -q -- '--kind' "$SB/out" && ok "E --help → 종료 코드 0, 사용법에 --kind 안내" || ko "E --help" "rc=$RC"
run_n --nope "x"; [ "$RC" = 2 ] && grep -q '알 수 없는 옵션: --nope' "$SB/err" && ok "E 알 수 없는 옵션 → 종료 코드 2" || ko "E 알 수 없는 옵션" "rc=$RC"
HOME="$SB/nohome" PATH="$BIN:$PATH" SLACK_API_BASE="$API" FAKE_CURL_LOG="$LOG" bash "$NOTIFY" "x" > "$SB/out" 2> "$SB/err"; RC=$?
[ "$RC" = 2 ] && grep -q 'slack-bot.env 없음' "$SB/err" && ok "E 설정 파일 없음 → 종료 코드 2" || ko "E 설정 없음" "rc=$RC"
: > "$LOG/calls.log"; : > "$LOG/post.json"
HOME="$HOME_DIR" PATH="$BIN:$PATH" FAKE_CURL_LOG="$LOG" bash "$NOTIFY" "x" > "$SB/out" 2> "$SB/err"
grep -qx 'https://slack.com/api/conversations.open' "$LOG/calls.log" && ! posted && ok "E SLACK_API_BASE 없으면 기본 주소(slack.com)로 가려 하며, 가짜 curl 이 막아 실제 접속 없음" || ko "E 기본 주소" "$(head -1 "$LOG/calls.log")"
printf 'invalid_auth' > "$LOG/fail.api"
run_n "실패 응답"; r_old=$RC; o_old=$(cat "$SB/out")
run_n --kind done "실패 응답"
[ "$r_old" = 1 ] && [ "$RC" = 1 ] && [ "$o_old" = invalid_auth ] && [ "$(cat "$SB/out")" = invalid_auth ] && ok "E API 실패(invalid_auth): 종료 코드 1·출력은 --kind 유무와 같음" || ko "E API 실패" "$r_old $RC"
: > "$LOG/fail.api"

# ---- F. --choices 안내 줄 -----------------------------------------------------------------------
newhome f
BODY=$'주제\n\n• 무엇: 정할 것'
HEAD=":비서-선택해주세요: $BODY"
G2="👉 답: :비서-a: A · :비서-b: B · :비서-추천대로: 추천대로 · :비서-보류: 보류"
G3="👉 답: :비서-a: A · :비서-b: B · :비서-c: C · :비서-추천대로: 추천대로 · :비서-보류: 보류"
G4="👉 답: :비서-a: A · :비서-b: B · :비서-c: C · :비서-d: D · :비서-추천대로: 추천대로 · :비서-보류: 보류"
for n in 2 3 4; do
  eval "g=\$G$n"
  run_n --kind decide --no-doc --choices "$n" "$BODY"
  [ "$RC" = 0 ] && [ "$(text)" = "$HEAD"$'\n\n'"$g" ] && [ "$(errn)" = 0 ] && ok "F --choices $n → 끝에 안내 줄 정확히 일치, 👉 검사 통과(경고 없음)" || ko "F --choices $n" "rc=$RC $(text | tail -1) / $(cat "$SB/err")"
done
run_n --kind decide --no-doc --choices 3 $'주제\n\n• 무엇: 정할 것\n\n참고: 샘플-123'
[ "$(text)" = "$HEAD"$'\n\n'"$G3"$'\n\n참고: 샘플-123' ] && ok "F '참고:' 줄이 있으면 안내 줄을 그 앞에 끼움(참고가 맨 아래 유지)" || ko "F 참고 앞" "$(text)"
run_n --kind decide --no-doc --choices 4 $'주제\n\n👉 답: 직접 쓴 줄'
[ "$(text)" = $':비서-선택해주세요: 주제\n\n👉 답: 직접 쓴 줄' ] && [ "$(text | grep -c 👉)" = 1 ] && ok "F 본문에 👉 줄이 이미 있으면 안내 줄을 붙이지 않음" || ko "F 중복 방지" "$(text)"
run_n --kind decide --no-doc --choices 2 $'주제\n\n```\n👉 코드 안\n```'
[ "$(text | grep -c '^👉 답:')" = 1 ] && ok "F 코드 블록 안의 👉 는 직접 쓴 줄로 치지 않음(안내 줄 부착)" || ko "F 코드 안 👉" "$(text)"
run_n --kind decide --no-doc "주제"; ! text | grep -q '👉' && ok "F --choices 없으면 안내 줄 없음(기존 동작)" || ko "F choices 없음" ""
for bad in 1 5 0 abc ""; do
  run_n --kind decide --no-doc --choices "$bad" "$BODY"
  [ "$RC" = 2 ] && nocall && grep -q '^--choices 범위 오류' "$SB/err" && ok "F --choices '$bad' → 종료 코드 2, 원인·조치, 전송 없음" || ko "F --choices '$bad'" "rc=$RC $(cat "$SB/err")"
done
run_n --kind decide --no-doc --choices
[ "$RC" = 2 ] && nocall && ok "F --choices 값 없음 → 종료 코드 2" || ko "F --choices 값 없음" "rc=$RC"
run_n --kind done --choices 3 "끝"
[ "$RC" = 2 ] && nocall && grep -q '^--choices 는 --kind decide 와만 사용' "$SB/err" && ok "F decide 가 아닌 --kind 와 --choices → 종료 코드 2" || ko "F 다른 kind" "rc=$RC $(cat "$SB/err")"
run_n --choices 3 "끝"
[ "$RC" = 2 ] && nocall && ok "F --kind 없이 --choices → 종료 코드 2" || ko "F kind 없음" "rc=$RC"
LRM_FORMAT=strict run_n --kind decide --no-doc --choices 2 "$BODY"
[ "$RC" = 0 ] && posted && ok "F strict 에서도 --choices 로 👉 줄이 채워지면 통과" || ko "F strict+choices" "rc=$RC $(cat "$SB/err")"
run_n --no-check --kind decide --no-doc --choices 2 "$BODY"
[ "$(text)" = "$HEAD"$'\n\n'"$G2" ] && [ "$(lastrec | jq -r '.unchecked')" = true ] && ok "F --no-check 여도 안내 줄은 붙음" || ko "F no-check+choices" "$(text)"
run_n --kind decide --no-doc --choices 2 "$BODY" "$SB/f.txt"
[ "$(text)" = "$HEAD"$'\n\n'"$G2" ] && ok "F 파일 첨부 설명글에도 안내 줄 부착" || ko "F 첨부+choices" "$(text)"

# 🅲·🅳 치환
C1=$'\xf0\x9f\x85\xb2' D1=$'\xf0\x9f\x85\xb3' VS=$'\xef\xb8\x8f'
#   :비서-c:·:비서-d: 존재가 미확인이라 --kind(--choices 포함) 지정 시에만 치환, --kind 없는 기존 호출은 원문 유지
run_n "$C1 $D1 $C1$VS $D1$VS 🅰️"
[ "$(text)" = "$C1 $D1 $C1$VS $D1$VS :비서-a:" ] && ok "F --kind 없는 🅲·🅳 는 치환하지 않음(🅰️ 기존 치환은 유지)" || ko "F kind 없는 🅲🅳" "$(text)"
run_n --kind progress "🔄 $C1 $D1 $C1$VS $D1$VS 🅰️"
[ "$(text)" = ":비서-진행중: :비서-c: :비서-d: :비서-c: :비서-d: :비서-a:" ] && ok "F --kind 지정 시 🅲·🅳 → :비서-c:·:비서-d: 치환(변형 선택자 유무 모두)" || ko "F kind 🅲🅳 치환" "$(text)"
run_n --kind decide --no-doc --choices 3 $'주제\n\n• 안 '"$C1"
[ "$(text | sed -n 3p)" = "• 안 :비서-c:" ] && ok "F --kind decide --no-doc --choices 본문의 🅲 도 치환" || ko "F choices 🅲" "$(text)"
legacy_cmp() { # 🅲·🅳 규칙 도입 전(402039d) 치환표 8종. --kind 없는 호출은 이 결과와 같아야 함
  printf '%s' "$1" | sed -e 's/🅰️/:비서-a:/g' -e 's/🅱️/:비서-b:/g' -e 's/✅/:비서-추천대로:/g' -e 's/❌/:비서-보류:/g' -e 's/🙋/:비서-선택해주세요:/g' -e 's/📄/:비서-공유합니다:/g' -e 's/🔄/:비서-진행중:/g' -e 's/⚠️/:비서-긴급:/g'
}
same=1
for inp in "🅲 안 C" "$D1$VS 안 D" "선택: ✅ 추천대로 🅲" $'첫줄\n🅲 둘째 ❌' "🅰️ 🅱️ 🅲 🅳 ❌ 🙋 📄 🔄 ⚠️"; do
  run_n "$inp"; [ "$(text)" = "$(legacy_cmp "$inp")" ] || { same=0; ko "F 이전 치환표와 다름" "$inp → $(text)"; }
done
[ "$same" = 1 ] && ok "F --kind 없는 호출은 🅲·🅳 포함 5개 입력에서 이전 치환표(줄머리 ✅ 예외 제외)와 같은 결과" || true
run_n --kind decide --no-doc --choices 2 $'주제\n\n✅ 완료 표시'
[ "$(text | sed -n 3p)" = ":white_check_mark: 완료 표시" ] && [ "$(text | tail -n 1)" = "$G2" ] && ok "F 본문 줄머리 ✅ 는 완료 표시, 안내 줄의 :비서-추천대로: 는 그대로" || ko "F 줄머리 ✅ + 안내 줄" "$(text)"
run_n --kind decide --no-doc --choices 2 $'참고: ✅ 문서\n\n본문'
[ "$(text)" = $'참고: :비서-추천대로: 문서\n\n본문\n\n'"$G2" ] && [ "$(lastrec | jq -r '.title')" = "참고: :비서-추천대로: 문서" ] && ok "F 첫 줄이 '참고:' 면 안내 줄을 끝에 붙임(맨 위 삽입·빈 제목 없음)" || ko "F 첫 줄 참고" "$(text) / $(lastrec | jq -r '.title')"
run_n --kind decide --no-doc --choices 2 $'주제\n\n참고: a\n\n• 무엇: x\n\n참고: b'
[ "$(text)" = $':비서-선택해주세요: 주제\n\n참고: a\n\n• 무엇: x\n\n'"$G2"$'\n\n참고: b' ] && ok "F '참고:' 가 둘이면 마지막 참고 앞에 안내 줄" || ko "F 참고 둘" "$(text)"
run_n --kind decide --no-doc --choices 2 $'주제\n\n👉 참고 링크'
[ "$(text | tail -n 1)" = "$G2" ] && ok "F '답:' 없는 👉 줄이 있어도 안내 줄을 붙임" || ko "F 👉 엄격 부착" "$(text)"
newhome pyfail2
FAKE_PY_FAIL=attach run_n --kind decide --no-doc --choices 2 "$BODY"
[ "$RC" = 0 ] && posted && grep -qx '안내 줄 부착 실패 — 안내 줄 없이 전송' "$SB/err" && [ "$(text)" = "$HEAD" ] && ok "F python3 부착 실패 + warn → 안내 줄 없이 전송, 한 줄 알림, 종료 코드 0" || ko "F 부착 실패 warn" "rc=$RC $(cat "$SB/err") / $(text)"
FAKE_PY_FAIL=attach LRM_FORMAT=strict run_n --kind decide --no-doc --choices 2 "$BODY"
[ "$RC" = 2 ] && ! posted && nocall && grep -q '^안내 줄 부착 실패(전송 안 함)' "$SB/err" && ok "F python3 부착 실패 + strict → 미전송·종료 코드 2·API 호출 0회" || ko "F 부착 실패 strict" "rc=$RC $(cat "$SB/err")"

# --emoji-set std
S2="👉 답: :a: A · :b: B · :white_check_mark: 추천대로 · :x: 보류"
S3="👉 답: :a: A · :b: B · :regional_indicator_c: C · :white_check_mark: 추천대로 · :x: 보류"
S4="👉 답: :a: A · :b: B · :regional_indicator_c: C · :regional_indicator_d: D · :white_check_mark: 추천대로 · :x: 보류"
for n in 2 3 4; do
  eval "g=\$S$n"
  run_n --kind decide --no-doc --choices "$n" --emoji-set std "$BODY"
  [ "$(text)" = "$HEAD"$'\n\n'"$g" ] && ok "F --emoji-set std --choices $n → 표준 이모지 안내 줄 정확히 일치" || ko "F std $n" "$(text | tail -1)"
done
run_n --kind decide --no-doc --choices 2 --emoji-set custom "$BODY"
[ "$(text)" = "$HEAD"$'\n\n'"$G2" ] && ok "F --emoji-set custom 은 기본과 같음" || ko "F custom" "$(text | tail -1)"
run_n --kind decide --no-doc --choices 2 --emoji-set bogus "$BODY"
[ "$RC" = 2 ] && nocall && grep -q '알 수 없는 --emoji-set: bogus' "$SB/err" && ok "F 모르는 --emoji-set → 종료 코드 2" || ko "F 잘못된 emoji-set" "rc=$RC"
run_n --kind decide --no-doc --choices 2 "$BODY"; r_a=$(lastrec | jq -c 'del(.at)')
[ "$(printf '%s' "$r_a" | jq -r '.form_kind')" = decide ] && ok "F --choices 호출도 sent 기록에 form_kind=decide" || ko "F form_kind" "$r_a"

# ---- G. 결정 1건 = 메시지 1개 ------------------------------------------------------------------
newhome g
MULTI_RX='^거부: 결정이 2개임(전송 안 함) — 1건씩 따로 보내세요'
two_ans=$'주제\n\n• 무엇: 가\n\n👉 답: :비서-a: A\n\n• 무엇: 나\n\n👉 답: :비서-b: B'
for mode in warn strict; do
  LRM_FORMAT=$mode run_n --kind decide --no-doc "$two_ans"
  [ "$RC" = 2 ] && ! posted && nocall && grep -q "$MULTI_RX" "$SB/err" && [ "$(lastlog | cut -f3)" = multi ] && ok "G 👉 답 줄 2개 → $mode 에서도 거부(종료 코드 2, 전송·API 호출 0회, 로그 multi)" || ko "G 답 줄 2개 $mode" "rc=$RC $(cat "$SB/err")"
done
run_n --kind decide --no-doc $'주제\n\n결정 1) 시각\n👉 답: 확인 가\n\n결정 2) 장소'
[ "$RC" = 2 ] && ! posted && grep -q "$MULTI_RX" "$SB/err" && ok "G '결정 1)·결정 2)' 제목 줄 2개 → 거부(답 줄은 1개여도)" || ko "G 결정 N)" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n① 결정: 시각\n② 결정: 장소\n\n👉 답: 확인'
[ "$RC" = 2 ] && ! posted && grep -q "$MULTI_RX" "$SB/err" && ok "G '① ②' 번호 결정 제목 줄 → 거부" || ko "G ①②" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n1. 결정 시각\n2. 결정 장소\n\n👉 답: 확인'
[ "$RC" = 2 ] && ! posted && ok "G '1. 2.' 번호 결정 제목 줄 → 거부" || ko "G 1. 2." "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n결정 1) 시각\n결정 2) 장소\n결정 3) 문구\n\n👉 답: 확인'
[ "$RC" = 2 ] && grep -q '^거부: 결정이 3개임' "$SB/err" && ok "G 결정 3개 → 개수를 3으로 안내" || ko "G 3개" "$(cat "$SB/err")"
grep -q '예: ' "$SB/err" && ok "G 거부 메시지에 분할 예시 포함" || ko "G 예시" "$(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n• 무엇: 시각 정하기\n• 선택지: A 9시 / B 10시 / C 11시\n\n👉 답: :비서-a: A · :비서-b: B · :비서-c: C'
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "G 결정 1건 + 선택지 A/B/C 나열 → 통과(오탐 없음)" || ko "G 선택지 오탐" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n• 무엇: 시각 정하기\n1. 현재는 9시\n2. 바꾸면 10시\n3. 영향은 없음\n\n👉 답: 확인'
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "G 결정 낱말 없는 번호 목록(1. 2. 3.) → 통과(오탐 없음)" || ko "G 번호 목록 오탐" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n① A안 9시\n② B안 10시\n\n👉 답: 확인'
[ "$RC" = 0 ] && posted && ok "G ①② 선택지 나열 → 통과(오탐 없음)" || ko "G ①② 선택지" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n```\n👉 답: 가\n👉 답: 나\n```\n• 무엇: x\n\n👉 답: 확인'
[ "$RC" = 0 ] && posted && ok "G 코드 블록 안의 답 줄 2개는 세지 않음" || ko "G 코드 블록" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n👉 답: 확인\n\n참고: 결정 1) 결정 2) LRM-1 https://x.example/결정 3'
[ "$RC" = 0 ] && posted && ok "G '참고:' 줄 자체의 결정 번호·링크는 세지 않음" || ko "G 참고 줄 자체" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc $'주제\n\n👉 답: 확인\n\n참고: 티켓\n결정 2) 장소\n👉 답: 가'
[ "$RC" = 2 ] && ! posted && grep -q "$MULTI_RX" "$SB/err" && ok "G [의도 변경] '참고:' 줄 뒤의 둘째 결정은 셈 → 거부" || ko "G 참고 뒤 둘째" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc --choices 3 $'주제\n\n• 무엇: 정할 것'
[ "$RC" = 0 ] && posted && ok "G --choices 안내 줄 1개는 통과(부착 줄은 개수에 안 셈)" || ko "G choices" "rc=$RC $(cat "$SB/err")"
# G2. 오탐 방지(정상 카드는 통과) / 누락 방지(변형 표기는 거부). 입력은 모두 가짜 내용
pass_case() { # 이름, 본문
  run_n --kind decide --no-doc "$2"
  [ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "G2 통과: $1" || ko "G2 오탐 $1" "rc=$RC $(cat "$SB/err")"
}
rej_case() { # 이름, 본문
  run_n --kind decide --no-doc "$2"
  [ "$RC" = 2 ] && ! posted && nocall && grep -q "$MULTI_RX" "$SB/err" && ok "G2 거부: $1" || ko "G2 누락 $1" "rc=$RC $(cat "$SB/err")"
}
pass_case "선택지 '1. 기존 결정 유지 / 2. 결정 철회'" $'주제\n\n• 선택지: 1. 기존 결정 유지 / 2. 결정 철회\n\n👉 답: 확인'
pass_case "선택지 '① 기존 결정 유지 / ② 결정 변경'" $'주제\n\n• 선택지: ① 기존 결정 유지 / ② 결정 변경\n\n👉 답: 확인'
pass_case "선택지 줄 나눔 '1. 기존 결정 유지' 줄 + '2. 결정 철회' 줄" $'주제\n\n1. 기존 결정 유지\n2. 결정 철회\n\n👉 답: 확인'
pass_case "선택지 줄 나눔 '① 기존 결정 유지' 줄 + '② 결정 변경' 줄" $'주제\n\n① 기존 결정 유지\n② 결정 변경\n\n👉 답: 확인'
pass_case "배경 '1. 이미 결정된 것 / 2. 미결정'" $'주제\n\n1. 이미 결정된 것\n2. 미결정\n\n👉 답: 확인'
pass_case "날짜 '결정 2026-11-01:' 두 줄" $'주제\n\n• 결정 2026-11-01: 가\n• 결정 2026-11-02: 나\n\n👉 답: 확인'
pass_case "수량 '결정 100건'·'결정 2026'" $'주제\n\n• 무엇: 결정 100건 대기\n• 왜: 결정 2026 년 기준\n\n👉 답: 확인'
rej_case "'• *결정 1*' 불릿+굵게" $'주제\n\n• *결정 1*\n• *결정 2*\n\n👉 답: 확인'
rej_case "'결정 ①·결정 ②'" $'주제\n\n결정 ① 시각\n결정 ② 장소\n\n👉 답: 확인'
rej_case "'결정-1·결정-2'" $'주제\n\n결정-1 시각\n결정-2 장소\n\n👉 답: 확인'
rej_case "'결정 #1·결정 #2'" $'주제\n\n결정 #1 시각\n결정 #2 장소\n\n👉 답: 확인'
rej_case "'(1) 결정·(2) 결정'" $'주제\n\n(1) 결정 시각\n(2) 결정 장소\n\n👉 답: 확인'
rej_case "키캡 '1️⃣ 결정·2️⃣ 결정'" $'주제\n\n1\xef\xb8\x8f\xe2\x83\xa3 결정 시각\n2\xef\xb8\x8f\xe2\x83\xa3 결정 장소\n\n👉 답: 확인'
rej_case "'**결정 1**·**결정 2**'" $'주제\n\n**결정 1**\n**결정 2**\n\n👉 답: 확인'
rej_case "인용 '> 결정 1)·> 결정 2)'" $'주제\n\n> 결정 1) 시각\n> 결정 2) 장소\n\n👉 답: 확인'
rej_case "'· 결정 1·· 결정 2'" $'주제\n\n· 결정 1 시각\n· 결정 2 장소\n\n👉 답: 확인'
# [의도 변경] 줄 중간 '결정 N' 은 불확실한 신호라 거부 대신 경고(warn·strict 모두 전송)
warn_case() { # 이름, 본문 — 전송하되 mid-decide 경고 한 줄
  for mode in warn strict; do
    LRM_FORMAT=$mode run_n --kind decide --no-doc "$2"
    [ "$RC" = 0 ] && posted && grep -q '^경고: 줄 중간에 다른 번호의' "$SB/err" && ! grep -q '^거부:' "$SB/err" && ok "G2 경고만($mode): $1" || ko "G2 경고 $mode $1" "rc=$RC $(cat "$SB/err")"
  done
}
warn_case "한 줄 '결정 1) … 결정 2) …'" $'주제\n\n결정 1) 시각 정하기 결정 2) 장소 정하기\n\n👉 답: 확인'
warn_case "정상 1건 + 본문 '지난주 결정 3) 과 충돌'" $'주제\n\n결정 1) 알림 시각\n• 왜: 지난주 결정 3) 과 충돌\n\n👉 답: 확인'
pass_case "같은 번호 반복 '결정 1) 시각' + '• 무엇: 결정 1: 9시'" $'주제\n\n결정 1) 시각\n• 무엇: 결정 1: 9시\n\n👉 답: 확인'
pass_case "같은 번호 머리 반복 '결정 1)' + '1. 결정 시각'" $'주제\n\n결정 1) 시각\n1. 결정 시각 상세\n\n👉 답: 확인'
rej_case "줄 머리 서로 다른 번호 + 줄 중간 참조" $'주제\n\n결정 1) 시각\n결정 2) 장소 — 지난주 결정 3) 참고\n\n👉 답: 확인'
rej_case "'참고:' 줄 뒤 둘째 '👉 답:' 줄" $'주제\n\n결정 1) 시각\n👉 답: 가\n\n참고: 티켓\n👉 답: 나'
# [의도 변경] 참고 블록 안의 번호 머리·불릿 결정 참조는 세지 않음
pass_case "참고 블록 '- 결정 1)'·'- 결정 2)' 문서 목록" $'주제\n\n결정 1) 시각\n\n👉 답: 확인\n\n참고:\n- 결정 1) 지난 회의 문서\n- 결정 2) 지난 회의 문서'
pass_case "참고 블록 '• 결정 1: 회의록'·'• 결정 2: 회의록'" $'주제\n\n결정 1) 시각\n\n👉 답: 확인\n\n참고:\n• 결정 1: 회의록\n• 결정 2: 회의록'
pass_case "표 안 예시 '| 결정 1) | 결정 2) |'" $'주제\n\n| 결정 1) | 결정 2) |\n| a | b |\n\n👉 답: 확인'
rej_case "'결정 1번:·결정 2번:'" $'주제\n\n결정 1번: 시각\n결정 2번: 장소\n\n👉 답: 확인'
rej_case "'1번 결정:·2번 결정:'" $'주제\n\n1번 결정: 시각\n2번 결정: 장소\n\n👉 답: 확인'
rej_case "머리글 '### 결정 1·### 결정 2'" $'주제\n\n### 결정 1\n### 결정 2\n\n👉 답: 확인'
rej_case "대괄호 '[결정 1]·[결정 2]'" $'주제\n\n[결정 1] 시각\n[결정 2] 장소\n\n👉 답: 확인'
pass_case "줄 머리 '결정 2026 년 기준'·'결정 2027 년 기준'(자릿수 제한)" $'주제\n\n결정 2026 년 기준\n결정 2027 년 기준\n\n👉 답: 확인'
# NFD(조합형) 한글도 NFC 로 맞춰 같은 결과
nfd=$(printf '주제\n\n결정 1) 시각\n결정 2) 장소\n\n👉 답: 확인' | python3 -I -c 'import sys,unicodedata;sys.stdout.write(unicodedata.normalize("NFD",sys.stdin.read()))')
rej_case "조합형(NFD) 한글 '결정 1)·결정 2)'" "$nfd"
# 정규식 시간 상한: 4만 자 한 줄(제곱 증가 회귀 방지)
timed_case() { # 이름, 접두 문자열
  body="$(python3 -I -c 'import sys;sys.stdout.write("주제\n\n"+sys.argv[1]*(40000//len(sys.argv[1]))+"\n\n👉 답: 확인")' "$2")"
  t0=$(python3 -I -c 'import time;print(time.time())')
  run_n --kind decide --no-doc "$body"
  t1=$(python3 -I -c 'import time;print(time.time())')
  python3 -I -c 'import sys;sys.exit(0 if float(sys.argv[2])-float(sys.argv[1])<1.0 else 1)' "$t0" "$t1" && ok "G2 시간 상한(1초 미만): 4만 자 '$1' 줄" || ko "G2 시간 $1" "$t0 $t1"
}
timed_case '*' '*'
timed_case '**' '**'
timed_case '> ' '> '
timed_case '[' '['
# 접두 불릿 삭제 변이(m5) 검출용
rej_case "'• 결정 1 / • 결정 2' 불릿" $'주제\n\n• 결정 1 시각\n• 결정 2 장소\n\n👉 답: 확인'
# --choices 시 검사 생략 변이(m9) 검출용
run_n --kind decide --no-doc --choices 2 $'주제\n\n• 결정 1 시각\n• 결정 2 장소'
[ "$RC" = 2 ] && ! posted && nocall && grep -q "$MULTI_RX" "$SB/err" && ok "G2 거부: --choices 2 + 결정 2개(안내 줄 부착 전에 검사)" || ko "G2 choices+다중" "rc=$RC $(cat "$SB/err")"
# 거부 요약이 다른 위반 뒤 마지막 줄에 보임
run_n --kind decide --no-doc $'주제 LRM-123\n\n• *가* *나*\n• 결정 1 시각\n• 결정 2 장소\n\n👉 답: 확인'
[ "$RC" = 2 ] && grep -q '^경고:' "$SB/err" && tail -n 1 "$SB/err" | grep -q "$MULTI_RX" && ok "G2 warn 에서 다른 위반과 겹쳐도 거부 문구가 마지막 줄" || ko "G2 거부 마지막 줄" "$(cat "$SB/err")"
# python3 검사 실패: decide 는 미전송, 다른 kind 는 기존대로
n3=$(logn)
FAKE_PY_FAIL=check run_n --kind decide --no-doc "주제$ANS"
[ "$RC" = 2 ] && ! posted && nocall && grep -q '^양식 검사 실패(전송 안 함)' "$SB/err" && [ "$(logn)" = $((n3 + 1))  ] && printf '%s\n' "$(lastlog)" | grep -Eq "${TABS}decide${TABS}check-fail${TABS}warn" && ok "G2 python3 검사 실패 + decide + warn → 미전송·종료 코드 2·로그 check-fail" || ko "G2 decide 검사 실패" "rc=$RC $(cat "$SB/err")"
FAKE_PY_FAIL=check run_n --no-check --kind decide --no-doc "주제$ANS"
[ "$RC" = 0 ] && posted && ok "G2 --no-check 우회는 검사 실패와 무관하게 유지" || ko "G2 no-check" "rc=$RC"
FAKE_PY_FAIL=check LRM_FORMAT=off run_n --kind decide --no-doc "주제$ANS"
[ "$RC" = 0 ] && posted && ok "G2 LRM_FORMAT=off 우회 유지" || ko "G2 off" "rc=$RC"
FAKE_PY_FAIL=check run_n --kind todo "주제$ANS"
[ "$RC" = 0 ] && posted && grep -qx '양식 검사 실패 — 검사 없이 전송' "$SB/err" && ok "G2 python3 검사 실패 + todo + warn → 기존대로 전송" || ko "G2 todo 검사 실패" "rc=$RC $(cat "$SB/err")"
n2=$(logn)
run_n --no-check --kind decide --no-doc "$two_ans"
[ "$RC" = 0 ] && posted && [ "$(logn)" = $((n2 + 1)) ] && printf '%s\n' "$(lastlog)" | grep -Eq "${TABS}decide${TABS}bypass-no-check${TABS}no-check${TABS}-\$" && ok "G --no-check 우회 → 전송하고 로그 한 줄(bypass-no-check)" || ko "G no-check 우회" "rc=$RC $(lastlog)"
LRM_FORMAT=off run_n --kind decide --no-doc --tag t9 "$two_ans"
[ "$RC" = 0 ] && posted && printf '%s\n' "$(lastlog)" | grep -Eq "${TABS}decide${TABS}bypass-off${TABS}off${TABS}t9\$" && ok "G LRM_FORMAT=off 우회 → 전송하고 로그 한 줄(bypass-off)" || ko "G off 우회" "rc=$RC $(lastlog)"
[ "$(grep -c '비서-a\|결정 1' "$CFG/slack-format.log" || true)" = 0 ] && ok "G 우회 로그에 본문 없음" || ko "G 우회 로그 본문" "$(cat "$CFG/slack-format.log")"
for k in todo done progress warn doc; do
  run_n --kind "$k" "$two_ans"
  [ "$RC" = 0 ] && posted && ! grep -q '결정이' "$SB/err" && ok "G --kind $k 는 답 줄 2개여도 영향 없음(전송)" || ko "G kind $k" "rc=$RC $(cat "$SB/err")"
done
run_n "$two_ans"
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "G --kind 없는 호출은 영향 없음" || ko "G kind 없음" "rc=$RC"

# ---- H. 번호 이모지 결정 카드(--options) ·할 일 번호(--todo-id) ------------------------------------
newhome h
OK3='1=오전 9시,2=오전 10시,3=지금처럼'
CBODY=$'알림 시각\n\n- 왜: 제각각'
CARD=$':비서-선택해주세요: 알림 시각\n\n- 왜: 제각각\n\n:one: 오전 9시\n:two: 오전 10시\n:three: 지금처럼\n:star: 추천: :two: · 되돌리기: 쉬움\n👉 답: :one: :two: :three: 중 하나 · :white_check_mark: 추천대로 · :x: 보류\n\n참고: 결정 D-0001 · 3/9'
run_n --kind decide --no-doc --options "$OK3" --recommend 2 --reversible 쉬움 --decision-id D-0001 --seq 3/9 "$CBODY"
[ "$RC" = 0 ] && [ "$(text)" = "$CARD" ] && [ "$(errn)" = 0 ] && ok "H 카드: 선택지·추천·되돌리기·답 줄·결정 번호 줄을 도구가 생성(정확히 일치, 경고 없음)" || ko "H 카드 모양" "rc=$RC $(text) / $(cat "$SB/err")"
r=$(lastrec)
[ "$(printf '%s' "$r" | jq -c '[.form_kind,.decision_id,.options,.recommended,.reversible,.seq]')" = '["decide","D-0001",{"1":"오전 9시","2":"오전 10시","3":"지금처럼"},2,"쉬움","3/9"]' ] && [ "$(printf '%s' "$r" | jq -r '.title')" = ":비서-선택해주세요: 알림 시각" ] \
  && ok "H sent 기록: decision_id·options(번호→문구)·recommended(숫자)·reversible·seq" || ko "H sent 기록" "$r"
run_n --kind decide --no-doc --options '1=가 안,2=나 안' --recommend 1 --decision-id D-0002 $'주제\n\n- 왜: 배경\n\n참고: 샘플-1'
[ "$(text)" = $':비서-선택해주세요: 주제\n\n- 왜: 배경\n\n:one: 가 안\n:two: 나 안\n:star: 추천: :one:\n👉 답: :one: :two: 중 하나 · :white_check_mark: 추천대로 · :x: 보류\n\n참고: 샘플-1\n결정 D-0002' ] \
  && [ "$(lastrec | jq -c 'has("reversible"), has("seq")' | tr '\n' ,)" = "false,false," ] && ok "H '참고:' 가 있으면 선택지는 그 앞, 결정 번호는 참고 블록 끝. 되돌리기·순번 없으면 줄·필드 없음" || ko "H 참고 앞" "$(text) / $(lastrec)"
run_n --kind decide --no-doc --options '1=가,2=나,3=다,4=라' --recommend 4 --decision-id D-0003 "$CBODY"
[ "$(payload '[.attachments[0].blocks[1].elements[].action_id] | join(",")')" = "pick_1,pick_2,pick_3,pick_4,custom" ] \
  && [ "$(payload '.attachments[0].blocks[1].elements[3].style')" = primary ] && [ "$(payload '.attachments[0].blocks[1].elements[0].style')" = null ] \
  && [ "$(payload '.attachments[0].blocks[1].block_id')" = "lrm_decide:D-0003" ] && [ "$(payload '.attachments[0].blocks[1].elements[1].value')" = "D-0003|2" ] \
  && ok "Z 번호 카드 → 버튼 4개(추천 primary)·직접 입력·결정 번호 값" || ko "Z 버튼" "$(payload '.attachments[0].blocks[1]' | head -c 300)"
[ "$RC" = 0 ] && text | grep -qx ':four: 라' && text | grep -qxF '👉 답: :one: :two: :three: :four: 중 하나 · :white_check_mark: 추천대로 · :x: 보류' && text | grep -qx ':star: 추천: :four:' && ok "H 선택지 4개·추천 4" || ko "H 4개" "rc=$RC $(text)"
run_n --kind decide --no-doc --options '1=9시, 10시 사이,2=b' --recommend 1 --decision-id D-0004 "$CBODY"
[ "$(lastrec | jq -r '.options["1"]')" = "9시, 10시 사이" ] && ok "H 문구 안 쉼표(뒤에 '숫자=' 없음)는 문구로 유지" || ko "H 쉼표" "$(lastrec)"
run_n --kind decide --no-doc --options ' 1 = 가 , 2=나 ' --recommend 1 --decision-id D-0005 "$CBODY"
[ "$(lastrec | jq -c '.options')" = '{"1":"가","2":"나"}' ] && ok "H 번호·문구 앞뒤 공백 정리" || ko "H 공백" "$(lastrec)"
# 선택지 문구의 '결정'은 결정 개수에 안 셈(도구가 붙인 줄은 검사 뒤 부착) / 본문의 결정 2개는 그대로 거부
for mode in warn strict; do
  LRM_FORMAT=$mode run_n --kind decide --no-doc --options '1=결정 유지,2=결정 철회' --recommend 1 --decision-id "D-0006$mode" "$CBODY"  # 모드마다 다른 번호(같은 번호 재사용은 경고)
  [ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "H 문구에 '결정'(1=결정 유지,2=결정 철회) → $mode 에서도 결정 1개로 통과, no-answer 없음" || ko "H 결정 문구 $mode" "rc=$RC $(cat "$SB/err")"
done
run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0007 $'주제\n\n결정 1) 시각\n결정 2) 장소'
[ "$RC" = 2 ] && ! posted && nocall && grep -q "$MULTI_RX" "$SB/err" && ok "H --options 여도 본문 결정 2개는 거부(결정 1건 = 메시지 1개 유지)" || ko "H 다중 유지" "rc=$RC $(cat "$SB/err")"
# 본문에 답 줄을 직접 쓰면 답 줄이 둘 → 모드·우회와 무관하게 거부
for v in "" "--no-check" "off"; do
  if [ "$v" = off ]; then LRM_FORMAT=off run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0008 "$CBODY$ANS"
  elif [ -n "$v" ]; then run_n "$v" --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0008 "$CBODY$ANS"
  else run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0008 "$CBODY$ANS"; fi
  [ "$RC" = 2 ] && ! posted && nocall && grep -q "^거부: --options 카드 본문에 '👉 답:' 줄이 있음" "$SB/err" && ok "H 본문 '👉 답:' + --options → 거부(${v:-warn})" || ko "H 답 줄 중복 ${v:-warn}" "rc=$RC $(cat "$SB/err")"
done
run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0009 $'주제\n\n:one: 직접 쓴 선택지'
[ "$RC" = 0 ] && posted && grep -q '^경고: 본문 줄 머리에 선택지 이모지' "$SB/err" && [ "$(text | grep -c '^:one:')" = 2 ] && ok "H 본문에 선택지 줄(:one:) → 경고만, 전송" || ko "H 선택지 중복 경고" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0010 $'주제\n\n1\xef\xb8\x8f\xe2\x83\xa3 키캡 선택지'
grep -q '^경고: 본문 줄 머리에 선택지 이모지' "$SB/err" && ok "H 본문 키캡(1️⃣) 선택지 줄도 경고" || ko "H 키캡 경고" "$(cat "$SB/err")"
LRM_FORMAT=strict run_n --kind decide --no-doc --options "$OK3" --recommend 3 --decision-id D-0011 "$CBODY"
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "H strict: 답 줄을 도구가 채우므로 통과" || ko "H strict" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0012 "$CBODY" "$SB/f.txt"
[ "$RC" = 0 ] && sed -n 1p "$LOG/posts.log" | jq -r '.attachments[0].blocks[0].text.text' | grep -qxF ':one: 오전 9시' && [ "$(tail -n 2 "$CFG/slack-sent.jsonl" | head -n 1 | jq -r '[.kind,.decision_id] | join(",")')" = "text,D-0012" ] \
  && ok "H 파일 첨부 카드: 선택지 줄은 버튼 카드 메시지에, sent 기록에 decision_id(순서는 J)" || ko "H 첨부" "$(cat "$LOG/posts.log") / $(lastrec)"
run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0013 "$(printf '주제\n\n- 왜: ✅ 와 ❌')"
text | grep -qxF -e '- 왜: :비서-추천대로: 와 :비서-보류:' && text | grep -qF ':white_check_mark: 추천대로 · :x: 보류' && ok "H 본문 ✅❌ 는 기존 치환, 답 줄은 표준 :white_check_mark:·:x:(치환 안 됨)" || ko "H 치환 경계" "$(text)"
# 거부(종료 코드 2, API 호출 0회): 인자 값·조합
rej() { # 이름 기대 문구 -- 인자...
  local name="$1" want="$2"; shift 3
  run_n "$@"
  [ "$RC" = 2 ] && nocall && ! posted && grep -q -- "$want" "$SB/err" && ok "H 거부: $name" || ko "H 거부 $name" "rc=$RC $(cat "$SB/err")"
}
D=(--kind decide --no-doc --recommend 1 --decision-id D-0100)
rej "선택지 1개" '^--options 범위 오류' -- "${D[@]}" --options '1=가' "$CBODY"
rej "선택지 5개" '^--options 범위 오류' -- "${D[@]}" --options '1=가,2=나,3=다,4=라,5=마' "$CBODY"
rej "번호 건너뜀(1,3)" '^--options 범위 오류' -- "${D[@]}" --options '1=가,3=다' "$CBODY"
rej "번호 0" '^--options 범위 오류' -- "${D[@]}" --options '0=가,1=나' "$CBODY"
rej "번호 중복" '^--options 번호 중복' -- "${D[@]}" --options '1=가,1=나' "$CBODY"
rej "문구 빔" '^--options 문구 없음' -- "${D[@]}" --options '1=,2=나' "$CBODY"
rej "문구 공백만" '^--options 문구 없음' -- "${D[@]}" --options '1=  ,2=나' "$CBODY"
rej "문구 81자" '^--options 문구가 81자' -- "${D[@]}" --options "1=$K41$K40,2=나" "$CBODY"
rej "문구 줄바꿈(가짜 답 줄)" '^--options 문구에 줄바꿈' -- "${D[@]}" --options $'1=가\n👉 답: 2,2=나' "$CBODY"
rej "문구 제어 문자" '^--options 문구에 줄바꿈' -- "${D[@]}" --options $'1=가\033[31m,2=나' "$CBODY"
rej "문구 방향 제어 문자" '^--options 문구에 줄바꿈' -- "${D[@]}" --options $'1=가\xe2\x80\xae,2=나' "$CBODY"
rej "형식 아님" '^--options 형식 오류' -- "${D[@]}" --options '가,나' "$CBODY"
rej "빈 값" '^--options 형식 오류' -- "${D[@]}" --options '' "$CBODY"
rej "--recommend 없음" '^--recommend 필요' -- --kind decide --no-doc --decision-id D-0100 --options "$OK3" "$CBODY"
rej "--recommend 범위 밖(4, 선택지 3개)" '^--recommend 범위 오류' -- --kind decide --no-doc --recommend 4 --decision-id D-0100 --options "$OK3" "$CBODY"
rej "--recommend 숫자 아님" '^--recommend 범위 오류' -- --kind decide --no-doc --recommend x --decision-id D-0100 --options "$OK3" "$CBODY"
rej "--decision-id 없음" '^--decision-id 필요' -- --kind decide --no-doc --recommend 1 --options "$OK3" "$CBODY"
for bad in 'D 1' 'X-1' 'D-' 'D-한글' 'D-1;x' 'D-12345678901234567'; do
  rej "--decision-id '$bad'" '^--decision-id 형식 오류' -- --kind decide --no-doc --recommend 1 --decision-id "$bad" --options "$OK3" "$CBODY"
done
rej "--reversible 값" '^--reversible 값 오류' -- "${D[@]}" --options "$OK3" --reversible 아마 "$CBODY"
for bad in 0/9 10/9 3 a/b 1/1000 ''; do
  rej "--seq '$bad'" '^--seq 형식 오류' -- "${D[@]}" --options "$OK3" --seq "$bad" "$CBODY"
done
rej "--kind done + --options" '는 --kind decide 와만 사용' -- --kind done --options "$OK3" "끝"
rej "--kind todo + --decision-id" '는 --kind decide 와만 사용' -- --kind todo --decision-id D-1 "할 일"
rej "--kind 없이 --options" '는 --kind decide 와만 사용' -- --options "$OK3" "x"
rej "--recommend 만(--options 없음)" '는 --options 와 함께 사용' -- --kind decide --no-doc --recommend 1 "$CBODY$ANS"
rej "--seq 만(--options 없음)" '는 --options 와 함께 사용' -- --kind decide --no-doc --seq 1/2 "$CBODY$ANS"
rej "--options + --choices" '^--options 와 --choices 는 함께 쓸 수 없음' -- "${D[@]}" --options "$OK3" --choices 3 "$CBODY"
rej "--todo-id + --kind decide --no-doc" '^--todo-id 는 --kind todo 와만 사용' -- --kind decide --no-doc --todo-id T-1 "$CBODY$ANS"
rej "--todo-id 형식" '^--todo-id 형식 오류' -- --kind todo --todo-id 'T-1 2' "할 일"
rej "--options 값 없음" '^--options 값 필요' -- --kind decide --no-doc --options
# python3 실패 → 카드 없이 보내지 않음(fail-closed)
for part in opts card; do
  FAKE_PY_FAIL=$part run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0200 "$CBODY"
  [ "$RC" = 2 ] && ! posted && nocall && ok "H python3 '$part' 실패 → 미전송·종료 코드 2" || ko "H py $part" "rc=$RC $(cat "$SB/err")"
done
# 옛 결정 카드·다른 kind 기록은 새 필드 없음
run_n --kind decide --no-doc --choices 2 "$CBODY"
[ "$(lastrec | jq -c '[has("decision_id"),has("options"),has("recommended"),has("todo_id")]')" = '[false,false,false,false]' ] && ok "H --choices 카드 기록에는 새 필드 없음(옛 카드로 취급)" || ko "H 옛 카드 기록" "$(lastrec)"
# 할 일 번호
TODO_G2="👉 답: 끝나면 :white_check_mark: (또는 :hammer_and_wrench:) · 보류는 :x:"
run_n --kind todo --todo-id T-0001 $'설정 파일 넣기\n\n• 무엇: 키 파일 복사'
[ "$RC" = 0 ] && [ "$(errn)" = 0 ] && [ "$(text)" = "$TODO_G 설정 파일 넣기"$'\n\n• 무엇: 키 파일 복사\n\n'"$TODO_G2"$'\n\n참고: 할 일 T-0001' ] && [ "$(lastrec | jq -r '[.form_kind,.todo_id] | join(",")')" = "todo,T-0001" ] \
  && ok "H --todo-id: 답 줄 없으면 완료 안내 줄·'참고: 할 일 T-…' 부착, no-answer 없음, sent 에 todo_id" || ko "H todo-id" "rc=$RC $(text) / $(cat "$SB/err") / $(lastrec)"
run_n --kind todo --todo-id T-0002 $'설정 파일 넣기\n\n👉 답: 끝나면 :white_check_mark:'
[ "$(text | grep -c '👉')" = 1 ] && [ "$(text | tail -n 1)" = "참고: 할 일 T-0002" ] && ok "H --todo-id: 답 줄이 있으면 안내 줄은 안 붙이고 번호만" || ko "H todo 답 줄" "$(text)"
run_n --kind todo $'설정 파일 넣기\n\n• 무엇: 키'
grep -q "👉 답:' 줄이 없음" "$SB/err" && ! text | grep -q '👉' && ! lastrec | jq -e 'has("todo_id")' >/dev/null && ok "H --todo-id 없는 todo 는 기존 동작(no-answer 경고·안내 줄 없음·todo_id 없음)" || ko "H todo 기존" "$(cat "$SB/err") / $(lastrec)"
# 스레드 답글의 반응은 수집되지 않으므로 번호 카드·할 일 번호 카드는 --thread 와 함께 보내지 않음. 옛 --choices 카드·글 호출은 불변
rej "--options + --thread" '^--thread 는 --options·--todo-id 카드와 함께 쓸 수 없음' -- "${D[@]}" --options "$OK3" --thread 1730000000.000001 "$CBODY"
rej "--todo-id + --thread" '^--thread 는 --options·--todo-id 카드와 함께 쓸 수 없음' -- --kind todo --todo-id T-0300 --thread 1730000000.000001 "할 일"
run_n --kind decide --no-doc --choices 2 --thread 1730000000.000001 "$CBODY"
[ "$RC" = 0 ] && posted && [ "$(jq -r '.thread_ts' "$LOG/post.json")" = 1730000000.000001 ] && ok "H --choices + --thread 는 기존대로 전송(옛 카드 불변)" || ko "H choices+thread" "rc=$RC $(cat "$SB/err")"
# todo 카드: 줄 중간 ✅·❌ 도 표준 이름. 강비서 이름으로 바뀌면 사용자가 그 이모지를 눌러도 완료·보류로 안 읽힘
run_n --kind todo --todo-id T-0301 $'설정 파일 넣기\n\n👉 답: 끝나면 ✅ · 보류는 ❌'
[ "$RC" = 0 ] && text | grep -qxF '👉 답: 끝나면 :white_check_mark: · 보류는 :x:' && ! text | grep -q '비서-' && ok "H todo 카드: 본문 답 줄의 ✅·❌ → :white_check_mark:·:x:(강비서 이름 없음)" || ko "H todo ✅ 글자" "rc=$RC $(text)"
run_n --kind todo $'설정 파일 넣기\n\n• 다 하면 ✅ 를 눌러 주세요 (보류 ❌)'
[ "$RC" = 0 ] && text | grep -qxF '• 다 하면 :white_check_mark: 를 눌러 주세요 (보류 :x:)' && ok "H todo 카드(--todo-id 없음)도 줄 중간 ✅·❌ 는 표준 이름" || ko "H todo 번호 없음 ✅" "$(text)"
run_n --kind done $'끝\n\n• 줄 중간 ✅ 와 ❌'
text | grep -qxF '• 줄 중간 :비서-추천대로: 와 :비서-보류:' && ok "H todo 외 kind 의 줄 중간 ✅·❌ 는 기존 치환 유지" || ko "H todo 외 치환" "$(text)"

# 두 자리 번호는 구분자로 봐서 범위 오류로 거부(문구에 조용히 합쳐지지 않게)
rej "두 자리 번호가 중간에(1,22,2)" '^--options 범위 오류' -- "${D[@]}" --options '1=가,22=나,2=다' "$CBODY"
rej "두 자리 번호(1,10)" '^--options 범위 오류' -- "${D[@]}" --options '1=가,10=나' "$CBODY"
# 문구 안의 이모지 이름·답 줄 기호는 답 이모지·답 줄로 오인되므로 거부
rej "문구 :white_check_mark: 이름" '^--options 문구에 이모지 이름' -- "${D[@]}" --options '1=:white_check_mark: 추천대로 흉내,2=나' "$CBODY"
rej "문구 :two: 이름" '^--options 문구에 이모지 이름' -- "${D[@]}" --options '1=가,2=:two: 가짜' "$CBODY"
rej "문구 강비서 이모지 이름" '^--options 문구에 이모지 이름' -- "${D[@]}" --options '1=가 :비서-추천대로:,2=나' "$CBODY"
rej "문구 👉 답" '^--options 문구에 이모지 이름' -- "${D[@]}" --options '1=가,2=👉 답: 1' "$CBODY"
rej "문구 ✅ 글자" '^--options 문구에 이모지 이름' -- "${D[@]}" --options '1=✅ 추천,2=나' "$CBODY"
rej "문구 ❌ 글자" '^--options 문구에 이모지 이름' -- "${D[@]}" --options '1=가,2=❌ 보류' "$CBODY"
rej "문구 키캡 숫자" '^--options 문구에 이모지 이름' -- "${D[@]}" --options $'1=가,2=1\xef\xb8\x8f\xe2\x83\xa3 번' "$CBODY"
run_n "${D[@]}" --options '1=시각 10:30:00,2=비율 3:2 안' "$CBODY"
[ "$RC" = 0 ] && posted && [ "$(lastrec | jq -r '.options["1"]')" = "시각 10:30:00" ] && ok "H 문구 안 콜론 숫자(10:30:00·3:2)는 이모지 이름이 아니라 통과" || ko "H 콜론 문구" "rc=$RC $(cat "$SB/err")"
# 같은 결정 번호 재사용은 7일 안이면 경고(전송은 함). 기록이 없거나 7일이 지났으면 조용함
run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0400 "$CBODY"
[ "$RC" = 0 ] && [ "$(errn)" = 0 ] && ok "H 새 결정 번호는 경고 없음" || ko "H 새 번호" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0400 "$CBODY"
[ "$RC" = 0 ] && posted && grep -q '^경고: --decision-id D-0400 로 최근 7일 안에 보낸 카드가 있음(전송함)' "$SB/err" && [ "$(errn)" = 1 ] && ok "H 같은 결정 번호를 7일 안에 다시 쓰면 경고 1줄(전송함)" || ko "H 번호 재사용" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0401 "$CBODY"
[ "$(errn)" = 0 ] && ok "H 다른 결정 번호는 경고 없음" || ko "H 다른 번호" "$(cat "$SB/err")"
printf '{"at":1,"channel":"C","ts":"1.1","thread_ts":"","kind":"text","title":"옛","tag":"","decision_id":"D-0402"}\n' >> "$CFG/slack-sent.jsonl"
run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0402 "$CBODY"
[ "$RC" = 0 ] && [ "$(errn)" = 0 ] && ok "H 7일 지난 기록의 번호는 경고 없음" || ko "H 옛 번호" "rc=$RC $(cat "$SB/err")"
# '참고:' 줄 뒤에 다른 줄이 더 있으면 결정 번호 줄은 그 뒤(메시지 맨 끝)
run_n --kind decide --no-doc --options "$OK3" --recommend 1 --decision-id D-0403 $'주제\n\n- 왜: 배경\n\n참고: 샘플-1\n\n추가 설명 줄'
[ "$RC" = 0 ] && [ "$(text | tail -n 2)" = $'참고: 샘플-1\n결정 D-0403' ] && ok "H 참고 줄은 맨 아래로 모이고 결정 번호 줄은 그 끝(추가 줄은 항목으로 올라감)" || ko "H 참고 뒤 줄" "$(text)"

# ---- I. 색 띠(attachment)·제목 굵게 ------------------------------------------------------------
newhome i
for spec in "decide|#F2994A" "todo|#F2994A" "warn|#EB5757" "progress|#2F80ED" "done|#27AE60" "doc|#27AE60"; do
  k=${spec%%|*}; c=${spec#*|}
  run_n --kind "$k" $([ "$k" = decide ] && echo --no-doc) $'주제\n\n• 무엇: 본문'"$ANS"
  [ "$RC" = 0 ] && [ "$(payload '.attachments | length')" = 1 ] && [ "$(payload '.attachments[0].color')" = "$c" ] \
    && [ "$(payload '.attachments[0].mrkdwn_in | index("text")')" = 0 ] && ok "I --kind $k → 색 띠 $c, mrkdwn 본문" || ko "I 색 $k" "rc=$RC $(cat "$SB/err") $(cat "$SB/out")"
done
run_n --kind done $'알림 시각 통일\n\n• 결과: 완료'
[ "$(payload '.attachments[0].text | split("\n")[0]')" = "*:white_check_mark: 알림 시각 통일*" ] && ok "I 첫 줄(종류 이모지 포함)이 *굵게*" || ko "I 제목 굵게" "$(payload '.attachments[0].text')"
[ "$(payload '.attachments[0].text | split("\n")[2]')" = "• 결과: 완료" ] && ok "I 둘째 줄 이후 본문은 그대로" || ko "I 본문 불변" "$(payload '.attachments[0].text')"
[ "$(payload '.text')" = ":white_check_mark: 알림 시각 통일" ] && [ "$(payload '.attachments[0].fallback')" = ":white_check_mark: 알림 시각 통일" ] && ok "I 알림 미리보기 text·fallback 에 제목(별표 없이)" || ko "I fallback" "$(cat "$LOG/post.json")"
run_n --kind done $'✅ *열린 PR*\n\n• 무엇: 확인'
[ "$(payload '.attachments[0].text | split("\n")[0]')" = ":white_check_mark: *열린 PR*" ] && [ "$(payload '.text')" = ":white_check_mark: 열린 PR" ] && ok "I 첫 줄이 이미 굵으면 중복 굵게 없음, 미리보기엔 별표 제거" || ko "I 중복 굵게" "$(cat "$LOG/post.json")"
run_n --kind done $'주제\n\n• 무엇: *핵심* 하나'
[ "$RC" = 0 ] && [ "$(errn)" = 0 ] && ok "I 제목 줄은 굵게 검사 제외(자동 굵게 + 본문 굵게 1곳 → 경고 없음)" || ko "I 굵게 제외" "$(cat "$SB/err")"
run_n --kind done $'*주제*\n\n• 무엇: *핵심* 하나'
[ "$(errn)" = 0 ] && ok "I 제목에 직접 쓴 굵게도 검사 제외" || ko "I 제목 직접 굵게" "$(cat "$SB/err")"
run_n --kind done $'주제\n\n• 무엇: *하나* 와 *둘*'
grep -q '^경고: 굵게가 2곳' "$SB/err" && ok "I 본문 굵게 2곳은 여전히 경고" || ko "I 본문 굵게 2곳" "$(cat "$SB/err")"
run_n --kind warn --thread 1700000000.000001 $'스레드\n\n• 무엇: 답글'
[ "$(payload '.thread_ts')" = 1700000000.000001 ] && [ "$(payload '.attachments | length')" = 1 ] && ok "I 스레드 답글도 thread_ts 유지 + 색 띠" || ko "I 스레드" "$(cat "$LOG/post.json")"
run_n --kind decide --no-doc --choices 3 $'알림 시각\n\n• 왜: 배경'
[ "$(payload '.attachments[0].text | contains("👉 답: :비서-a: A")')" = true ] && ok "I --choices 안내 줄이 색 띠 본문 안에 있음" || ko "I choices" "$(cat "$LOG/post.json")"
run_n --kind decide --no-doc --options "1=가 안,2=나 안" --recommend 1 --decision-id D-0900 $'주제\n\n• 왜: 배경'
[ "$(payload '.attachments[0].blocks[0].text.text | contains(":one: 가 안") and contains("결정 D-0900")')" = true ] && [ "$(lastrec | jq -r '.decision_id')" = D-0900 ] && ok "I --options 카드: 선택지·결정 번호가 색 띠 본문에, 기록의 decision_id 유지" || ko "I options" "$(cat "$LOG/post.json")"
run_n --kind done "끝"
r=$(lastrec)
[ "$(printf '%s' "$r" | jq -r '.ts')" = 1700000000.000100 ] && [ "$(printf '%s' "$r" | jq -r '.channel')" = Dfake ] && [ "$(printf '%s' "$r" | jq -r '.title')" = ":white_check_mark: 끝" ] \
  && ok "I sent 기록에 ts·channel 유지, title 에 별표 없음" || ko "I sent 기록" "$r"
run_n "종류 없는 호출"
[ "$(payload 'keys | join(",")')" = "channel,text" ] && [ "$(payload '.text')" = "종류 없는 호출" ] && ok "I --kind 없는 호출은 페이로드 불변(attachments 없음, 굵게 없음)" || ko "I 종류 없음" "$(cat "$LOG/post.json")"
printf '<html></html>' > "$SB/doc.html"
run_n --kind doc $'문서\n\n• 무엇: 첨부' "$SB/doc.html"
[ "$RC" = 0 ] && [ "$(payload 'has("attachments")')" = false ] && [ "$(payload '.initial_comment | split("\n")[0]')" = ":비서-공유합니다: 문서" ] && [ "$(lastrec | jq -r '.file_id')" = Ffake1 ] \
  && ok "I 파일 첨부는 기존 경로 그대로(색 띠 없음, file_id 기록)" || ko "I 파일 첨부" "rc=$RC $(cat "$LOG/post.json") $(cat "$SB/err")"
# 색 값은 스크립트 한 곳에만 둠
n=$(grep -c '#F2994A\|#EB5757\|#2F80ED\|#27AE60' "$NOTIFY")
[ "$n" = 4 ] && ok "I 색 값은 스크립트의 KIND_COLOR 한 곳(4줄)에만 있음" || ko "I 색 값 위치" "$n"

# ---- Z. 토큰·실제 접속 ----------------------------------------------------------------------
leak=$(grep -rlF -- "$FAKE_TOKEN" "$SB" 2>/dev/null | grep -v '/slack-bot\.env$' || true)
[ -z "$leak" ] && ok "Z 토큰이 stdout·stderr·로그·sent 기록·가짜 curl 호출 기록에 없음(설정 파일 제외 전체 검색)" || ko "Z 토큰 노출" "$leak"

# ---- Y. --alive 생존 줄 -----------------------------------------------------------------------
printf 'print("봇: 연결됨 · 마지막 수신 2분 전")\n' > "$SB/alive.sh"
LRM_BOT_ALIVE="$SB/alive.sh"; export LRM_BOT_ALIVE
run_n --kind progress --alive "점검$ANS"
[ "$RC" = 0 ] && [ "$(text | sed -n 2p)" = "봇: 연결됨 · 마지막 수신 2분 전" ] && [ "$(errn)" = 0 ] && ok "Y --alive → 주제 바로 아래 생존 줄, 경고 없음" || ko "Y --alive" "rc=$RC $(text | head -2)"
printf 'raise SystemExit(1)\n' > "$SB/alive.sh"
run_n --kind progress --alive "점검$ANS"
[ "$RC" = 0 ] && [ "$(text | sed -n 2p)" = "" ] && ok "Y 생존 조회 실패 → 줄 없이 전송" || ko "Y 조회 실패" "rc=$RC"
unset LRM_BOT_ALIVE

# ---- J. 첨부 + 번호 카드: 버튼 카드를 먼저, 파일은 카드 스레드에 / 버튼 없는 결정 카드 경고 -------------------
newhome j; : > "$LOG/posts.log"
printf 'x' > "$SB/clip.mp4"
run_n --kind decide --no-doc --options '1=A 안,2=B 안' --recommend 2 --decision-id D-0J01 "주제" "$SB/clip.mp4"
c1=$(sed -n 1p "$LOG/posts.log"); c2=$(sed -n 2p "$LOG/posts.log")
[ "$RC" = 0 ] && [ "$(printf '%s' "$c1" | jq -r '.attachments[0].blocks[1].elements | map(.value) | join(",")')" = 'D-0J01|1,D-0J01|2,D-0J01|custom' ] \
  && ok "J 첨부+--options: 첫 호출이 버튼 달린 카드(chat.postMessage)" || ko "J 카드 먼저" "rc=$RC $c1"
[ "$(printf '%s' "$c2" | jq -r '.thread_ts')" = 1700000000.000100 ] && [ "$(printf '%s' "$c2" | jq -r '.files[0].title')" = clip.mp4 ] \
  && ok "J 파일은 카드 ts 의 스레드로 업로드" || ko "J 파일 스레드" "$c2"
[ "$(wc -l < "$CFG/slack-sent.jsonl" | tr -d ' ')" = 2 ] && [ "$(sed -n 1p "$CFG/slack-sent.jsonl" | jq -r '[.ts,.decision_id,.kind]|join(",")')" = '1700000000.000100,D-0J01,text' ] \
  && [ "$(sed -n 2p "$CFG/slack-sent.jsonl" | jq 'has("decision_id")')" = false ] && ok "J 보낸 기록: 카드(ts·decision_id) 1줄 + 파일 1줄(카드 필드 없음)" || ko "J 보낸 기록" "$(cat "$CFG/slack-sent.jsonl")"
run_n --kind decide --no-doc --options '1=A 안,2=B 안' --recommend 2 --decision-id D-0J02 "주제" "$SB/none.mp4"
[ "$RC" = 2 ] && nocall && ok "J 첨부 파일이 없으면 카드도 보내지 않음(종료 코드 2)" || ko "J 파일 없음" "rc=$RC"
LRM_BTN_WARN=on run_n --kind decide --no-doc --choices 2 "주제"
[ "$RC" = 0 ] && grep -q '선택지 버튼 없음' "$SB/err" && ok "J --options 없는 결정 카드 → 버튼 없음 경고(전송은 함)" || ko "J 버튼 없음 경고" "rc=$RC $(cat "$SB/err")"
LRM_BTN_WARN=on run_n --kind decide --no-doc --options '1=A 안,2=B 안' --recommend 2 --decision-id D-0J03 "주제"
! grep -q '선택지 버튼 없음' "$SB/err" && ok "J --options 카드는 경고 없음" || ko "J 정상 카드 경고" "$(cat "$SB/err")"

# ---- K. 줄바꿈 양식: '- ' 여러 줄 허용 · ' / '·' · ' 3개 이상 이은 줄은 경고만 ---------------------------
newhome k
run_n --kind done $'완료\n\n- 무엇: 끝\n- 왜: 이유\n- 결과: 반영'
[ "$RC" = 0 ] && [ "$(errn)" = 0 ] && posted && ok "K '- ' 항목 여러 줄 → 경고 없이 전송" || ko "K 여러 줄" "rc=$RC $(cat "$SB/err")"
run_n --kind done $'완료\n\n- 무엇: A / B / C / D'
[ "$RC" = 0 ] && grep -q "^경고: ' / '·' · ' 로 이은 줄이 있음(3번째 줄)" "$SB/err" && ! grep -q '양식 위반' "$SB/err" && posted && ok "K ' / ' 3개 이상 → 경고만(위반 건수에 안 셈, 전송)" || ko "K 슬래시 경고" "rc=$RC $(cat "$SB/err")"
run_n --kind done $'완료\n\n- 무엇: A · B · C · D'
grep -q "로 이은 줄이 있음" "$SB/err" && ok "K ' · ' 3개 이상 → 경고" || ko "K 가운뎃점 경고" "$(cat "$SB/err")"
LRM_FORMAT=strict run_n --kind done $'완료\n\n- 무엇: A / B / C / D'
[ "$RC" = 0 ] && posted && ok "K strict 에서도 거부하지 않음" || ko "K strict" "rc=$RC"
run_n --kind done $'완료\n\n- 무엇: A / B\n- 왜: C · D'
[ "$(errn)" = 0 ] && ok "K 2개까지는 경고 없음" || ko "K 2개" "$(cat "$SB/err")"
run_n --kind done $'완료 · 가 · 나 · 다 · 라\n\n- 무엇: 끝'
! grep -q "로 이은 줄" "$SB/err" && ok "K 첫 줄(제목)은 검사 제외" || ko "K 제목" "$(cat "$SB/err")"
run_n --kind decide --no-doc "주제$ANS"
! grep -q "로 이은 줄" "$SB/err" && ok "K 고정 형식 '👉 답:' 줄은 검사 제외" || ko "K 답 줄" "$(cat "$SB/err")"

# ---- L. 본문 로컬 경로 거부·비밀 첨부 거부·--link-to·직접 호출 카드 양식 ---------------------------------------
newhome l; : > "$LOG/posts.log"
printf 'x' > "$SB/doc.html"; printf 'x' > "$SB/server.pem"
run_n --kind done $'완료\n\n- 무엇: /Users/me/out/report.html 확인'
[ "$RC" = 2 ] && nocall && grep -q '본문에 로컬 경로(/Users/me/out/report.html)가 있는데 같은 파일이 --file 첨부로 오지 않음' "$SB/err" && ok "L 본문 경로 + 첨부 없음 → 거부(종료 코드 2, API 호출 0회)" || ko "L 경로 거부" "rc=$RC $(cat "$SB/err")"
run_n --kind done --no-check $'완료\n\n참고: ./out/a.pdf'
[ "$RC" = 2 ] && nocall && ok "L --no-check 로도 못 풂(참고 줄의 경로도 대상)" || ko "L no-check" "rc=$RC"
run_n --kind done $'완료\n\n- 무엇: '"$SB/doc.html" "$SB/doc.html"
[ "$RC" = 0 ] && ok "L 같은 파일을 첨부하면 통과" || ko "L 첨부 통과" "rc=$RC $(cat "$SB/err")"
run_n --kind done --attached "$SB/doc.html" $'완료\n\n- 무엇: '"$SB/doc.html"
[ "$RC" = 0 ] && posted && ok "L --attached(따로 첨부하는 파일)로 알리면 통과" || ko "L attached" "rc=$RC $(cat "$SB/err")"
run_n --kind done $'완료\n\n- 무엇: `docs/a.html` 참조, https://x.co/a.pdf?f=/t/b.png#c.html'
[ "$RC" = 0 ] && ok "L 백틱 코드·URL 안의 파일명은 대상 아님" || ko "L 코드·URL" "rc=$RC $(cat "$SB/err")"
run_n --kind done "완료" "$SB/server.pem"
[ "$RC" = 2 ] && nocall && grep -q '민감 파일' "$SB/err" && ok "L 비밀 파일 첨부 → 거부(종료 코드 2)" || ko "L 비밀 첨부" "rc=$RC $(cat "$SB/err")"

: > "$LOG/fail.link"; : > "$LOG/posts.log"
run_n --kind decide --no-doc --options '1=A,2=B' --recommend 1 --decision-id D-0L01 --link-to 1700000000.000100 $'주제\n\n배경 한 줄'
c=$(sed -n 1p "$LOG/posts.log" | jq -r '.attachments[0].blocks[0].text.text')
[ "$RC" = 0 ] && printf '%s' "$c" | grep -qF '<https://example.slack.com/archives/Dfake/p1700000000000100|결정 카드 열기>' && [ "$(printf '%s' "$c" | grep -n '결정 카드 열기' | cut -d: -f1)" -lt "$(printf '%s' "$c" | grep -n '^참고:' | cut -d: -f1)" ] && grep -q 'chat.getPermalink' "$LOG/calls.log" && ok "L --link-to: 조회한 메시지 링크가 본문 끝에" || ko "L link-to" "rc=$RC $c"
printf 'x' > "$LOG/fail.link"
run_n --kind done --link-to 1700000000.000100 $'완료\n\n- 무엇: 끝'
[ "$RC" = 0 ] && ! text | grep -q 'slack.com' && grep -q '링크 조회 실패' "$SB/err" && ok "L 링크 조회 실패 → 링크 없이 전송(경고)" || ko "L link 실패" "rc=$RC $(text) $(cat "$SB/err")"
: > "$LOG/fail.link"
run_n --kind done --link-to bogus "완료"
[ "$RC" = 2 ] && nocall && ok "L --link-to 형식 오류 → 종료 코드 2" || ko "L link 형식" "rc=$RC"

run_n --kind decide --no-doc --options '1=A,2=B' --recommend 1 --decision-id D-0L02 $'주제\n\n• 왜: 배경\n참고: main 9c972aa'
c=$(sed -n 1p "$LOG/posts.log" | jq -r '.attachments[0].blocks[0].text.text')
printf '%s' "$c" | grep -qxF -e '- 왜: 배경' && ! printf '%s' "$c" | grep -q '^- 참고' && printf '%s' "$c" | grep -qx '참고: main 9c972aa' && [ "$(printf '%s' "$c" | tail -n 2 | head -n 1)" = '' -o "$(printf '%s' "$c" | tail -n 1)" = '결정 D-0L02' ] \
  && ok "L 직접 호출 결정 카드도 전송기와 같은 줄 양식('- ' 항목, 참고 줄은 머리표 없이 맨 아래)" || ko "L 카드 양식" "$c"

# ---- M. 직접 호출 결정의 문서 계약·--link-to 위치 -------------------------------------------------------
newhome m; : > "$LOG/posts.log"
run_n --kind decide --options '1=A,2=B' --recommend 1 --decision-id D-0M01 $'주제\n\n배경'
[ "$RC" = 2 ] && nocall && grep -q -- '--no-doc 를 명시' "$SB/err" && ok "M 직접 호출 decide: 첨부·--no-doc 없으면 종료 코드 2 + 조치 안내, 호출 0회" || ko "M 문서 필수" "rc=$RC $(cat "$SB/err")"
run_n --kind decide "주제$ANS"
[ "$RC" = 2 ] && nocall && ok "M --options 없는 decide 도 같은 계약" || ko "M choices 없는 decide" "rc=$RC"
run_n --kind decide --no-doc --options '1=A,2=B' --recommend 1 --decision-id D-0M02 $'주제\n\n배경'
[ "$RC" = 0 ] && posted && ok "M --no-doc 명시하면 통과" || ko "M no-doc" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --attached "$SB/doc.html" --options '1=A,2=B' --recommend 1 --decision-id D-0M03 $'주제\n\n배경'
[ "$RC" = 0 ] && ok "M --attached 로 따로 첨부를 알리면 통과" || ko "M attached" "rc=$RC $(cat "$SB/err")"
run_n --kind decide --options '1=A,2=B' --recommend 1 --decision-id D-0M04 $'주제\n\n배경' "$SB/doc.html"
[ "$RC" = 0 ] && ok "M 파일 인자가 있으면 통과" || ko "M 파일 인자" "rc=$RC $(cat "$SB/err")"
run_n --kind done --no-doc "완료"
[ "$RC" = 2 ] && nocall && ok "M --no-doc 는 decide 와만" || ko "M no-doc 오용" "rc=$RC"
printf 'x' > "$SB/key.p8"; printf 'x' > "$SB/a.crt"; printf 'x' > "$SB/b.cer"; printf 'x' > "$SB/c.der"; printf 'x' > "$SB/d.gpg"; printf 'x' > "$SB/e.asc"
for f in key.p8 a.crt b.cer c.der d.gpg e.asc; do
  run_n --kind done "완료" "$SB/$f"
  [ "$RC" = 2 ] && nocall && grep -q '민감 파일' "$SB/err" && ok "M 비밀 확장자 첨부 거부: $f" || ko "M 비밀 확장자 $f" "rc=$RC"
done
run_n --kind done $'완료\n\n참고:/tmp/a.pdf'
[ "$RC" = 2 ] && nocall && ok "M '참고:/tmp/a.pdf'(콜론 바로 뒤 경로) 거부" || ko "M 콜론 경로" "rc=$RC"
run_n --kind done $'완료\n\n참고: docs/결과 보고서.pdf'
[ "$RC" = 2 ] && nocall && ok "M '참고: docs/결과 보고서.pdf'(공백 든 상대 경로) 거부" || ko "M 공백 상대 경로" "rc=$RC"
: > "$LOG/fail.link"; : > "$LOG/posts.log"
run_n --kind decide --no-doc --options '1=A,2=B' --recommend 1 --decision-id D-0M05 --link-to 1700000000.000100 $'주제\n\n- 배경\n참고: main 9c972aa'
c=$(sed -n 1p "$LOG/posts.log" | jq -r '.attachments[0].blocks[0].text.text')
l=$(printf '%s' "$c" | grep -n '결정 카드 열기' | cut -d: -f1); r=$(printf '%s' "$c" | grep -n '^참고:' | cut -d: -f1)
[ -n "$l" ] && [ "$l" -lt "$r" ] && [ "$(printf '%s' "$c" | tail -n 1)" = '결정 D-0M05' -o "$(printf '%s' "$c" | tail -n 1)" = '참고: main 9c972aa' ] && ok "M --link-to 링크는 참고 줄 앞" || ko "M 링크 위치" "$c"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
