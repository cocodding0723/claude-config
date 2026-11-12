#!/usr/bin/env bash
# scrum_post.py·scrum_collect.py 시험. 가짜 claude·알림·osascript 와 임시 폴더만 씀(네트워크·Slack·Jira 없음)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P); S="$HERE/../../hubs/scripts"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); echo "PASS  $1"; }
ko() { FAIL=$((FAIL + 1)); echo "FAIL  $1 — ${2:-}"; }
D=$(mktemp -d "${TMPDIR:-/tmp}/cc-scrum.XXXXXX"); trap 'rm -rf "${D:?}"' EXIT
export LRM_SCRUM_NAME="시험이름" FAKE_NAME="시험이름" FAKE_DIR="$D" LRM_HUB_DIR="$D" LRM_SCRUM_DRAFT_DIR="$D/draft" LRM_SCRUM_LOG="$D/scrum.log" \
  LRM_CLAUDE="$HERE/fake_claude.sh" LRM_NOTIFY="$HERE/fake_send.sh" LRM_SCRUM_OSA="$HERE/fake_osa.sh" \
  LRM_SCRUM_COLLECT="$HERE/fake_collect.py" LRM_SCRUM_NET_WAIT=0 LRM_SCRUM_RETRY_WAITS="0,0"
chmod +x "$HERE"/fake_*.sh
post() { python3 "$S/scrum_post.py" "$@" > "$D/out" 2> "$D/err"; }
sent() { [ -f "$D/sent.log" ] && grep -c '^===$' "$D/sent.log" || echo 0; }
reset() { rm -f "$D"/sent.log "$D"/claude.log "$D"/claude.args "$D"/osa.log "$D"/send-* "$D"/plan-* "$D"/scrum.state.json "$D"/scrum.lock; rm -rf "${D:?}/draft"; }
WK=2026-11-12   # 목요일

# ---- 날짜 잠금 ----
reset; post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(sent)" = 1 ] && grep -q -- "--kind doc" "$D/sent.log" && ok "평일 첫 실행 → doc 1건 발송" || ko "첫 발송" "rc=$rc $(sent)"
post --date $WK; [ "$(sent)" = 1 ] && grep -q "이미 보냄" "$D/err" && ok "같은 날 두 번째 실행은 보내지 않음(날짜 잠금)" || ko "날짜 잠금" "$(sent)"
post --date $WK --force; [ "$(sent)" = 2 ] && ok "--force 는 잠금 무시" || ko "--force" "$(sent)"
post --date 2026-11-13; [ "$(sent)" = 3 ] && ok "다른 날짜는 따로 발송" || ko "다른 날" "$(sent)"
# 잠금은 OS 파일 잠금(리뷰 지적 2): 살아 있는 보유자는 파일이 오래돼도 지켜지고, 죽은 실행의 잔재 파일은 막지 않음
holder() { exec python3 -c 'import fcntl,os,sys,time; fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR); fcntl.flock(fd,fcntl.LOCK_EX); print("held",flush=True); time.sleep(30)' "$D/scrum.lock"; }
reset; holder > "$D/holder.out" & H_PID=$!
for _ in $(seq 50); do grep -q held "$D/holder.out" 2>/dev/null && break; sleep 0.1; done
touch -t 202001010000 "$D/scrum.lock"   # 30분 훨씬 넘게 오래된 잠금 파일
post --date $WK; [ "$(sent)" = 0 ] && [ ! -e "$D/claude.log" ] && grep -q "진행 중" "$D/err" && ok "살아 있는 실행이 잠금을 쥐면 mtime 이 오래돼도 건너뜀" || ko "오래된 잠금 오삭제" "$(sent)"
kill "$H_PID" 2>/dev/null; wait "$H_PID" 2>/dev/null; sleep 0.2
post --date $WK; [ "$(sent)" = 1 ] && ok "보유 프로세스가 죽으면 잠금이 풀려 실행(잔재 파일은 무시)" || ko "죽은 잠금" "$(sent)"
reset; echo x > "$D/scrum.lock"; post --date $WK; [ "$(sent)" = 1 ] && ok "내용만 있고 아무도 안 쥔 잠금 파일은 막지 않음" || ko "잔재 파일" "$(sent)"
python3 - "$S" "$D" <<'PY' && ok "같은 프로세스의 두 번째 take_lock 은 거부(flock)" || ko "중복 take_lock"
import os, sys
sys.path.insert(0, sys.argv[1]); os.environ["LRM_HUB_DIR"] = sys.argv[2]
import scrum_post as p
a = p.take_lock(); b = p.take_lock()
assert a is not None and b is None, (a, b)
p.release_lock(a); c = p.take_lock(); assert c is not None
PY

