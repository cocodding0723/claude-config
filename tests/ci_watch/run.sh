#!/usr/bin/env bash
# ci_watch.py·bot_alive.py 시험. 가짜 gh·알림과 임시 폴더만 씀(네트워크·Slack 없음)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P); S="$HERE/../../hubs/scripts"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); echo "PASS  $1"; }
ko() { FAIL=$((FAIL + 1)); echo "FAIL  $1 — ${2:-}"; }
D=$(mktemp -d "${TMPDIR:-/tmp}/cc-ciwatch.XXXXXX"); trap 'rm -rf "$D"' EXIT
export FAKE_DIR="$D" LRM_HUB_DIR="$D" LRM_GH="$HERE/fake_gh.sh" LRM_NOTIFY="$HERE/fake_send.sh"
chmod +x "$HERE"/fake_*.sh
echo '[{"number":679,"title":"[LRM-474] fix: 게이트"}]' > "$D/prs.json"
chk() { printf '[{"name":"Run Unity validation / Unity validation","workflow":"PR Test Pipeline","bucket":"%s","link":"https://x/actions/runs/11/job/22"},{"name":"AI Review Gate (%s/5)","workflow":"Preview Stack Events","bucket":"%s","link":""},{"name":"mention-author","workflow":"PR Author Mention","bucket":"fail","link":""}]' "$1" "$2" "$3" > "$D/checks-679.json"; }
printf 'Unity validation\t2026-11-11T05:00:00.1Z ##[group]x\nUnity validation\t2026-11-11T05:00:01.2Z 시트 스냅숏이 없음: Locale.csv\n' > "$D/log.txt"
sent() { [ -f "$D/sent.log" ] && grep -c '^===$' "$D/sent.log" || echo 0; }
run() { python3 "$S/ci_watch.py" "$@" > "$D/out" 2>&1; }

chk pending 3 pending; run; [ "$(sent)" = 0 ] && ok "첫 실행은 기준만 기록, 전송 0" || ko "첫 실행" "$(sent)"
chk fail 5 pass; run; [ "$(sent)" = 1 ] && ok "결론 변화 → 1건 전송" || ko "변화 전송" "$(sent)"
grep -q -- "--kind warn" "$D/sent.log" && grep -q "시트 스냅숏이 없음: Locale.csv" "$D/sent.log" && ok "실패면 warn + 원인 한 줄" || ko "원인 줄" "$(cat "$D/sent.log")"
! grep -q "AI Review Gate" "$D/sent.log" && ! grep -q "mention-author" "$D/sent.log" && ok "실패한 감시 대상만 보냄" || ko "감시 대상"
! grep -q "LRM-474" "$D/sent.log" && ok "티켓 번호 본문 제외" || ko "티켓"
run; [ "$(sent)" = 1 ] && ok "같은 결론 재전송 없음" || ko "중복 방지" "$(sent)"
chk pending 5 pass; run; [ "$(sent)" = 1 ] && ok "재실행 진행 중은 무음" || ko "진행 무음" "$(sent)"
chk pass 5 pass; run; [ "$(sent)" = 2 ] && tail -5 "$D/sent.log" | grep -q -- "--kind done" && grep -q "참고: PR #679" "$D/sent.log" && ok "전부 통과 → done 1건(PR 번호는 참고 줄)" || ko "통과 알림" "$(sent)"
run; [ "$(sent)" = 2 ] && ok "통과 재전송 없음" || ko "통과 중복" "$(sent)"
chk fail 5 pass; LRM_NOTIFY=/usr/bin/false run; chk fail 5 pass; run; [ "$(sent)" = 3 ] && ok "전송 실패 시 다음 주기 재시도" || ko "재시도" "$(sent)"
echo 680 > "$D/ci-watch.txt"; echo '[]' > "$D/checks-680.json"; run; grep -q "감시 2건" "$D/out" && ok "ci-watch.txt PR 목록 사용" || ko "목록 파일" "$(cat "$D/out")"
rm "$D/ci-watch.txt"

# 봇 생존
now() { date '+%m-%d %H:%M:%S'; }
old() { date -v-20M '+%m-%d %H:%M:%S'; }
echo "$(now) [socket] 생존 연결=예 마지막이벤트=3분 전 재연결=0회" > "$D/slack-socket.log"
out=$(python3 "$S/bot_alive.py"); [ "$out" = "봇: 연결됨 · 마지막 수신 3분 전" ] && ok "생존 줄" || ko "생존 줄" "$out"
: > "$D/sent.log"
echo "$(old) [socket] 생존 연결=예 마지막이벤트=3분 전 재연결=0회" > "$D/slack-socket.log"
python3 "$S/bot_alive.py" --warn-once; python3 "$S/bot_alive.py" --warn-once
[ "$(sent)" = 1 ] && grep -q -- "--kind warn" "$D/sent.log" && ok "끊김 warn 1회만" || ko "끊김 1회" "$(sent)"
echo "$(now) [socket] 생존 연결=예 마지막이벤트=1분 전 재연결=0회" >> "$D/slack-socket.log"
python3 "$S/bot_alive.py" --warn-once; [ ! -e "$D/bot-alive.warned" ] && ok "복구 시 표시 초기화" || ko "초기화"
echo "통과 $PASS / 실패 $FAIL"; [ "$FAIL" = 0 ]
