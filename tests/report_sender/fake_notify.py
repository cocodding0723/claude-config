#!/usr/bin/env python3
"""slack_notify.sh 대역. 실제 Slack·토큰에 접속하지 않음. 인자를 calls.jsonl 에 남기고 'ok <ts>' 를 출력.
시나리오는 $FAKE_DIR 의 파일: fail.n(남은 실패 횟수, 호출마다 1 감소) · fail.all(있으면 항상 실패) · fail.rc(실패 종료 코드, 기본 1) · fail.msg(실패 stderr 문구) · sleep(초)"""
import json
import os
import sys
import time

SECRET_ENV = ("SLACK_BOT_TOKEN", "SLACK_USER_ID", "SLACK_APP_TOKEN", "SLACK_SIGNING_SECRET")  # 로컬 SLACK_API_BASE 같은 설정값은 세지 않음
d = os.environ["FAKE_DIR"]
rd = lambda n: open(os.path.join(d, n)).read().strip() if os.path.exists(os.path.join(d, n)) else ""  # noqa: E731
if rd("sleep"):
    time.sleep(float(rd("sleep")))
with open(os.path.join(d, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": sys.argv[1:], "slack_env": sum(k in SECRET_ENV for k in os.environ)}, ensure_ascii=False) + "\n")
left = int(rd("fail.n") or 0)
if rd("fail.all") or left > 0:
    if left > 0:
        open(os.path.join(d, "fail.n"), "w").write(str(left - 1))
    sys.stderr.write((rd("fail.msg") or "fake: slack 전송 실패") + "\n")
    sys.exit(int(rd("fail.rc") or 1))
n = len(open(os.path.join(d, "calls.jsonl")).read().splitlines())
ts = f"1700000000.{n:06d}"
with open(os.path.join(d, "sent.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"channel": "D1", "ts": ts}) + "\n")
print("ok", ts)
