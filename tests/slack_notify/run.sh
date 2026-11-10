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
# 가짜 python3: FAKE_PY_FAIL=check|attach|both 면 해당 부분 호출(인자 3번째)만 실패, 그 외는 진짜 python3 로 위임
printf '#!/bin/bash\ncase "${FAKE_PY_FAIL:-}:${3:-}" in check:check|attach:attach|both:check|both:attach) exit 1 ;; esac\nexec "%s" "$@"\n' "$(command -v python3)" > "$BIN/python3"
chmod 755 "$BIN/python3"
: > "$LOG/calls.log"; : > "$LOG/post.json"; : > "$LOG/fail.api"
API=http://127.0.0.1:9
FAKE_TOKEN="fake-token-0123456789-abcdefghij"
unset LRM_FORMAT

HOME_DIR="" CFG=""
newhome() { # 이름: 설정 파일만 있는 새 HOME(로그·sent 기록 분리용)
  HOME_DIR="$SB/home-$1"; CFG="$HOME_DIR/.config/lrm-hubs"
  mkdir -p "$CFG"
  printf 'SLACK_BOT_TOKEN=%s\nSLACK_USER_ID=Uuser1\n' "$FAKE_TOKEN" > "$CFG/slack-bot.env"
}
newhome main

run_n() { # 인자를 slack_notify.sh 에 그대로 전달. 결과: RC, $SB/out, $SB/err, $LOG/post.json
  : > "$LOG/calls.log"; : > "$LOG/post.json"
  HOME="$HOME_DIR" PATH="$BIN:$PATH" SLACK_API_BASE="$API" FAKE_CURL_LOG="$LOG" bash "$NOTIFY" "$@" > "$SB/out" 2> "$SB/err"
  RC=$?
}
text() { jq -r '.text // .initial_comment // empty' "$LOG/post.json" 2>/dev/null; }
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
  run_n --kind "$k" "주제$ANS"
  [ "$RC" = 0 ] && [ "$(text | head -1)" = "$e 주제" ] && [ "$(errn)" = 0 ] && ok "A --kind $k → 첫 줄 앞에 '$e' 한 번, 경고 없음" || ko "A --kind $k" "rc=$RC $(text | head -1)"
done
run_n --kind decide "🙋 주제$ANS"
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
run_n --kind decide $'주제\n\n• 무엇: 정할 것'
grep -q "경고: 결정·할 일인데 '👉 답:' 줄이 없음" "$SB/err" && ok "B decide 에 👉 줄 없음 → 위반" || ko "B 👉 없음" "$(cat "$SB/err")"
run_n --kind todo $'주제\n\n• 무엇: 할 일'
grep -q "👉 답:' 줄이 없음" "$SB/err" && ok "B todo 에 👉 줄 없음 → 위반" || ko "B todo 👉" "$(cat "$SB/err")"
run_n --kind decide "주제$ANS"
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
run_n --kind decide $'주제\n\n👉 참고 링크'
grep -q "👉 답:' 줄이 없음" "$SB/err" && ok "B '답:' 없는 👉 줄은 답 줄로 안 침" || ko "B 👉 엄격" "$(cat "$SB/err")"
run_n --kind decide $'주제\n\n  :point_right: 답: 확인'
[ "$(errn)" = 0 ] && ok "B ':point_right: 답:' 줄(앞 공백 허용)도 답 줄로 침" || ko "B point_right" "$(cat "$SB/err")"
run_n --kind decide $'주제\n\n👉  답 : 확인'
[ "$(errn)" = 0 ] && ok "B '👉' 와 '답' 사이·'답' 뒤 공백 허용" || ko "B 👉 공백" "$(cat "$SB/err")"
run_n --kind decide $'주제\n\n참고: x\n👉 답: 확인'
grep -q "👉 답:' 줄이 없음" "$SB/err" && ok "B '참고:' 줄 뒤의 👉 답 은 본문이 아니라 답 줄로 안 침" || ko "B 참고 뒤 👉" "$(cat "$SB/err")"
KEYCAP=$'1\xef\xb8\x8f\xe2\x83\xa3'   # 1️⃣ = 숫자 1 + VS16 + U+20E3(Me)
K39=$(printf '가%.0s' $(seq 1 39))
run_n --kind done "$KEYCAP $K39"
[ "$(errn)" = 0 ] && ok "B 키캡(1️⃣)은 1자로 셈(1+가×39=40자 → 통과)" || ko "B 키캡 40" "$(cat "$SB/err")"
run_n --kind done "$KEYCAP ${K39}가"
grep -q '^경고: 첫 줄이 41자(최대 40)' "$SB/err" && ok "B 키캡 포함 41자 → 위반" || ko "B 키캡 41" "$(cat "$SB/err")"