# ---- 주말·공휴일 건너뜀 ----
reset; post --date 2026-11-14; rc=$?
[ $rc = 0 ] && [ "$(sent)" = 0 ] && [ ! -e "$D/claude.log" ] && grep -q "주말" "$D/err" && ok "토요일은 claude 도 부르지 않고 건너뜀" || ko "토요일" "rc=$rc"
post --date 2026-11-15; [ "$(sent)" = 0 ] && ok "일요일 건너뜀" || ko "일요일"
printf '# 시험 공휴일\n2026-11-12\n' > "$D/scrum-holidays.txt"; post --date $WK; [ "$(sent)" = 0 ] && ok "공휴일 목록의 날은 건너뜀" || ko "공휴일"
rm -f "$D/scrum-holidays.txt"
python3 -c '
import sys, datetime as dt; sys.path.insert(0, sys.argv[1]); import scrum_collect as c
assert c.prev_workday(dt.date(2026, 11, 16)) == dt.date(2026, 11, 13), "월요일 → 금요일"
assert c.prev_workday(dt.date(2026, 11, 12)) == dt.date(2026, 11, 11), "목요일 → 수요일"
' "$S" && ok "직전 평일: 월요일은 금요일부터" || ko "직전 평일"

# ---- 모델 대체 순서 ----
reset; echo fail > "$D/plan-claude-fable-5-1"; post --date $WK
[ "$(tr '\n' ' ' < "$D/claude.log")" = "claude-fable-5-1 claude-fable-5-2 " ] && grep -q "모델 claude-fable-5-2" "$D/sent.log" && ok "fable-5-1 실패 → fable-5-2 채택, 참고 줄에 모델 기록" || ko "1→2" "$(cat "$D/claude.log")"
reset; echo fail > "$D/plan-claude-fable-5-1"; echo bad > "$D/plan-claude-fable-5-2"; post --date $WK
[ "$(tr '\n' ' ' < "$D/claude.log")" = "claude-fable-5-1 claude-fable-5-2 claude-opus-5-5 " ] && grep -q "모델 claude-opus-5-5" "$D/sent.log" && ok "양식 불량도 실패로 쳐 opus 까지 내려감" || ko "1→2→3" "$(cat "$D/claude.log")"
reset; post --date $WK; [ "$(cat "$D/claude.log")" = "claude-fable-5-1" ] && ok "첫 모델이 되면 다음 모델은 부르지 않음" || ko "첫 모델" "$(cat "$D/claude.log")"
reset; echo empty > "$D/plan-m-a"; LRM_SCRUM_MODELS="m-a,m-b" post --date $WK; [ "$(tr '\n' ' ' < "$D/claude.log")" = "m-a m-b " ] && ok "모델 순서는 LRM_SCRUM_MODELS 로 바꿈" || ko "모델 목록" "$(cat "$D/claude.log")"
reset; echo extra > "$D/plan-claude-fable-5-1"; post --date $WK
f=$(ls "$D"/draft/*.md | head -1); ! grep -q "타인" "$f" && ! grep -q "머리말" "$f" && head -1 "$f" | grep -q "^# $LRM_SCRUM_NAME\$" && ok "머리말·다른 사람 칸은 잘라냄" || ko "칸 분리" "$(cat "$f")"

reset; echo noatl > "$D/plan-claude-fable-5-1"; post --date $WK; f=$(ls "$D"/draft/*.md | head -1)
grep -q "경고: Jira·Confluence 를 못 읽어" "$D/sent.log" && ! grep -q "atlassian-unavailable\|설명 문장" "$f" && ok "Atlassian 못 읽음 → Slack 경고 1줄, 표시·끝 설명 문장은 초안에서 제거" || ko "Atlassian 경고" "$(cat "$f")"
reset; post --date $WK; ! grep -q "Jira·Confluence" "$D/sent.log" && ok "Atlassian 읽었으면 경고 없음" || ko "Atlassian 정상"

# ---- 초안 claude 도구 제한(리뷰 지적 1) ----
reset; post --date $WK
args=$(sed '/^---$/,$d' "$D/claude.args"); opt() { printf '%s\n' "$args" | grep -A1 -x -- "$1" | tail -1; }
[ "$(opt --tools)" = "<빈값>" ] && ok "claude -p 에 --tools \"\" 로 내장 도구 전부 끔" || ko "--tools" "$(opt --tools)"
[ "$(opt --permission-mode)" = dontAsk ] && ok "--permission-mode dontAsk 로 목록 밖 호출 거부" || ko "dontAsk" "$(opt --permission-mode)"
allowed=$(opt --allowedTools)
[ -n "$allowed" ] && ! printf '%s' "$allowed" | tr ',' '\n' | grep -qv '^mcp__atlassian__\(get\|search\|atlassianUserInfo\)' && ok "--allowedTools 는 Atlassian 읽기 도구만" || ko "allowedTools" "$allowed"
denied=$(opt --disallowedTools)
for t in Bash Read Write Edit WebFetch mcp__atlassian__createJiraIssue mcp__atlassian__editJiraIssue mcp__atlassian__addOrEditJiraIssueComment mcp__atlassian__updateConfluenceContent mcp__atlassian__executeWrite; do
  printf '%s' "$denied" | tr ',' '\n' | grep -qx "$t" || ko "--disallowedTools" "$t 누락"
done; ok "--disallowedTools 에 Bash·파일·웹·Atlassian 쓰기 도구 명시"

# ---- 수집 오류 반영(리뷰 지적 5) ----
reset; echo err-all > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 1 ] && [ ! -e "$D/claude.log" ] && grep -q -- "--kind warn" "$D/sent.log" && grep -q "collect_required_missing" "$D/sent.log" && [ ! -d "$D/draft" ] && ok "prs·commits 수집 둘 다 실패 → 초안 없이 warn(정상 초안으로 확정 안 함)" || ko "수집 전부 실패" "rc=$rc"
python3 -c 'import json,sys;sys.exit(1 if "2026-11-12" in json.load(open(sys.argv[1]))["sent"] else 0)' "$D/scrum.state.json" && ok "수집 실패한 날은 '보냄' 기록 없음" || ko "수집 실패 기록"
! grep -q "keyring" "$D/sent.log" && ok "수집 오류 원문(keyring)은 Slack 으로 안 나감" || ko "오류 원문 노출"
reset; echo err-only > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 1 ] && grep -q "collect_all_empty" "$D/sent.log" && ok "전 출처가 비고 오류가 있으면 실패" || ko "전부 빔" "rc=$rc"
reset; echo err-prs > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 0 ] && grep -q -- "--kind doc" "$D/sent.log" && grep -q "경고: 재료 수집 누락(prs)" "$D/sent.log" && ok "일부 출처 누락 → 초안은 보내되 누락 출처를 경고 줄로 명시" || ko "일부 누락" "rc=$rc"
reset; echo badjson > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 1 ] && grep -q "collect_bad_json" "$D/sent.log" && ok "수집 출력이 JSON 이 아니면 실패" || ko "깨진 JSON" "rc=$rc"
reset; post --date $WK; ! grep -q "수집 누락" "$D/sent.log" && ok "오류 없으면 누락 경고 없음" || ko "정상 경고"

# ---- notify 시간 초과·실행 불가(리뷰 지적 6) ----
reset; echo hang > "$D/send-mode"; LRM_SCRUM_NOTIFY_TIMEOUT=1 post --date $WK; rc=$?
[ $rc = 1 ] && ! grep -q "Traceback" "$D/err" && [ -s "$D/osa.log" ] && ls "$D"/draft/2026-11-12.md > /dev/null && grep -q "시간 초과" "$D/scrum.log" && ok "notify 시간 초과 → 예외 없이 로그·로컬 알림·초안 보존, 종료 코드 1" || ko "notify 시간 초과" "rc=$rc $(tail -3 "$D/err")"
reset; LRM_NOTIFY="$D/없는-명령" post --date $WK; rc=$?
[ $rc = 1 ] && ! grep -q "Traceback" "$D/err" && [ -s "$D/osa.log" ] && ls "$D"/draft/2026-11-12.md > /dev/null && [ ! -e "$D/scrum.state.json" ] && ok "notify 실행 불가(OSError) → 예외 없이 로컬 알림, 보냄 기록 없음" || ko "notify OSError" "rc=$rc $(tail -3 "$D/err")"
reset; echo hang > "$D/send-mode"; for m in claude-fable-5-1 claude-fable-5-2 claude-opus-5-5; do echo fail > "$D/plan-$m"; done; LRM_SCRUM_NOTIFY_TIMEOUT=1 post --date $WK; rc=$?
[ $rc = 1 ] && ! grep -q "Traceback" "$D/err" && [ -s "$D/osa.log" ] && ok "warn 발송 중 시간 초과도 예외 없이 로컬 알림" || ko "warn 시간 초과" "rc=$rc"

# ---- Slack 으로 나가는 문자열 정제(리뷰 지적 4) ----
reset; echo secret > "$D/plan-claude-fable-5-1"; post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(tr '\n' ' ' < "$D/claude.log")" = "claude-fable-5-1 claude-fable-5-2 " ] && ! grep -rq "FAKE_TEST_SECRET" "$D/sent.log" "$D"/draft && ok "비밀 패턴이 든 초안은 거부 → 다음 모델 채택, 본문·첨부에 노출 없음" || ko "비밀 초안" "rc=$rc $(cat "$D/claude.log")"
reset; for m in claude-fable-5-1 claude-fable-5-2 claude-opus-5-5; do echo secret > "$D/plan-$m"; done; post --date $WK
grep -q -- "--kind warn" "$D/sent.log" && grep -q "sensitive_text" "$D/sent.log" && ! grep -q "FAKE_TEST_SECRET\|D-PRIVATE" "$D/sent.log" "$D/scrum.log" && ok "전 모델이 비밀 초안이면 분류 코드만 담은 warn, 로그에도 원문 없음" || ko "비밀 warn"
reset; echo idtopic > "$D/plan-claude-fable-5-1"; post --date $WK
body=$(sed -n '1,/^참고/p' "$D/sent.log"); ! printf '%s' "$body" | grep -q "LRM-123\|D-PRIVATE" && printf '%s' "$body" | grep -q "정리" && grep -q "LRM-123" "$D"/draft/*.md && ok "Slack 본문 주제에서 내부 번호 제거(첨부 원문은 유지)" || ko "주제 정제" "$body"
reset; for m in claude-fable-5-1 claude-fable-5-2 claude-opus-5-5; do echo authfail > "$D/plan-$m"; done; post --date $WK
grep -q -- "--kind warn" "$D/sent.log" && grep -q "exit_1" "$D/sent.log" && ! grep -q "FAKE_TEST_SECRET\|Bearer\|Authorization" "$D/sent.log" "$D/scrum.log" && ok "CLI 오류 원문(Authorization 등)은 Slack·로그에 없고 분류 코드만" || ko "오류 원문" "$(cat "$D/sent.log")"

# ---- 실패 시 warn 1건 ----
reset; for m in claude-fable-5-1 claude-fable-5-2 claude-opus-5-5; do echo fail > "$D/plan-$m"; done; post --date $WK; rc=$?
[ $rc = 1 ] && [ "$(sent)" = 1 ] && grep -q -- "--kind warn" "$D/sent.log" && grep -q "• 무엇" "$D/sent.log" && grep -q "• 왜" "$D/sent.log" && grep -q "• 결과" "$D/sent.log" && grep -q "👉 답" "$D/sent.log" && ok "전부 실패 → warn 1건(무엇·왜·결과·답 양식), 종료 코드 1" || ko "warn" "rc=$rc $(sent)"
post --date $WK; [ "$(sent)" = 1 ] && ok "같은 날 재실행해도 warn 은 1건" || ko "warn 중복" "$(sent)"
python3 -c 'import json,sys;sys.exit(1 if "2026-11-12" in json.load(open(sys.argv[1]))["sent"] else 0)' "$D/scrum.state.json" && ok "실패한 날은 '보냄' 으로 기록하지 않음(재시도 가능)" || ko "실패 기록"
rm -f "$D/plan-claude-fable-5-1"; post --date $WK; [ "$(sent)" = 2 ] && grep -q -- "--kind doc" "$D/sent.log" && ok "복구 뒤 재실행은 초안 발송" || ko "복구" "$(sent)"

# ---- 발송 경로·dry-run ----
reset; echo 2 > "$D/send-fails"; post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(cat "$D/send-calls")" = 3 ] && ok "Slack API 실패(코드 1)는 2회 재시도 뒤 성공" || ko "재시도" "rc=$rc calls=$(cat "$D/send-calls")"
reset; echo 5 > "$D/send-fails"; post --date $WK; rc=$?
[ $rc = 1 ] && [ "$(cat "$D/send-calls")" = 3 ] && [ -s "$D/osa.log" ] && ls "$D"/draft/2026-11-12.md > /dev/null && [ ! -e "$D/scrum.state.json" ] && python3 -c 'import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDWR); fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)' "$D/scrum.lock" && ok "끝내 실패 → 초안 파일 남김·로컬 알림·잠금 풀림" || ko "발송 실패" "rc=$rc"
reset; post --date $WK --dry-run; rc=$?
[ $rc = 0 ] && [ "$(sent)" = 0 ] && [ ! -e "$D/scrum.state.json" ] && grep -q "^# $LRM_SCRUM_NAME\$" "$D/out" && [ -s "$D/draft/2026-11-12.dry.md" ] && ok "--dry-run: 발송·상태 기록 없이 초안만 출력·저장" || ko "dry-run" "rc=$rc"
reset; post --date $WK; post --date $WK --dry-run; [ "$(sent)" = 1 ] && grep -q "^# $LRM_SCRUM_NAME\$" "$D/out" && ok "--dry-run 은 이미 보낸 날에도 초안 생성" || ko "dry-run 재생성"
reset; echo '{"sent":{"2026-11-09":{"at":1}}}' > "$D/scrum.state.json"; post --date $WK
grep -q "경고: 11-11 초안이 안 나갔음" "$D/sent.log" && ok "어제 건너뜀 경고 1줄" || ko "어제 건너뜀" "$(cat "$D/sent.log")"
reset; post --date $WK; ! grep -q "경고" "$D/sent.log" && ok "첫 실행(기록 없음)은 건너뜀 경고 없음" || ko "첫 실행 경고"
reset; post --date $WK; [ "$(sent)" = 1 ] && [ "$(grep -c . "$D/sent.log")" -le 12 ] && ! grep -q "LRM-1" <(sed -n 1,/참고/p "$D/sent.log" | grep -v "참고:") && ok "Slack 본문엔 티켓 번호 없음(원문은 첨부 파일)" || ko "본문 규칙"

# ---- 본인 칸 제목 ----
reset; (unset LRM_SCRUM_NAME; post --date $WK); rc=$?; [ $rc = 2 ] && grep -q "SCRUM_NAME" "$D/err" && [ "$(sent)" = 0 ] && ok "제목 없으면 종료 코드 2 + 설정 방법 안내" || ko "제목 없음" "rc=$rc"
reset; echo "SCRUM_NAME=파일이름" > "$D/scrum.env"; (unset LRM_SCRUM_NAME; FAKE_NAME=파일이름 post --date $WK); rc=$?; [ $rc = 2 ] || { head -1 "$D"/draft/*.md | grep -q "^# 파일이름$" && ok "scrum.env 의 SCRUM_NAME 사용" || ko "scrum.env" "rc=$rc"; }
rm -f "$D/scrum.env"

# ---- 인자 오류·도움말 ----
post --date 11-12; [ $? = 2 ] && grep -q "YYYY-MM-DD" "$D/err" && ok "날짜 형식 오류 → 종료 코드 2 + 조치 안내" || ko "인자 오류"
"$S/scrum_run.sh" --help > "$D/help"; [ $? = 0 ] && grep -q -- "--dry-run" "$D/help" && ok "래퍼 --help" || ko "--help"
python3 "$S/scrum_collect.py" --date 2026-13-01 > /dev/null 2> "$D/err"; [ $? = 2 ] && ok "수집기 날짜 오류 → 종료 코드 2" || ko "수집기 오류"

echo "---- PASS $PASS / FAIL $FAIL"
[ "$FAIL" = 0 ]
