#!/bin/bash
# 가짜 알림. 인자를 FAKE_DIR/sent.log 에 한 건씩 기록. FAKE_SEND_FAILS 번까지는 종료 코드 1(API 실패)
printf '%s\n===\n' "$*" >> "${FAKE_DIR:?}/sent.log"
n=$(cat "$FAKE_DIR/send-calls" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE_DIR/send-calls"
# send-mode: hang(응답 없이 멈춤) | record-hang(Slack 에는 올리고 기록한 뒤 멈춤) | silent1(오류 코드 없이 종료 코드 1) | record-silent1(올린 뒤 기록하고 종료 코드 1)
mode=$(cat "$FAKE_DIR/send-mode" 2>/dev/null)
case "$mode" in record-*) printf '{"at":%s,"kind":"file","path":"%s","file_id":"F_FAKE_%s"}\n' "$(date +%s)" "${!#}" "$n" >> "$FAKE_DIR/slack-sent.jsonl" ;; esac
case "$mode" in hang|record-hang) exec sleep 5 ;; silent1|record-silent1) exit 1 ;; esac
# FAKE_SEND_FAILS 번까지는 Slack 이 거절한 것처럼 오류 코드를 내고 종료 코드 1
[ "$n" -le "$(cat "$FAKE_DIR/send-fails" 2>/dev/null || echo 0)" ] && { echo ratelimited; exit 1; }
echo "ok 1700000000.000100"