# ---- C. 단계 모드 ----------------------------------------------------------------------------
newhome c
run_n --kind decide "✅ $K41"
[ "$RC" = 0 ] && posted && [ "$(sed -n 1p "$SB/err" | cut -c1-3)" = "경고:" ] && [ "$(tail -n 1 "$SB/err")" = "양식 위반 2건(전송함)" ] && [ "$(grep -c '^경고:' "$SB/err")" = 2 ] && ok "C warn(기본): 위반 2건도 전송·종료 코드 0, stderr 경고 줄 + 마지막 줄 '양식 위반 2건(전송함)'" || ko "C warn" "rc=$RC $(cat "$SB/err")"
LRM_FORMAT=strict run_n --kind decide "✅ $K41"
[ "$RC" = 2 ] && ! posted && nocall && [ "$(grep -c '^거부:' "$SB/err")" = 2 ] && tail -n 1 "$SB/err" | grep -q '^양식 위반 2건(전송 안 함)' && ok "C strict: 위반 시 미전송·API 호출 0회·종료 코드 2, '거부:' 줄" || ko "C strict" "rc=$RC $(cat "$SB/err")"
LRM_FORMAT=strict run_n --kind done "✅ 깨끗한 완료"
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "C strict: 위반 없으면 그대로 전송" || ko "C strict 정상" "rc=$RC"
LRM_FORMAT=off run_n --kind decide "✅ $K41"
n0=$(logn)
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "C off: 위반해도 검사 없이 전송, stderr 없음" || ko "C off" "rc=$RC $(cat "$SB/err")"
LRM_FORMAT=off run_n --kind decide "✅ $K41"
[ "$(logn)" = "$n0" ] && ok "C off: 검사 로그에 줄을 안 남김" || ko "C off 로그" ""
LRM_FORMAT=bogus run_n --kind done "끝"
[ "$RC" = 2 ] && nocall && grep -q 'LRM_FORMAT 값 오류: bogus' "$SB/err" && ok "C LRM_FORMAT 잘못된 값 + --kind → 종료 코드 2, 원인·조치" || ko "C 잘못된 모드" "rc=$RC"
LRM_FORMAT=bogus run_n "그냥 보냄 $K41"
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && ok "C LRM_FORMAT 값이 이상해도 --kind 없는 호출은 영향 없음" || ko "C 모드 무영향" "rc=$RC"
LRM_FORMAT=strict run_n --no-check --kind decide "✅ $K41"
n1=$(logn)
[ "$RC" = 0 ] && posted && [ "$(errn)" = 0 ] && [ "$(lastrec | jq -r '.unchecked')" = true ] && [ "$(lastrec | jq -r '.form_kind')" = decide ] && ok "C --no-check: strict 에서도 검사 없이 전송, sent 기록 unchecked:true" || ko "C --no-check" "rc=$RC $(lastrec)"
run_n --no-check --kind decide "✅ $K41"
[ "$(logn)" = "$n1" ] && ok "C --no-check: 검사를 안 했으므로 로그 줄 없음" || ko "C --no-check 로그" ""
run_n --no-check "kind 없이도 기록"
[ "$(lastrec | jq -r '.unchecked')" = true ] && ok "C --no-check 만 줘도 unchecked:true 기록" || ko "C --no-check 단독" "$(lastrec)"
newhome pyfail
FAKE_PY_FAIL=check run_n --kind done "깨끗"
[ "$RC" = 0 ] && posted && grep -qx '양식 검사 실패 — 검사 없이 전송' "$SB/err" && [ "$(errn)" = 1 ] && printf '%s\n' "$(lastlog)" | grep -Eq "${TABS}check-fail${TABS}warn" && ok "C python3 검사 실패 + warn → 한 줄 알리고 검사 없이 전송, 종료 코드 0, 로그 check-fail" || ko "C 검사 실패 warn" "rc=$RC $(cat "$SB/err") / $(lastlog)"
FAKE_PY_FAIL=check LRM_FORMAT=strict run_n --kind done "깨끗"
[ "$RC" = 2 ] && ! posted && nocall && grep -q '^양식 검사 실패(전송 안 함)' "$SB/err" && ok "C python3 검사 실패 + strict → 미전송·종료 코드 2·API 호출 0회" || ko "C 검사 실패 strict" "rc=$RC $(cat "$SB/err")"

