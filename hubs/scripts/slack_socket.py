#!/usr/bin/env python3
"""Slack Socket Mode 수신기: 봇 DM 메시지·반응 이벤트가 오면 slack_poll.sh --once 를 바로 실행.

수집·중복 방지·inbox 기록은 slack_poll.sh 가 그대로 맡음. 이 스크립트는 '언제 돌릴지'만 바꿈.
수신 본문은 데이터로만 다루고 실행·eval 하지 않음. 토큰은 slack-bot.env 에서 읽고 출력하지 않음.

사용: slack_socket.py            (launchd ai.lrm.slacksocket 가 상주 실행)
환경: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs)
종료 코드: 0 정상 종료, 2 설정 오류(토큰 없음)·연결 실패
"""
import os
import subprocess
import sys
import threading
import time

os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/cert.pem")

from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

DIR = os.environ.get("LRM_HUB_DIR", os.path.expanduser("~/.config/lrm-hubs"))
POLL = os.path.join(DIR, "slack_poll.sh")
DEBOUNCE_S = 2.0  # 연속 이벤트(메시지+반응)를 한 번의 수집으로 묶음


def load_env(path: str) -> dict:
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.removeprefix("export ").strip()
            values[key] = value.strip().strip('"').strip("'")
    return values


def log(msg: str) -> None:
    print(f"{time.strftime('%m-%d %H:%M:%S')} [socket] {msg}", flush=True)


class Trigger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def fire(self, reason: str) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_S, self._run, args=(reason,))
            self._timer.daemon = True
            self._timer.start()

    def _run(self, reason: str) -> None:
        try:
            out = subprocess.run(
                ["/bin/bash", POLL, "--once", "--json"],
                capture_output=True, text=True, timeout=300,
            )
            log(f"{reason} → 수집 rc={out.returncode} {out.stdout.strip()[:120]}")
        except Exception as err:  # 수집 실패는 다음 이벤트·launchd 주기 수집이 메움
            log(f"{reason} → 수집 실패 {type(err).__name__}")


def main() -> int:
    env = load_env(os.path.join(DIR, "slack-bot.env"))
    app_token, bot_token = env.get("SLACK_APP_TOKEN", ""), env.get("SLACK_BOT_TOKEN", "")
    me = env.get("SLACK_USER_ID", "")
    if not app_token.startswith("xapp-") or not bot_token:
        log("SLACK_APP_TOKEN(xapp-) 또는 SLACK_BOT_TOKEN 없음 — slack-bot.env 확인")
        return 2

    trigger = Trigger()
    client = SocketModeClient(app_token=app_token)

    def on_request(c: SocketModeClient, req: SocketModeRequest) -> None:
        c.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type != "events_api":
            return
        event = (req.payload or {}).get("event", {})
        kind = event.get("type")
        # 사용자 본인이 보낸 DM·반응만. 봇 자신의 메시지는 무시
        if event.get("bot_id") or (me and event.get("user") not in (me, None)):
            return
        if kind == "message" and event.get("channel_type") == "im":
            trigger.fire("DM")
        elif kind == "reaction_added":
            trigger.fire("반응")

    client.socket_mode_request_listeners.append(on_request)
    try:
        client.connect()
    except Exception as err:  # 토큰 오류·네트워크 실패는 재시작해도 같음. traceback 대신 한 줄만 남김
        log(f"연결 실패 {type(err).__name__} — SLACK_APP_TOKEN·네트워크 확인")
        return 2
    log("연결됨")
    trigger.fire("시작")  # 끊긴 동안 쌓인 것 한 번 수집
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
