#!/bin/bash
# 가짜 orca. FAKE_ORCA_DIR 의 JSON 을 그대로 돌려줌. 호출 인자와 Slack 토큰 상속 여부만 기록
# worker-list 는 실제처럼 쪽으로 나눔: 첫 쪽 workers.json, --cursor <c> 면 workers_<c>.json
# FAKE_ORCA_FAIL="terms workers_c2 …" 그 파일 실패, FAKE_ORCA_ALT="terms tasks …" 그 파일 대신 <이름>_alt.json
D="${FAKE_ORCA_DIR:?}"
echo "$* tok=${SLACK_BOT_TOKEN:-none}" >> "$D/orca.calls"
[ -n "${FAKE_ORCA_SLEEP:-}" ] && sleep "$FAKE_ORCA_SLEEP"
# --limit 없는 호출도 실제처럼 받음(터미널은 전부, worker-list 는 첫 쪽만)
case "$*" in
  "terminal list --limit 1000 --json"|"terminal list --json") f=terms ;;
  "orchestration worker-list --full --limit 100 --json"|"orchestration worker-list --full --json") f=workers ;;
  "orchestration worker-list --full --limit 100 --cursor "*" --json") a="$*"; c="${a#*--cursor }"; f="workers_${c% --json}" ;;
  "orchestration task-list --run "*" --json") f=tasks ;;
  "worktree list --limit 1000 --json") f=worktrees ;;
  "terminal read --terminal "*" --screen --limit 40 --json") [ -f "$D/screen.json" ] && cat "$D/screen.json" || echo '{"ok":true,"result":{"terminal":{"tail":[]}}}'; exit 0 ;;
  *) echo '{"ok":false}'; exit 1 ;;
esac
case " ${FAKE_ORCA_FAIL:-} " in *" $f "*) echo "fake failure" >&2; exit 1 ;; esac
# 끝없는 커서: loopN 은 빈 쪽 + 다음 커서 loopN+1(쪽 수 상한 시험)
case "$f" in workers_loop*) n="${f#workers_loop}"
  printf '{"ok":true,"result":{"workers":[],"page":{"limit":100,"total":5,"hasMore":true,"nextCursor":"loop%d"}}}\n' $((n + 1)); exit 0 ;; esac
case " ${FAKE_ORCA_ALT:-} " in *" $f "*) f="${f}_alt" ;; esac
[ -f "$D/$f.json" ] || { echo '{"ok":false}'; exit 1; }
cat "$D/$f.json"