# ---- D. 집계 로그 ----------------------------------------------------------------------------
newhome d
run_n --kind decide --tag ui-hub --title 비밀제목마커 $'✅ 비밀본문마커 LRM-9\n\n• 무엇: 비밀본문마커'
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
  run_n --kind decide --choices "$n" "$BODY"
  [ "$RC" = 0 ] && [ "$(text)" = "$HEAD"$'\n\n'"$g" ] && [ "$(errn)" = 0 ] && ok "F --choices $n → 끝에 안내 줄 정확히 일치, 👉 검사 통과(경고 없음)" || ko "F --choices $n" "rc=$RC $(text | tail -1) / $(cat "$SB/err")"
done
run_n --kind decide --choices 3 $'주제\n\n• 무엇: 정할 것\n\n참고: 샘플-123'
[ "$(text)" = "$HEAD"$'\n\n'"$G3"$'\n\n참고: 샘플-123' ] && ok "F '참고:' 줄이 있으면 안내 줄을 그 앞에 끼움(참고가 맨 아래 유지)" || ko "F 참고 앞" "$(text)"
run_n --kind decide --choices 4 $'주제\n\n👉 답: 직접 쓴 줄'
[ "$(text)" = $':비서-선택해주세요: 주제\n\n👉 답: 직접 쓴 줄' ] && [ "$(text | grep -c 👉)" = 1 ] && ok "F 본문에 👉 줄이 이미 있으면 안내 줄을 붙이지 않음" || ko "F 중복 방지" "$(text)"
run_n --kind decide --choices 2 $'주제\n\n```\n👉 코드 안\n```'
[ "$(text | grep -c '^👉 답:')" = 1 ] && ok "F 코드 블록 안의 👉 는 직접 쓴 줄로 치지 않음(안내 줄 부착)" || ko "F 코드 안 👉" "$(text)"
run_n --kind decide "주제"; ! text | grep -q '👉' && ok "F --choices 없으면 안내 줄 없음(기존 동작)" || ko "F choices 없음" ""
for bad in 1 5 0 abc ""; do
  run_n --kind decide --choices "$bad" "$BODY"
  [ "$RC" = 2 ] && nocall && grep -q '^--choices 범위 오류' "$SB/err" && ok "F --choices '$bad' → 종료 코드 2, 원인·조치, 전송 없음" || ko "F --choices '$bad'" "rc=$RC $(cat "$SB/err")"
done
run_n --kind decide --choices
[ "$RC" = 2 ] && nocall && ok "F --choices 값 없음 → 종료 코드 2" || ko "F --choices 값 없음" "rc=$RC"
run_n --kind done --choices 3 "끝"
[ "$RC" = 2 ] && nocall && grep -q '^--choices 는 --kind decide 와만 사용' "$SB/err" && ok "F decide 가 아닌 --kind 와 --choices → 종료 코드 2" || ko "F 다른 kind" "rc=$RC $(cat "$SB/err")"
run_n --choices 3 "끝"
[ "$RC" = 2 ] && nocall && ok "F --kind 없이 --choices → 종료 코드 2" || ko "F kind 없음" "rc=$RC"
LRM_FORMAT=strict run_n --kind decide --choices 2 "$BODY"
[ "$RC" = 0 ] && posted && ok "F strict 에서도 --choices 로 👉 줄이 채워지면 통과" || ko "F strict+choices" "rc=$RC $(cat "$SB/err")"
run_n --no-check --kind decide --choices 2 "$BODY"
[ "$(text)" = "$HEAD"$'\n\n'"$G2" ] && [ "$(lastrec | jq -r '.unchecked')" = true ] && ok "F --no-check 여도 안내 줄은 붙음" || ko "F no-check+choices" "$(text)"
run_n --kind decide --choices 2 "$BODY" "$SB/f.txt"
[ "$(text)" = "$HEAD"$'\n\n'"$G2" ] && ok "F 파일 첨부 설명글에도 안내 줄 부착" || ko "F 첨부+choices" "$(text)"

