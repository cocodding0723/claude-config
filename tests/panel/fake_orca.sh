#!/bin/bash
# 가짜 orca. FAKE_ORCA_DIR 의 JSON 을 그대로 돌려줌. 호출 인자와 Slack 토큰 상속 여부만 기록
D="${FAKE_ORCA_DIR:?}"
echo "$* tok=${SLACK_BOT_TOKEN:-none}" >> "$D/orca.calls"
[ -n "${FAKE_ORCA_SLEEP:-}" ] && sleep "$FAKE_ORCA_SLEEP"
case "$*" in
  "terminal list --json") f=terms ;;
  "orchestration worker-list --full --json") f=workers ;;
  "orchestration task-list --run "*" --json") f=tasks ;;
  *) echo '{"ok":false}'; exit 1 ;;
esac
case " ${FAKE_ORCA_FAIL:-} " in *" $f "*) echo "fake failure" >&2; exit 1 ;; esac
cat "$D/$f.json"