# 🅲·🅳 치환
C1=$'\xf0\x9f\x85\xb2' D1=$'\xf0\x9f\x85\xb3' VS=$'\xef\xb8\x8f'
#   :비서-c:·:비서-d: 존재가 미확인이라 --kind(--choices 포함) 지정 시에만 치환, --kind 없는 기존 호출은 원문 유지
run_n "$C1 $D1 $C1$VS $D1$VS 🅰️"
[ "$(text)" = "$C1 $D1 $C1$VS $D1$VS :비서-a:" ] && ok "F --kind 없는 🅲·🅳 는 치환하지 않음(🅰️ 기존 치환은 유지)" || ko "F kind 없는 🅲🅳" "$(text)"
run_n --kind progress "🔄 $C1 $D1 $C1$VS $D1$VS 🅰️"
[ "$(text)" = ":비서-진행중: :비서-c: :비서-d: :비서-c: :비서-d: :비서-a:" ] && ok "F --kind 지정 시 🅲·🅳 → :비서-c:·:비서-d: 치환(변형 선택자 유무 모두)" || ko "F kind 🅲🅳 치환" "$(text)"
run_n --kind decide --choices 3 $'주제\n\n• 안 '"$C1"
[ "$(text | sed -n 3p)" = "• 안 :비서-c:" ] && ok "F --kind decide --choices 본문의 🅲 도 치환" || ko "F choices 🅲" "$(text)"
legacy_cmp() { # 🅲·🅳 규칙 도입 전(402039d) 치환표 8종. --kind 없는 호출은 이 결과와 같아야 함
  printf '%s' "$1" | sed -e 's/🅰️/:비서-a:/g' -e 's/🅱️/:비서-b:/g' -e 's/✅/:비서-추천대로:/g' -e 's/❌/:비서-보류:/g' -e 's/🙋/:비서-선택해주세요:/g' -e 's/📄/:비서-공유합니다:/g' -e 's/🔄/:비서-진행중:/g' -e 's/⚠️/:비서-긴급:/g'
}
same=1
for inp in "🅲 안 C" "$D1$VS 안 D" "선택: ✅ 추천대로 🅲" $'첫줄\n🅲 둘째 ❌' "🅰️ 🅱️ 🅲 🅳 ❌ 🙋 📄 🔄 ⚠️"; do
  run_n "$inp"; [ "$(text)" = "$(legacy_cmp "$inp")" ] || { same=0; ko "F 이전 치환표와 다름" "$inp → $(text)"; }
done
[ "$same" = 1 ] && ok "F --kind 없는 호출은 🅲·🅳 포함 5개 입력에서 이전 치환표(줄머리 ✅ 예외 제외)와 같은 결과" || true
run_n --kind decide --choices 2 $'주제\n\n✅ 완료 표시'
[ "$(text | sed -n 3p)" = ":white_check_mark: 완료 표시" ] && [ "$(text | tail -n 1)" = "$G2" ] && ok "F 본문 줄머리 ✅ 는 완료 표시, 안내 줄의 :비서-추천대로: 는 그대로" || ko "F 줄머리 ✅ + 안내 줄" "$(text)"
run_n --kind decide --choices 2 $'참고: ✅ 문서\n\n본문'
[ "$(text)" = $'참고: :비서-추천대로: 문서\n\n본문\n\n'"$G2" ] && [ "$(lastrec | jq -r '.title')" = "참고: :비서-추천대로: 문서" ] && ok "F 첫 줄이 '참고:' 면 안내 줄을 끝에 붙임(맨 위 삽입·빈 제목 없음)" || ko "F 첫 줄 참고" "$(text) / $(lastrec | jq -r '.title')"
run_n --kind decide --choices 2 $'주제\n\n참고: a\n\n• 무엇: x\n\n참고: b'
[ "$(text)" = $':비서-선택해주세요: 주제\n\n참고: a\n\n• 무엇: x\n\n'"$G2"$'\n\n참고: b' ] && ok "F '참고:' 가 둘이면 마지막 참고 앞에 안내 줄" || ko "F 참고 둘" "$(text)"
run_n --kind decide --choices 2 $'주제\n\n👉 참고 링크'
[ "$(text | tail -n 1)" = "$G2" ] && ok "F '답:' 없는 👉 줄이 있어도 안내 줄을 붙임" || ko "F 👉 엄격 부착" "$(text)"
newhome pyfail2
FAKE_PY_FAIL=attach run_n --kind decide --choices 2 "$BODY"
[ "$RC" = 0 ] && posted && grep -qx '안내 줄 부착 실패 — 안내 줄 없이 전송' "$SB/err" && [ "$(text)" = "$HEAD" ] && ok "F python3 부착 실패 + warn → 안내 줄 없이 전송, 한 줄 알림, 종료 코드 0" || ko "F 부착 실패 warn" "rc=$RC $(cat "$SB/err") / $(text)"
FAKE_PY_FAIL=attach LRM_FORMAT=strict run_n --kind decide --choices 2 "$BODY"
[ "$RC" = 2 ] && ! posted && nocall && grep -q '^안내 줄 부착 실패(전송 안 함)' "$SB/err" && ok "F python3 부착 실패 + strict → 미전송·종료 코드 2·API 호출 0회" || ko "F 부착 실패 strict" "rc=$RC $(cat "$SB/err")"

# --emoji-set std
S2="👉 답: :a: A · :b: B · :white_check_mark: 추천대로 · :x: 보류"
S3="👉 답: :a: A · :b: B · :regional_indicator_c: C · :white_check_mark: 추천대로 · :x: 보류"
S4="👉 답: :a: A · :b: B · :regional_indicator_c: C · :regional_indicator_d: D · :white_check_mark: 추천대로 · :x: 보류"
for n in 2 3 4; do
  eval "g=\$S$n"
  run_n --kind decide --choices "$n" --emoji-set std "$BODY"
  [ "$(text)" = "$HEAD"$'\n\n'"$g" ] && ok "F --emoji-set std --choices $n → 표준 이모지 안내 줄 정확히 일치" || ko "F std $n" "$(text | tail -1)"
done
run_n --kind decide --choices 2 --emoji-set custom "$BODY"
[ "$(text)" = "$HEAD"$'\n\n'"$G2" ] && ok "F --emoji-set custom 은 기본과 같음" || ko "F custom" "$(text | tail -1)"
run_n --kind decide --choices 2 --emoji-set bogus "$BODY"
[ "$RC" = 2 ] && nocall && grep -q '알 수 없는 --emoji-set: bogus' "$SB/err" && ok "F 모르는 --emoji-set → 종료 코드 2" || ko "F 잘못된 emoji-set" "rc=$RC"
run_n --kind decide --choices 2 "$BODY"; r_a=$(lastrec | jq -c 'del(.at)')
[ "$(printf '%s' "$r_a" | jq -r '.form_kind')" = decide ] && ok "F --choices 호출도 sent 기록에 form_kind=decide" || ko "F form_kind" "$r_a"

# ---- Z. 토큰·실제 접속 ----------------------------------------------------------------------
leak=$(grep -rlF -- "$FAKE_TOKEN" "$SB" 2>/dev/null | grep -v '/slack-bot\.env$' || true)
[ -z "$leak" ] && ok "Z 토큰이 stdout·stderr·로그·sent 기록·가짜 curl 호출 기록에 없음(설정 파일 제외 전체 검색)" || ko "Z 토큰 노출" "$leak"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
